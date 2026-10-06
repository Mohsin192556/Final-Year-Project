from fastapi.testclient import TestClient

import app.library as library
import app.main as main
import app.rag as rag
from app.main import app

client = TestClient(app)


class FakeResponse:
    def __init__(self, data) -> None:
        self.data = data


class FakeQuery:
    def __init__(self, client, table: str) -> None:
        self.client = client
        self.table = table
        self.operation = "select"
        self.values = None
        self.filters = []
        self.return_rows = False

    def select(self, _columns: str = "*"):
        self.return_rows = True
        if self.operation == "select":
            return self
        return self

    def insert(self, values):
        self.operation = "insert"
        self.values = values
        return self

    def update(self, values):
        self.operation = "update"
        self.values = values
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, column: str, value):
        self.filters.append((column, value))
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        matches = [
            row
            for row in rows
            if all(row.get(column) == value for column, value in self.filters)
        ]
        if self.operation == "insert":
            inserted = self.values if isinstance(self.values, list) else [self.values]
            rows.extend(dict(row) for row in inserted)
            return FakeResponse(inserted)
        if self.operation == "update":
            for row in matches:
                row.update(self.values)
            return FakeResponse([dict(row) for row in matches] if self.return_rows else [])
        if self.operation == "delete":
            self.client.tables[self.table] = [
                row for row in rows if row not in matches
            ]
            return FakeResponse(matches)
        return FakeResponse([dict(row) for row in matches])


class FakeStorageBucket:
    def __init__(self, client) -> None:
        self.client = client

    def create_signed_upload_url(self, _path: str) -> dict[str, str]:
        return {"token": "short-lived-upload-token"}

    def download(self, path: str) -> bytes:
        return self.client.files[path]

    def remove(self, paths: list[str]) -> None:
        for path in paths:
            self.client.files.pop(path, None)


class FakeStorage:
    def __init__(self, client) -> None:
        self.client = client

    def from_(self, _bucket: str) -> FakeStorageBucket:
        return FakeStorageBucket(self.client)


class FakeRpc:
    def __init__(self, client, name: str, params: dict[str, object]) -> None:
        self.client = client
        self.name = name
        self.params = params

    def execute(self) -> FakeResponse:
        if self.name == "activate_legal_index":
            self.client.tables["legal_index_state"] = [
                {
                    "id": True,
                    "active_version": self.params["p_active_version"],
                    "fingerprints": self.params["p_fingerprints"],
                    "document_count": self.params["p_document_count"],
                    "indexed_page_count": self.params["p_indexed_page_count"],
                    "chunk_count": self.params["p_chunk_count"],
                    "indexed_at": self.params["p_indexed_at"],
                }
            ]
        return FakeResponse([])


class FakeSupabase:
    def __init__(self) -> None:
        self.tables = {}
        self.files = {}
        self.storage = FakeStorage(self)

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict[str, object]) -> FakeRpc:
        return FakeRpc(self, name, params)


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


def test_chat_requires_an_indexed_knowledge_base(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        rag,
        "_load_index",
        lambda: (_ for _ in ()).throw(FileNotFoundError("No active index.")),
    )
    response = client.post("/api/chat", json={"question": "What does the law say?"})
    assert response.status_code == 503
    assert "knowledge base is not ready" in response.json()["detail"]


def test_document_library_requires_admin_key(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "this-is-a-long-enough-test-admin-key")
    response = client.get("/api/documents")
    assert response.status_code == 401


def test_document_upload_and_review_flow(monkeypatch) -> None:
    fake_supabase = FakeSupabase()
    monkeypatch.setattr(library, "supabase_client", lambda: fake_supabase)
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
        "/api/documents/upload-intent",
        headers=headers,
        json={
            "filename": "test.pdf",
            "title": "Test Act",
            "citation": "Test Act, 2026",
            "jurisdiction": "Pakistan",
            "source_url": "https://example.gov.pk/test-act",
            "verified": False,
        },
    )
    assert response.status_code == 200
    intent = response.json()
    document_id = intent["id"]
    assert intent["upload_token"] == "short-lived-upload-token"
    fake_supabase.files[intent["storage_path"]] = b"%PDF-1.7 test"

    response = client.post(
        f"/api/documents/{document_id}/complete-upload",
        headers=headers,
    )
    assert response.status_code == 200
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

    response = client.post("/api/documents/index", headers=headers)
    assert response.status_code == 200
    assert response.json()["document_count"] == 1
    assert response.json()["chunk_count"] == 1

    response = client.get("/api/health/knowledge-base")
    assert response.status_code == 200
    assert response.json() == {"status": "indexed"}

    response = client.get("/api/documents", headers=headers)
    assert response.json()["documents"][0]["status"] == "indexed"


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


def test_index_reports_invalid_library_state(monkeypatch) -> None:
    admin_key = "this-is-a-long-enough-test-admin-key"
    monkeypatch.setenv("ADMIN_API_KEY", admin_key)

    def fail_index() -> None:
        raise ValueError("No reviewed PDFs with extractable text are ready to index.")

    monkeypatch.setattr(main, "build_index", fail_index)
    response = client.post(
        "/api/documents/index",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert response.status_code == 422
    assert "No reviewed PDFs" in response.json()["detail"]
