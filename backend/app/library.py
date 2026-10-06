import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.embeddings import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_DIR = ROOT / "data" / "documents"
SOURCES_FILE = DOCUMENTS_DIR / "sources.json"
load_dotenv(ROOT / ".env")


class EmbeddingQuotaExceeded(RuntimeError):
    pass


def storage_path() -> Path:
    configured = Path(os.getenv("CHROMA_PERSIST_DIRECTORY", "data/chroma"))
    return configured if configured.is_absolute() else ROOT / configured


def _build_embeddings(backend: str | None = None):
    backend_name = (backend or os.getenv("EMBEDDING_BACKEND", "google")).lower()
    if backend_name == "local":
        return FakeEmbeddings(size=768)
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
    )


def _quota_error(exc: BaseException) -> bool:
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower()


def document_path(relative_path: str) -> Path:
    base = DOCUMENTS_DIR.resolve()
    path = (base / relative_path).resolve()
    if not path.is_relative_to(base):
        raise ValueError("The source manifest contains an unsafe document path.")
    return path


def load_sources() -> dict[str, dict[str, object]]:
    if not SOURCES_FILE.is_file():
        return {}
    try:
        content = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("The legal source manifest contains invalid JSON.") from exc
    if not isinstance(content, dict) or not isinstance(content.get("sources"), dict):
        raise ValueError("The legal source manifest must contain a sources object.")
    records = content["sources"]
    valid_records = {
        key: value
        for key, value in records.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    for relative_path in valid_records:
        document_path(relative_path)
    return valid_records


def save_sources(records: dict[str, dict[str, object]]) -> None:
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = SOURCES_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps({"sources": records}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_file.replace(SOURCES_FILE)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as document:
        for block in iter(lambda: document.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def document_fingerprint(path: Path, record: dict[str, object]) -> str:
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
        "file_sha256": file_sha256(path),
        "metadata": index_metadata,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def list_documents() -> list[dict[str, object]]:
    records = load_sources()
    active_index_path = storage_path() / "active-index.json"
    active_index: dict[str, object] = {}
    if active_index_path.is_file():
        try:
            loaded_index = json.loads(active_index_path.read_text(encoding="utf-8"))
            if isinstance(loaded_index, dict):
                active_index = loaded_index
        except json.JSONDecodeError:
            active_index = {}
    indexed_documents = active_index.get("documents", {})
    if not isinstance(indexed_documents, dict):
        indexed_documents = {}

    documents = []
    for relative_path, record in records.items():
        path = document_path(relative_path)
        fingerprint = (
            document_fingerprint(path, record) if path.is_file() else None
        )
        is_verified = record.get("verified") is True
        is_indexed = bool(
            fingerprint
            and indexed_documents.get(relative_path) == fingerprint
            and is_verified
        )
        documents.append(
            {
                "id": record.get("id", relative_path),
                "title": record.get("title", path.stem),
                "citation": record.get("citation", ""),
                "jurisdiction": record.get("jurisdiction", "Pakistan"),
                "source_url": record.get("source_url", ""),
                "original_filename": record.get("original_filename", path.name),
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

    records = load_sources()
    for relative_path, record in records.items():
        if record.get("id", relative_path) != document_id:
            continue
        path = document_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError("The uploaded PDF is missing.")
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("This PDF is password-protected.")
        page_count = len(reader.pages)
        if page_number < 1 or page_number > page_count:
            raise IndexError("Requested page is outside this PDF.")

        quality = record.get("quality")
        if not isinstance(quality, dict):
            _, quality = analyze_pdf(path)
            record["quality"] = quality
            save_sources(records)

        text = normalize_page_text(
            reader.pages[page_number - 1].extract_text(extraction_mode="layout") or ""
        )
        return {
            "document_id": document_id,
            "title": record.get("title", path.stem),
            "page_number": page_number,
            "page_count": page_count,
            "text": text[:6000],
            "truncated": len(text) > 6000,
            "quality": quality,
        }
    raise KeyError("Document not found.")


def verify_document(document_id: str, verified: bool) -> dict[str, object]:
    records = load_sources()
    for relative_path, record in records.items():
        if record.get("id", relative_path) == document_id:
            record["verified"] = verified
            save_sources(records)
            return {
                "id": document_id,
                "verified": verified,
                "status": "needs_indexing" if verified else "needs_review",
            }
    raise KeyError("Document not found.")


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
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    records = load_sources()
    documents: list[Document] = []
    indexed_hashes: dict[str, str] = {}
    failures: list[str] = []
    quality_changed = False

    indexed_page_counts: dict[str, int] = {}
    for relative_path, metadata in records.items():
        path = document_path(relative_path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            failures.append(f"Skipping {relative_path}: PDF file is missing.")
            continue
        if metadata.get("verified") is not True:
            failures.append(f"Skipping {relative_path}: source needs review.")
            continue
        source_url = metadata.get("source_url")
        citation = metadata.get("citation")
        if (
            not isinstance(source_url, str)
            or urlparse(source_url).scheme not in {"http", "https"}
            or not urlparse(source_url).netloc
            or not isinstance(citation, str)
            or not citation.strip()
        ):
            failures.append(f"Skipping {relative_path}: citation or source URL is invalid.")
            continue
        try:
            pages, quality = analyze_pdf(path)
        except (ValueError, OSError) as exc:
            failures.append(f"Skipping {relative_path}: {exc}")
            continue

        excluded_pages = metadata.get("excluded_pages", [])
        if not isinstance(excluded_pages, list) or any(
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            or page_number > int(quality["page_count"])
            for page_number in excluded_pages
        ):
            failures.append(f"Skipping {relative_path}: excluded page list is invalid.")
            continue
        excluded = set(excluded_pages)
        pages = [item for item in pages if item[0] not in excluded]
        if not pages:
            failures.append(f"Skipping {relative_path}: all pages were excluded.")
            continue

        indexed_hashes[relative_path] = document_fingerprint(path, metadata)
        indexed_page_counts[relative_path] = len(pages)
        if metadata.get("quality") != quality:
            metadata["quality"] = quality
            quality_changed = True
        document_id = str(metadata.get("id", relative_path))
        for page_number, page_text in pages:
            documents.append(
                Document(
                    page_content=page_text,
                    metadata={
                        "source": relative_path,
                        "document_id": document_id,
                        "title": str(metadata.get("title") or path.stem),
                        "citation": f"{citation.strip()}, PDF p. {page_number}",
                        "jurisdiction": str(
                            metadata.get("jurisdiction") or "Pakistan"
                        ),
                        "source_url": source_url,
                        "page": page_number,
                        "pdf_page": page_number,
                    },
                )
            )

    if failures:
        print("\n".join(failures), file=sys.stderr)

    if quality_changed:
        save_sources(records)
    if not documents:
        raise ValueError(
            "No reviewed PDFs with extractable text are ready to index."
        )

    if documents and not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("Set GOOGLE_API_KEY in backend/.env before indexing.")

    chunks = RecursiveCharacterTextSplitter(
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
    ).split_documents(documents)
    persist_directory = storage_path()
    persist_directory.mkdir(parents=True, exist_ok=True)
    collection_name = (
        f"pak_law_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
    )
    configured_embedding_backend = os.getenv("EMBEDDING_BACKEND", "google").lower()
    embedding_backend = (
        "placeholder"
        if configured_embedding_backend == "local"
        else configured_embedding_backend
    )
    embeddings = _build_embeddings(configured_embedding_backend)
    try:
        Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=collection_name,
            persist_directory=str(persist_directory),
        )
    except Exception as exc:
        if _quota_error(exc):
            embeddings = FakeEmbeddings(size=768)
            try:
                Chroma.from_documents(
                    documents=chunks,
                    embedding=embeddings,
                    collection_name=collection_name,
                    persist_directory=str(persist_directory),
                )
                embedding_backend = "placeholder"
            except Exception as fallback_exc:
                raise EmbeddingQuotaExceeded(
                    "Gemini embedding quota/rate limit reached (HTTP 429). "
                    "Wait for the quota to reset or check the Google AI project "
                    "quota, then retry. The active knowledge index was not changed."
                ) from fallback_exc
        else:
            raise

    manifest = {
        "collection_name": collection_name,
        "embedding_backend": embedding_backend,
        "retrieval_backend": "bm25",
        "chunk_count": len(chunks),
        "document_count": len(indexed_hashes),
        "indexed_page_count": sum(indexed_page_counts.values()),
        "documents": indexed_hashes,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = persist_directory / "active-index.json"
    temporary_manifest = manifest_path.with_suffix(".tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return manifest
