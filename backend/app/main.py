import logging
import os
import re
import secrets
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware

from app.library import (
    active_index_manifest,
    build_index,
    complete_document_upload,
    create_document_upload,
    list_documents,
    preview_document,
    verify_document,
)
from app.schemas import (
    ChatRequest,
    ChatResponse,
    DocumentUploadIntent,
    VerificationRequest,
)
from app.supabase_store import SupabaseConfigurationError

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
library_lock = threading.Lock()
MAX_PAGE_NUMBER = 1000

app = FastAPI(
    title="Pakistan Law Assistant API",
    description="A source-grounded legal information assistant for Pakistan.",
    version="0.1.0",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


DISCLAIMER = (
    "This is general legal information, not legal advice or representation. "
    "Laws and procedures may change. Consult a qualified Pakistani lawyer "
    "before acting, especially for urgent or high-stakes matters."
)


def parse_excluded_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for segment in value.split(","):
        segment = segment.strip()
        if not segment:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", segment)
        if not match:
            raise HTTPException(
                status_code=422,
                detail="Pages to skip must be numbers or ranges, e.g. 1-8, 12.",
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > MAX_PAGE_NUMBER:
            raise HTTPException(
                status_code=422,
                detail=f"Skipped page numbers must be between 1 and {MAX_PAGE_NUMBER}.",
            )
        pages.update(range(start, end + 1))
    return sorted(pages)


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected_key = os.getenv("ADMIN_API_KEY", "")
    if len(expected_key) < 24:
        raise HTTPException(
            status_code=503,
            detail="Document library is disabled. Configure ADMIN_API_KEY with at least 24 characters.",
        )
    scheme, _, supplied_key = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        supplied_key, expected_key
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid document-library admin key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="The AI service is not configured. Set GOOGLE_API_KEY on the backend.",
        )

    try:
        from app.rag import GeminiQuotaExceeded, answer_question

        answer, sources = answer_question(
            question=request.question.strip(),
            history=[item.model_dump() for item in request.history[-8:]],
        )
    except SupabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The legal knowledge base is not ready. Add verified source "
                "documents and run the ingestion command."
            ),
        ) from exc
    except GeminiQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("The legal assistant failed to answer a question.")
        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete this request. Please try again.",
        ) from exc

    return ChatResponse(answer=answer, sources=sources, disclaimer=DISCLAIMER)


@app.get("/api/health/knowledge-base")
def knowledge_base_health() -> dict[str, str]:
    try:
        manifest = active_index_manifest()
    except SupabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not check legal knowledge-base status.")
        raise HTTPException(
            status_code=503, detail="Could not check the legal knowledge base."
        ) from exc
    if manifest is None:
        return {"status": "not_indexed"}
    return {"status": "indexed"}


@app.get("/api/documents", dependencies=[Depends(require_admin)])
def get_documents() -> dict[str, object]:
    try:
        return {"documents": list_documents()}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not load the document library.")
        raise HTTPException(
            status_code=502, detail="The document library is unavailable."
        ) from exc


@app.post(
    "/api/documents/upload-intent",
    dependencies=[Depends(require_admin)],
)
def document_upload_intent(request: DocumentUploadIntent) -> dict[str, object]:
    if Path(request.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF file.")
    title = request.title.strip()
    citation = request.citation.strip()
    jurisdiction = request.jurisdiction.strip()
    if not title or not citation or not jurisdiction:
        raise HTTPException(
            status_code=422,
            detail="Title, citation, and jurisdiction are required.",
        )
    pages_to_exclude = parse_excluded_pages(request.excluded_pages)
    try:
        return create_document_upload(
            title=title,
            citation=citation,
            jurisdiction=jurisdiction,
            source_url=str(request.source_url),
            original_filename=request.filename,
            verified=request.verified,
            excluded_pages=pages_to_exclude,
        )
    except Exception as exc:
        logger.exception("Could not prepare a signed PDF upload.")
        raise HTTPException(
            status_code=502, detail="Could not prepare a secure PDF upload."
        ) from exc


@app.post(
    "/api/documents/{document_id}/complete-upload",
    dependencies=[Depends(require_admin)],
)
def finish_document_upload(document_id: str) -> dict[str, object]:
    try:
        with library_lock:
            return complete_document_upload(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF upload validation failed.")
        raise HTTPException(
            status_code=502,
            detail="Could not validate the uploaded PDF. Check backend logs.",
        ) from exc


@app.post(
    "/api/documents/{document_id}/verification",
    dependencies=[Depends(require_admin)],
)
def update_document_verification(
    document_id: str,
    request: VerificationRequest,
) -> dict[str, object]:
    try:
        with library_lock:
            return verify_document(document_id, request.verified)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not change document verification.")
        raise HTTPException(
            status_code=502, detail="Could not update document verification."
        ) from exc


@app.get(
    "/api/documents/{document_id}/preview",
    dependencies=[Depends(require_admin)],
)
def get_document_preview(
    document_id: str,
    page: int = 1,
) -> dict[str, object]:
    try:
        with library_lock:
            return preview_document(document_id, page)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not preview the legal document.")
        raise HTTPException(
            status_code=502, detail="Could not load the document preview."
        ) from exc


@app.post("/api/documents/index", dependencies=[Depends(require_admin)])
def index_documents() -> dict[str, object]:
    try:
        with library_lock:
            manifest = build_index()
        return {
            "status": "indexed",
            "document_count": manifest["document_count"],
            "chunk_count": manifest["chunk_count"],
            "indexed_page_count": manifest["indexed_page_count"],
            "indexed_at": manifest["indexed_at"],
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document indexing failed.")
        raise HTTPException(
            status_code=502,
            detail="Indexing failed. Check the backend logs and Supabase configuration.",
        ) from exc
