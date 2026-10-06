import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware

from app.library import (
    DOCUMENTS_DIR,
    EmbeddingQuotaExceeded,
    analyze_pdf,
    build_index,
    list_documents,
    load_sources,
    preview_document,
    save_sources,
    storage_path,
    verify_document,
)
from app.schemas import ChatRequest, ChatResponse, VerificationRequest

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
library_lock = threading.Lock()
MAX_PDF_BYTES = 30 * 1024 * 1024
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
    manifest = storage_path() / "active-index.json"
    if not manifest.is_file():
        return {"status": "not_indexed"}
    return {"status": "indexed"}


@app.get("/api/documents", dependencies=[Depends(require_admin)])
def get_documents() -> dict[str, object]:
    try:
        return {"documents": list_documents()}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/documents/upload", dependencies=[Depends(require_admin)])
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(min_length=2, max_length=200),
    citation: str = Form(min_length=2, max_length=300),
    jurisdiction: str = Form(min_length=2, max_length=120),
    source_url: str = Form(min_length=8, max_length=2000),
    verified: bool = Form(default=False),
    excluded_pages: str = Form(default="", max_length=500),
) -> dict[str, object]:
    clean_title = title.strip()
    clean_citation = citation.strip()
    clean_jurisdiction = jurisdiction.strip()
    clean_url = source_url.strip()
    pages_to_exclude = parse_excluded_pages(excluded_pages)
    parsed_url = urlparse(clean_url)
    if not clean_title or not clean_citation or not clean_jurisdiction:
        raise HTTPException(
            status_code=422, detail="Title, citation, and jurisdiction are required."
        )
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(
            status_code=422, detail="Source URL must be an HTTP or HTTPS link."
        )
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF file.")

    content = await file.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413, detail="PDF exceeds the 30 MB upload limit."
        )
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The selected file is not a PDF.")

    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    document_id = str(uuid4())
    relative_path = f"uploads/{document_id}.pdf"
    destination = DOCUMENTS_DIR / relative_path
    temporary_path = destination.with_suffix(".upload")
    try:
        from pypdf.errors import PdfReadError

        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(content)
        _, quality = analyze_pdf(temporary_path, max_pages=1000)

        with library_lock:
            records = load_sources()
            temporary_path.replace(destination)
            records[relative_path] = {
                "id": document_id,
                "title": clean_title,
                "citation": clean_citation,
                "jurisdiction": clean_jurisdiction,
                "source_url": clean_url,
                "original_filename": Path(file.filename).name[:255],
                "verified": verified,
                "excluded_pages": pages_to_exclude,
                "quality": quality,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                save_sources(records)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
    except HTTPException:
        temporary_path.unlink(missing_ok=True)
        raise
    except ValueError as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {exc}") from exc
    except PdfReadError as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail="The uploaded PDF is damaged or invalid."
        ) from exc
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        logger.exception("PDF upload failed.")
        raise HTTPException(
            status_code=500, detail="Could not save the PDF to the document library."
        ) from exc

    return {
        "id": document_id,
        "title": clean_title,
        "status": "needs_indexing" if verified else "needs_review",
        "verified": verified,
        "quality": quality,
    }


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
    except EmbeddingQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document indexing failed.")
        raise HTTPException(
            status_code=502,
            detail="Indexing failed. Check the backend logs and Gemini configuration.",
        ) from exc
