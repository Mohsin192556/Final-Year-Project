# Qanoon — Pakistan Law Assistant

A starter monorepo for a source-grounded legal information chat application:

- **Frontend:** Next.js App Router, React, and TypeScript.
- **Backend:** FastAPI with LangChain, Gemini chat, a persistent Chroma store,
  and article-aware BM25 retrieval.
- **Knowledge base:** local legal documents admitted to indexing only when their
  source manifest entry includes a citation, URL, and explicit verification.

The assistant is intended for general legal information and research support.
It is not a lawyer, does not provide legal representation, and should not be
used as a substitute for advice from a qualified Pakistani lawyer.

## Requirements

- Node.js 20+
- Python 3.11+
- A Gemini API key

## Run the backend

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Set `GOOGLE_API_KEY` in `backend/.env`, then run:

```powershell
uvicorn app.main:app --reload
```

The API health endpoint is at `http://localhost:8000/api/health`; interactive
API documentation is at `http://localhost:8000/docs`.

## Upload and index legal PDFs

1. Add a strong `ADMIN_API_KEY` (at least 24 characters) to `backend/.env`.
   Document-library routes reject requests without this key.
2. Open [http://localhost:3000/library](http://localhost:3000/library) and enter
   the admin key. It is held in page memory and is not stored in browser storage.
3. Upload a text-based PDF with its title, legal citation, jurisdiction, and
   official source URL. Optionally list cover/table-of-contents PDF pages to
   exclude from indexing (for example `1-11`). Uploads are limited to 30 MB and
   1,000 pages.
4. Check the review confirmation only after checking authenticity, current
   version, jurisdiction, citation, and source URL. You can also mark an uploaded
   source reviewed from the document list.
5. Preview extracted pages, then select **Build / refresh search index**. The
   backend normalizes layout whitespace, creates overlapping passages that
   prefer article-heading boundaries, and attaches PDF-page citations. Chat
   retrieval uses local BM25-style lexical ranking with an additional boost for
   explicitly requested article numbers. Gemini embeddings are stored when
   available, but retrieval does not depend on them; if embedding quota is
   exhausted, indexing uses placeholder vectors and still switches the active
   Chroma index only after the document passages are stored successfully. Only
   reviewed PDFs are included. Rebuild after changing the reviewed set.

Scanned/image-only PDFs must be OCR'd before upload; this starter does not run
OCR. The original PDFs, source manifest, and Chroma index are local backend data
and are ignored by Git. For trusted sources, check reuse rights before uploading.
The CLI alternative is `python ingest_documents.py` from `backend/`.

## Chat API usage

Chat retrieval is local and does not call Gemini embeddings for each question.
Each answer makes one Gemini generation request with the recent conversation and
ranked legal excerpts. Responses are capped at 2,048 output tokens to avoid
unbounded long answers while leaving room for detailed explanations. Automatic
model retries are disabled so quota errors do not silently multiply requests;
the API returns a clear quota response instead.

## Run the frontend

In another terminal:

```powershell
cd frontend
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

Open `http://localhost:3000`. Set `NEXT_PUBLIC_API_BASE_URL` when the backend is
hosted at a different origin.

## Validate

Backend tests:

```powershell
cd backend
pytest
```

Frontend production build:

```powershell
cd frontend
npm run build
```

## Current limits and next steps

- The repository does not include Pakistani legal documents; add and verify
  sources before expecting substantive legal answers.
- The local admin key is shared by anyone who knows it; use proper user
  authentication, authorization, and HTTPS before hosting this beyond a trusted
  local environment.
- Indexing may send document text to Gemini for embeddings, and chat sends
  retrieved excerpts to Gemini to generate answers. Review data handling and
  provider terms before uploading confidential material.
- The first UI/API is English-only and covers no preselected practice area.
- Add a legal-professional-reviewed evaluation set before expanding coverage.
- Add authentication, privacy/retention policy, abuse controls, and deployment
  hardening before collecting sensitive user matters or launching publicly.
- Document upload and court filing are intentionally not exposed as public API
  operations in this starter.
