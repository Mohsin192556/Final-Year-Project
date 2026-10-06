from fastapi.testclient import TestClient

import app.library as library
import app.main as main
from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_requires_google_api_key(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    response = client.post("/api/chat", json={"question": "What is the law?"})
    assert response.status_code == 503
    assert "GOOGLE_API_KEY" in response.json()["detail"]


def test_chat_rejects_empty_question() -> None:
    response = client.post("/api/chat", json={"question": "  "})
    assert response.status_code == 422


def test_chat_requires_an_indexed_knowledge_base(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_PERSIST_DIRECTORY", str(tmp_path))
    response = client.post("/api/chat", json={"question": "What does the law say?"})
    assert response.status_code == 503
    assert "knowledge base is not ready" in response.json()["detail"]


def test_document_library_requires_admin_key(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "this-is-a-long-enough-test-admin-key")
    response = client.get("/api/documents")
    assert response.status_code == 401


def test_document_upload_and_review_flow(monkeypatch, tmp_path) -> None:
    documents_dir = tmp_path / "documents"
    monkeypatch.setattr(main, "DOCUMENTS_DIR", documents_dir)
    monkeypatch.setattr(library, "DOCUMENTS_DIR", documents_dir)
    monkeypatch.setattr(library, "SOURCES_FILE", documents_dir / "sources.json")
    monkeypatch.setenv("ADMIN_API_KEY", "this-is-a-long-enough-test-admin-key")

    class FakePage:
        def extract_text(self, extraction_mode: str = "plain") -> str:
            return "Section 1. This is a test legal provision."

    class FakePdfReader:
        def __init__(self, _path: str) -> None:
            self.is_encrypted = False
            self.pages = [FakePage()]

    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", FakePdfReader)
    headers = {"Authorization": "Bearer this-is-a-long-enough-test-admin-key"}
    response = client.post(
        "/api/documents/upload",
        headers=headers,
        data={
            "title": "Test Act",
            "citation": "Test Act, 2026",
            "jurisdiction": "Pakistan",
            "source_url": "https://example.gov.pk/test-act",
            "verified": "false",
        },
        files={"file": ("test.pdf", b"%PDF-1.7 test", "application/pdf")},
    )
    assert response.status_code == 200
    document_id = response.json()["id"]
    assert response.json()["status"] == "needs_review"
    assert response.json()["quality"]["page_count"] == 1
    assert response.json()["quality"]["extracted_page_count"] == 1

    response = client.get("/api/documents", headers=headers)
    assert response.status_code == 200
    assert response.json()["documents"][0]["status"] == "needs_review"

    response = client.get(
        f"/api/documents/{document_id}/preview?page=1",
        headers=headers,
    )
    assert response.status_code == 200
    assert "test legal provision" in response.json()["text"]
    assert response.json()["page_number"] == 1

    response = client.post(
        f"/api/documents/{document_id}/verification",
        headers=headers,
        json={"verified": True},
    )
    assert response.status_code == 200
    assert response.json()["verified"] is True


def test_normalize_page_text_removes_running_header_and_preserves_structure() -> None:
    text = (
        "       CONSTITUTION OF PAKISTAN\n"
        "             14\n"
        "\n"
        "  1.    The Republic and its territories\n"
        "\n"
        " 11. (1)  Pakistan shall be a Federal Republic.\n"
    )
    normalized = library.normalize_page_text(text)
    assert "CONSTITUTION OF PAKISTAN" not in normalized
    assert normalized == (
        "1. The Republic and its territories\n\n"
        "1. (1) Pakistan shall be a Federal Republic."
    )


def test_parse_excluded_pages_supports_ranges_and_reports_invalid_input() -> None:
    assert main.parse_excluded_pages("1-3, 7, 9-10") == [1, 2, 3, 7, 9, 10]
    try:
        main.parse_excluded_pages("3-1")
    except Exception as error:
        assert getattr(error, "status_code", None) == 422
    else:
        raise AssertionError("Descending page ranges should be rejected.")


def test_index_reports_gemini_quota_exhaustion(monkeypatch) -> None:
    admin_key = "this-is-a-long-enough-test-admin-key"
    monkeypatch.setenv("ADMIN_API_KEY", admin_key)

    def fail_index() -> None:
        raise library.EmbeddingQuotaExceeded("Gemini embedding quota reached.")

    monkeypatch.setattr(main, "build_index", fail_index)
    response = client.post(
        "/api/documents/index",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert response.status_code == 429
    assert "Gemini embedding quota" in response.json()["detail"]
