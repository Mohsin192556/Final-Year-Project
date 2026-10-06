# Legal source documents

Uploaded PDFs, source metadata, and searchable passages are stored in Supabase.
Run `backend/supabase/schema.sql` in the Supabase SQL Editor before using the
document library. Only sources explicitly marked reviewed are indexed.

Scanned/image-only PDFs must be OCR'd before upload. The backend validates
uploaded files and rejects PDFs without selectable text.
