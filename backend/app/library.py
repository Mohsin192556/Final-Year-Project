import hashlib
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.supabase_store import (
    response_rows,
    storage_bucket,
    supabase_client,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_PDF_BYTES = 30 * 1024 * 1024
logger = logging.getLogger(__name__)
load_dotenv(ROOT / ".env")


def document_fingerprint(content: bytes, record: dict[str, object]) -> str:
    index_metadata = {
        key: record.get(key)
        for key in (
            "title",
            "citation",
            "jurisdiction",
            "source_url",
            "excluded_pages",
        )
    }
    payload = {
        "file_sha256": hashlib.sha256(content).hexdigest(),
        "metadata": index_metadata,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _document(document_id: str) -> dict[str, object]:
    rows = response_rows(
        supabase_client()
        .table("legal_documents")
        .select("*")
        .eq("id", document_id)
        .limit(1)
        .execute()
    )
    if not rows:
        raise KeyError("Document not found.")
    return rows[0]


def _download_document(storage_path: str) -> bytes:
    content = supabase_client().storage.from_(storage_bucket()).download(storage_path)
    if not isinstance(content, bytes):
        raise RuntimeError("Supabase Storage returned an invalid PDF response.")
    return content


def _active_index_row() -> dict[str, object] | None:
    rows = response_rows(
        supabase_client()
        .table("legal_index_state")
        .select("*")
        .eq("id", True)
        .limit(1)
        .execute()
    )
    if not rows or not rows[0].get("active_version"):
        return None
    return rows[0]


def active_index_manifest() -> dict[str, object] | None:
    row = _active_index_row()
    if row is None:
        return None
    return {
        "index_version": row["active_version"],
        "documents": row.get("fingerprints") or {},
        "document_count": row.get("document_count", 0),
        "indexed_page_count": row.get("indexed_page_count", 0),
        "chunk_count": row.get("chunk_count", 0),
        "indexed_at": row.get("indexed_at"),
    }


def list_documents() -> list[dict[str, object]]:
    records = response_rows(
        supabase_client()
        .table("legal_documents")
        .select("*")
        .eq("status", "ready")
        .execute()
    )
    active_index = active_index_manifest() or {}
    indexed_documents = active_index.get("documents", {})
    if not isinstance(indexed_documents, dict):
        indexed_documents = {}

    documents = []
    for record in records:
        storage_path = str(record["storage_path"])
        is_verified = record.get("verified") is True
        is_indexed = bool(
            record.get("fingerprint")
            and indexed_documents.get(storage_path) == record.get("fingerprint")
            and is_verified
        )
        documents.append(
            {
                "id": record["id"],
                "title": record.get("title", "Legal document"),
                "citation": record.get("citation", ""),
                "jurisdiction": record.get("jurisdiction", "Pakistan"),
                "source_url": record.get("source_url", ""),
                "original_filename": record.get("original_filename", ""),
                "excluded_pages": record.get("excluded_pages", []),
                "verified": is_verified,
                "indexed": is_indexed,
                "quality": record.get("quality"),
                "status": (
                    "indexed"
                    if is_indexed
                    else "needs_review"
                    if not is_verified
                    else "needs_indexing"
                ),
            }
        )
    return sorted(documents, key=lambda item: str(item["title"]).casefold())


def preview_document(
    document_id: str, page_number: int
) -> dict[str, object]:
    from pypdf import PdfReader

    record = _document(document_id)
    content = _download_document(str(record["storage_path"]))
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        reader = PdfReader(str(temporary_path))
        if reader.is_encrypted:
            raise ValueError("This PDF is password-protected.")
        page_count = len(reader.pages)
        if page_number < 1 or page_number > page_count:
            raise IndexError("Requested page is outside this PDF.")

        text = normalize_page_text(
            reader.pages[page_number - 1].extract_text(extraction_mode="layout") or ""
        )
        return {
            "document_id": document_id,
            "title": record.get("title", "Legal document"),
            "page_number": page_number,
            "page_count": page_count,
            "text": text[:6000],
            "truncated": len(text) > 6000,
            "quality": record.get("quality"),
        }
    finally:
        temporary_path.unlink(missing_ok=True)


def create_document_upload(
    *,
    title: str,
    citation: str,
    jurisdiction: str,
    source_url: str,
    original_filename: str,
    verified: bool,
    excluded_pages: list[int],
) -> dict[str, object]:
    document_id = str(uuid4())
    path = f"uploads/{document_id}.pdf"
    storage = supabase_client().storage.from_(storage_bucket())
    signed_response = storage.create_signed_upload_url(path)
    signed_data = (
        signed_response
        if isinstance(signed_response, dict)
        else getattr(signed_response, "data", None)
    )
    if not isinstance(signed_data, dict) or not isinstance(
        signed_data.get("token"), str
    ):
        raise RuntimeError("Supabase did not return a signed upload token.")

    client = supabase_client()
    client.table("legal_documents").insert(
        {
            "id": document_id,
            "storage_path": path,
            "title": title,
            "citation": citation,
            "jurisdiction": jurisdiction,
            "source_url": source_url,
            "original_filename": Path(original_filename).name[:255],
            "verified": verified,
            "excluded_pages": excluded_pages,
            "status": "pending",
        }
    ).execute()
    return {
        "id": document_id,
        "storage_path": path,
        "upload_token": signed_data["token"],
        "bucket": storage_bucket(),
    }


def _remove_pending_upload(document_id: str, path: str) -> None:
    client = supabase_client()
    client.storage.from_(storage_bucket()).remove([path])
    client.table("legal_documents").delete().eq("id", document_id).execute()


def complete_document_upload(document_id: str) -> dict[str, object]:
    from pypdf.errors import PdfReadError

    record = _document(document_id)
    if record.get("status") == "ready":
        return {
            "id": document_id,
            "title": record.get("title", "Legal document"),
            "status": "needs_indexing" if record.get("verified") else "needs_review",
            "verified": record.get("verified") is True,
            "quality": record.get("quality"),
        }
    if record.get("status") != "pending":
        raise ValueError("This upload is not waiting for completion.")
    path = str(record["storage_path"])
    content = _download_document(path)
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        _remove_pending_upload(document_id, path)
        raise ValueError("The uploaded file is not a valid PDF within the size limit.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        _, quality = analyze_pdf(temporary_path, max_pages=1000)
    except (ValueError, OSError, PdfReadError) as exc:
        _remove_pending_upload(document_id, path)
        raise ValueError(f"Could not read PDF: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    page_count = int(quality["page_count"])
    excluded_pages = record.get("excluded_pages") or []
    if any(
        not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
        or page_number > page_count
        for page_number in excluded_pages
    ):
        _remove_pending_upload(document_id, path)
        raise ValueError("Skipped page numbers are outside this PDF.")

    fingerprint = document_fingerprint(content, record)
    rows = response_rows(
        supabase_client()
        .table("legal_documents")
        .update(
            {
                "quality": quality,
                "fingerprint": fingerprint,
                "status": "ready",
            }
        )
        .eq("id", document_id)
        .eq("status", "pending")
        .select("id")
        .execute()
    )
    if not rows:
        raise RuntimeError("The pending document record could not be finalized.")
    return {
        "id": document_id,
        "title": record.get("title", "Legal document"),
        "status": "needs_indexing" if record.get("verified") else "needs_review",
        "verified": record.get("verified") is True,
        "quality": quality,
    }


def verify_document(document_id: str, verified: bool) -> dict[str, object]:
    rows = response_rows(
        supabase_client()
        .table("legal_documents")
        .update({"verified": verified})
        .eq("id", document_id)
        .eq("status", "ready")
        .select("id")
        .execute()
    )
    if not rows:
        raise KeyError("Document not found.")
    return {
        "id": document_id,
        "verified": verified,
        "status": "needs_indexing" if verified else "needs_review",
    }


def search_index_chunks(
    query_terms: str, target_article: str | None, limit: int = 200
) -> list[dict[str, object]]:
    response = supabase_client().rpc(
        "search_legal_chunks",
        {
            "p_terms": query_terms,
            "p_target_article": target_article or "",
            "p_limit": limit,
        },
    ).execute()
    return response_rows(response)


def normalize_page_text(text: str) -> str:
    normalized_lines: list[str] = []
    skip_folio = False
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        if cleaned == "CONSTITUTION OF PAKISTAN":
            skip_folio = True
            continue
        if skip_folio:
            skip_folio = False
            if re.fullmatch(r"(?:[ivxlcdm]+|\d+)", cleaned, flags=re.IGNORECASE):
                continue
        if not cleaned:
            if normalized_lines and normalized_lines[-1]:
                normalized_lines.append("")
            continue
        normalized_lines.append(cleaned)
    article_number: str | None = None
    for index, line in enumerate(normalized_lines):
        if not line:
            continue
        heading = re.match(r"^(\d+[A-Z]?)\.\s+[A-Z]", line)
        if heading:
            article_number = heading.group(1)
            continue
        if article_number:
            duplicated_number = re.match(
                rf"^{re.escape(article_number)}(\d+)\.\s+\((\d+)\)",
                line,
            )
            if (
                duplicated_number
                and duplicated_number.group(1) == duplicated_number.group(2)
            ):
                normalized_lines[index] = line[len(article_number) :]
            article_number = None
    while normalized_lines and not normalized_lines[-1]:
        normalized_lines.pop()
    return "\n".join(normalized_lines)


def analyze_pdf(
    path: Path, max_pages: int | None = None
) -> tuple[list[tuple[int, str]], dict[str, object]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ValueError(f"{path.name} is password-protected.")
    if max_pages is not None and len(reader.pages) > max_pages:
        raise ValueError(f"{path.name} exceeds the {max_pages}-page limit.")
    pages = [
        (
            page_number,
            normalize_page_text(
                page.extract_text(extraction_mode="layout") or ""
            ),
        )
        for page_number, page in enumerate(reader.pages, start=1)
    ]
    pages_with_text = [(number, text) for number, text in pages if text]
    if not pages_with_text:
        raise ValueError(
            f"{path.name} has no selectable text. OCR scanned PDFs before uploading."
        )
    quality = {
        "page_count": len(pages),
        "extracted_page_count": len(pages_with_text),
        "empty_pages": [number for number, text in pages if not text],
        "text_character_count": sum(len(text) for _, text in pages_with_text),
        "extraction_method": "pypdf layout",
    }
    return pages_with_text, quality


def build_index() -> dict[str, object]:
    client = supabase_client()
    records = response_rows(
        client.table("legal_documents")
        .select("*")
        .eq("status", "ready")
        .eq("verified", True)
        .execute()
    )
    documents: list[Document] = []
    fingerprints: dict[str, str] = {}
    indexed_page_count = 0

    for record in records:
        storage_path = str(record["storage_path"])
        source_url = record.get("source_url")
        citation = record.get("citation")
        if (
            not isinstance(source_url, str)
            or urlparse(source_url).scheme not in {"http", "https"}
            or not urlparse(source_url).netloc
            or not isinstance(citation, str)
            or not citation.strip()
        ):
            logger.warning("Skipping %s: citation or source URL is invalid.", storage_path)
            continue

        content = _download_document(storage_path)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            pages, quality = analyze_pdf(temporary_path)
        except (ValueError, OSError) as exc:
            logger.warning("Skipping %s: %s", storage_path, exc)
            continue
        finally:
            temporary_path.unlink(missing_ok=True)

        excluded_pages = record.get("excluded_pages") or []
        page_count = int(quality["page_count"])
        if any(
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            or page_number > page_count
            for page_number in excluded_pages
        ):
            logger.warning("Skipping %s: excluded page list is invalid.", storage_path)
            continue
        excluded = set(excluded_pages)
        pages = [
            (page_number, text)
            for page_number, text in pages
            if page_number not in excluded
        ]
        if not pages:
            logger.warning("Skipping %s: all pages were excluded.", storage_path)
            continue

        fingerprint = document_fingerprint(content, record)
        fingerprints[storage_path] = fingerprint
        indexed_page_count += len(pages)
        client.table("legal_documents").update(
            {"quality": quality, "fingerprint": fingerprint}
        ).eq("id", record["id"]).execute()

        for page_number, page_text in pages:
            documents.append(
                Document(
                    page_content=page_text,
                    metadata={
                        "source": storage_path,
                        "document_id": str(record["id"]),
                        "title": str(record.get("title") or "Legal document"),
                        "citation": f"{citation.strip()}, PDF p. {page_number}",
                        "jurisdiction": str(
                            record.get("jurisdiction") or "Pakistan"
                        ),
                        "source_url": source_url,
                        "page": page_number,
                        "pdf_page": page_number,
                    },
                )
            )

    if not documents:
        raise ValueError("No reviewed PDFs with extractable text are ready to index.")

    splitter = RecursiveCharacterTextSplitter(
        separators=[
            r"\n(?=\d+[A-Z]?\.\s+[A-Z])",
            "\n\n",
            "\n",
            " ",
            "",
        ],
        is_separator_regex=True,
        chunk_size=1200,
        chunk_overlap=180,
        add_start_index=True,
    )
    chunks = splitter.split_documents(documents)
    index_version = str(uuid4())
    for offset in range(0, len(chunks), 100):
        batch = [
            {
                "index_version": index_version,
                "content": chunk.page_content,
                "metadata": chunk.metadata,
            }
            for chunk in chunks[offset : offset + 100]
        ]
        client.table("legal_chunks").insert(batch).execute()

    indexed_at = datetime.now(timezone.utc).isoformat()
    client.rpc(
        "activate_legal_index",
        {
            "p_active_version": index_version,
            "p_fingerprints": fingerprints,
            "p_document_count": len(fingerprints),
            "p_indexed_page_count": indexed_page_count,
            "p_chunk_count": len(chunks),
            "p_indexed_at": indexed_at,
        },
    ).execute()
    manifest = {
        "index_version": index_version,
        "retrieval_backend": "bm25",
        "chunk_count": len(chunks),
        "document_count": len(fingerprints),
        "indexed_page_count": indexed_page_count,
        "documents": fingerprints,
        "indexed_at": indexed_at,
    }
    return manifest
