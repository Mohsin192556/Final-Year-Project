# Qanoon — Pakistan Law Assistant

A starter monorepo for a source-grounded legal information chat application:

- **Frontend:** Next.js App Router, React, and TypeScript.
- **Backend:** FastAPI with Gemini chat, deployed as Vercel Python Functions.
- **Knowledge base:** private Supabase Storage for PDFs and Supabase Postgres
  full-text search with article-aware BM25 reranking.

The assistant is intended for general legal information and research support.
It is not a lawyer, does not provide legal representation, and should not be
used as a substitute for advice from a qualified Pakistani lawyer.

## Requirements

- Node.js 20+
- Python 3.11+
- A Gemini API key
- A Supabase project

## Run the backend

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Create a Supabase project and run [`backend/supabase/schema.sql`](backend/supabase/schema.sql)
in its SQL Editor. Set `GOOGLE_API_KEY`, `SUPABASE_URL`, and
`SUPABASE_SERVICE_ROLE_KEY` in `backend/.env`, then run:

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
   prefer article-heading boundaries, and attaches PDF-page citations. Chunks
   are stored in Supabase Postgres and searched with PostgreSQL full-text
   search, then reranked with BM25 and an article-number boost. Indexing does
   not call Gemini. Only reviewed PDFs are included; rebuild after changing the
   reviewed set.

Scanned/image-only PDFs must be OCR'd before upload; this starter does not run
OCR. PDFs are sent directly from the browser to a private Supabase Storage
bucket using a short-lived signed upload token; the Vercel function does not
receive the file body. The API validates each upload and stores document
metadata and searchable chunks in Supabase. For trusted sources, check reuse
rights before uploading. The CLI alternative is `python ingest_documents.py`
from `backend/`.

## Chat API usage

Chat retrieval uses Supabase Postgres and does not call Gemini embeddings for
each question. Each answer makes one Gemini generation request with the recent
conversation and ranked legal excerpts. Responses are capped at 2,048 output tokens to avoid
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

Open `http://localhost:3000`. Set `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in
`frontend/.env.local` using the same Supabase project. The anon/publishable key is intended for browser use;
the backend service-role key must never be placed in the frontend.

## Deploy to Vercel

Deploy the frontend and backend as two Vercel projects from this repository.
Vercel runs the FastAPI app as Python Functions; Supabase holds all persistent
PDF, metadata, and search-index data.

1. In Vercel, import the repository for the frontend project and set **Root
   Directory** to `frontend`.
2. Set `NEXT_PUBLIC_API_BASE_URL` to the backend Vercel deployment origin,
   `NEXT_PUBLIC_SUPABASE_URL` to the Supabase project URL, and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` to the Supabase publishable/anon key. Set
   these for the Vercel environments you use, then redeploy the frontend.
3. Create a second Vercel project from the same repository and set its **Root
   Directory** to `backend`. Vercel detects the FastAPI app and dependencies
   from `app/main.py` and `requirements.txt`.
4. Configure these backend environment variables in Vercel:
   `GOOGLE_API_KEY`, `GEMINI_MODEL`, a strong `ADMIN_API_KEY`,
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and
   `SUPABASE_STORAGE_BUCKET=qanoon-legal-documents`.
5. Set backend `CORS_ORIGINS` to the exact frontend Vercel origin, with no
   trailing slash. Keep the Supabase service-role key server-only.
6. Verify the backend `/api/health` returns `{"status":"ok"}`. Run the Supabase
   schema once, then confirm `/api/health/knowledge-base` reports `indexed`
   after reviewing documents and building the search index.

The browser-visible `NEXT_PUBLIC_API_BASE_URL` must contain only the API origin,
never API keys or other secrets. Vercel's local function filesystem is
ephemeral; do not configure document or index paths there. Supabase Free has
usage limits and can pause inactive projects, so check its current plan limits
before relying on it for important data.

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
- Chat sends retrieved excerpts to Gemini to generate answers. Review data
  handling and provider terms before uploading confidential material.
- The first UI/API is English-only and covers no preselected practice area.
- Add a legal-professional-reviewed evaluation set before expanding coverage.
- Add authentication, privacy/retention policy, abuse controls, and deployment
  hardening before collecting sensitive user matters or launching publicly.
- Document upload and court filing are intentionally not exposed as public API
  operations in this starter.
