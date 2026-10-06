# Legal source documents

The `/library` page uploads text-based PDFs and records each document's citation,
jurisdiction, and official source URL in `sources.json`. The indexer includes
only entries explicitly marked `"verified": true`. Verify authenticity,
currency, jurisdiction, and lawful reuse before marking a document reviewed.

Scanned/image-only PDFs must be OCR'd first. Run `python ingest_documents.py`
from the `backend` directory to rebuild the search index from the reviewed
library without using the web interface.
