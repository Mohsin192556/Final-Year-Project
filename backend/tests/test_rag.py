from types import SimpleNamespace

from langchain_core.documents import Document
from fastapi.testclient import TestClient

import app.rag as rag
from app.main import app
from app.rag import _response_text, _retrieve_relevant_documents, _source_excerpt


class FakeVectorStore:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    def get(self, include: list[str]) -> dict[str, list[object]]:
        assert include == ["documents", "metadatas"]
        return {
            "documents": [document.page_content for document in self.documents],
            "metadatas": [document.metadata for document in self.documents],
        }


def test_retrieval_ranks_the_requested_constitution_article_first() -> None:
    store = FakeVectorStore(
        [
            Document(
                page_content=(
                    "19. Freedom of speech, etc.\n\n"
                    "19. Every citizen shall have the right to freedom of speech "
                    "and expression, and there shall be freedom of the press."
                ),
                metadata={"page": 23},
            ),
            Document(
                page_content=(
                    "235. Proclamation in case of financial emergency. "
                    "The President may make a declaration."
                ),
                metadata={"page": 154},
            ),
            Document(
                page_content=(
                    "66. Privileges of members. There shall be freedom of speech "
                    "in Majlis-e-Shoora (Parliament)."
                ),
                metadata={"page": 50},
            ),
            Document(
                page_content="19. Port quarantine, seamen's and marine hospitals.",
                metadata={"page": 216},
            ),
        ]
    )

    results = _retrieve_relevant_documents(
        store, "What does Article 19 of the Constitution say about freedom of speech?"
    )

    assert results[0].metadata["page"] == 23
    assert "Every citizen" in results[0].page_content
    assert all(result.metadata["page"] != 154 for result in results)
    assert all(result.metadata["page"] != 216 for result in results)


def test_retrieval_uses_supabase_full_text_search_before_bm25() -> None:
    class SupabaseIndex:
        def __init__(self) -> None:
            self.search_args = None

        def search(self, query_terms: str, target_article: str, limit: int):
            self.search_args = (query_terms, target_article, limit)
            return [
                {
                    "content": "19. Freedom of speech, etc.\n\n"
                    "19. Every citizen has freedom of speech.",
                    "metadata": {"page": 23},
                }
            ]

    index = SupabaseIndex()
    results = _retrieve_relevant_documents(
        index, "What does Article 19 say about freedom of speech?"
    )

    assert index.search_args == ("freedom | speech", "19", 200)
    assert results[0].metadata["page"] == 23


def test_gemini_content_blocks_are_converted_to_plain_text() -> None:
    content = [
        {"type": "text", "text": "Article 19 protects freedom of speech."},
        {"type": "text", "text": "It is subject to lawful restrictions."},
        {"type": "metadata", "signature": "provider-internal-value"},
    ]

    assert _response_text(content) == (
        "Article 19 protects freedom of speech.\n"
        "It is subject to lawful restrictions."
    )


def test_source_excerpt_shows_only_the_requested_article() -> None:
    document = Document(
        page_content=(
            "18. Freedom of trade.\n\n"
            "18. Trade text.\n\n"
            "19. Freedom of speech, etc.\n\n"
            "19. Every citizen has freedom of speech and expression.\n\n"
            "19A. Right to information.\n\n"
            "20. Freedom of religion."
        )
    )

    excerpt = _source_excerpt(
        document, "What does Article 19 say about freedom of speech?"
    )

    assert excerpt.startswith("19. Freedom of speech, etc.")
    assert "Every citizen has freedom of speech and expression" in excerpt
    assert "18. Freedom of trade" not in excerpt
    assert "19A. Right to information" not in excerpt
    assert "20. Freedom of religion" not in excerpt


def test_gemini_response_without_text_is_not_stringified() -> None:
    try:
        _response_text([{"type": "metadata", "signature": "provider-internal-value"}])
    except TypeError as error:
        assert "no readable text" in str(error)
    else:
        raise AssertionError("Non-text model output must not be shown as an answer.")


def test_chat_api_returns_clean_answer_from_gemini_content_blocks(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(
        rag,
        "_load_index",
        lambda: (
            FakeVectorStore(
                [
                    Document(
                        page_content=(
                            "19. Freedom of speech, etc. Every citizen shall have "
                            "the right to freedom of speech and expression."
                        ),
                        metadata={
                            "title": "Constitution of Pakistan",
                            "citation": "Article 19, PDF p. 23",
                            "jurisdiction": "Pakistan",
                            "source_url": "https://example.gov.pk/constitution",
                            "page": 23,
                        },
                    )
                ]
            ),
            {},
        ),
    )

    model_options: dict[str, object] = {}

    class FakeChatModel:
        def __init__(self, **_kwargs: object) -> None:
            model_options.update(_kwargs)

        def invoke(self, _messages: list[object]) -> SimpleNamespace:
            self.messages = _messages
            return SimpleNamespace(
                content=[
                    {"type": "text", "text": "Article 19 protects speech [S1]."},
                    {"type": "metadata", "signature": "not user-facing"},
                ]
            )

    monkeypatch.setattr(rag, "ChatGoogleGenerativeAI", FakeChatModel)
    response = TestClient(app).post(
        "/api/chat",
        json={"question": "What does Article 19 say about freedom of speech?"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "Article 19 protects speech [S1]."
    assert response.json()["sources"][0]["citation"] == "Article 19, PDF p. 23"
    assert model_options["max_output_tokens"] == 2048
    assert model_options["max_retries"] == 0


def test_chat_api_surfaces_gemini_quota_errors_without_retrying(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setattr(
        rag,
        "_load_index",
        lambda: (
            FakeVectorStore(
                [
                    Document(
                        page_content="19. Freedom of speech, etc.",
                        metadata={"page": 23},
                    )
                ]
            ),
            {},
        ),
    )

    class QuotaLimitedChatModel:
        def __init__(self, **_kwargs: object) -> None:
            self.options = _kwargs

        def invoke(self, _messages: list[object]) -> SimpleNamespace:
            self.messages = _messages
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                "generativelanguage.googleapis.com/"
                "generate_content_free_tier_requests, limit: 5, "
                "model: gemini-3.7-flash. quota_id: "
                '"GenerateRequestsPerMinutePerProjectPerModel-FreeTier". '
                "Please retry in 17.4s."
            )

    monkeypatch.setattr(rag, "ChatGoogleGenerativeAI", QuotaLimitedChatModel)
    response = TestClient(app).post(
        "/api/chat",
        json={"question": "What does Article 19 say about freedom of speech?"},
    )

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert "gemini-3.7-flash" in detail
    assert "5 generate-content requests per minute" in detail
    assert "18 seconds" in detail
