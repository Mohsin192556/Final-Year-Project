"use client";

import Link from "next/link";
import { FormEvent, useCallback, useRef, useState } from "react";

type LegalDocument = {
  id: string;
  title: string;
  citation: string;
  jurisdiction: string;
  source_url: string;
  original_filename: string;
  verified: boolean;
  indexed: boolean;
  excluded_pages: number[];
  status: "indexed" | "needs_review" | "needs_indexing";
  quality?: {
    page_count: number;
    extracted_page_count: number;
    empty_pages: number[];
    text_character_count: number;
    extraction_method: string;
  } | null;
};

type DocumentPreview = {
  document_id: string;
  title: string;
  page_number: number;
  page_count: number;
  text: string;
  truncated: boolean;
  quality: NonNullable<LegalDocument["quality"]>;
};

const apiBase = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export default function LibraryPage() {
  const [adminKey, setAdminKey] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [documents, setDocuments] = useState<LegalDocument[]>([]);
  const [preview, setPreview] = useState<DocumentPreview | null>(null);
  const [title, setTitle] = useState("");
  const [citation, setCitation] = useState("");
  const [jurisdiction, setJurisdiction] = useState("Pakistan");
  const [sourceUrl, setSourceUrl] = useState("");
  const [excludedPages, setExcludedPages] = useState("");
  const [verified, setVerified] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const request = useCallback(
    (path: string, init: RequestInit = {}) =>
      fetch(`${apiBase}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${adminKey}`,
          ...init.headers,
        },
      }),
    [adminKey],
  );

  const loadDocuments = useCallback(async () => {
    setError("");
    try {
      const response = await request("/api/documents");
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Could not load the document library.");
      }
      setDocuments(payload.documents);
      setAuthenticated(true);
    } catch (caught) {
      setAuthenticated(false);
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not connect to the document library.",
      );
    }
  }, [request]);

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!adminKey.trim()) return;
    await loadDocuments();
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile) {
      setError("Choose a PDF to upload.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    const body = new FormData();
    body.set("file", selectedFile);
    body.set("title", title);
    body.set("citation", citation);
    body.set("jurisdiction", jurisdiction);
    body.set("source_url", sourceUrl);
    body.set("verified", String(verified));
    body.set("excluded_pages", excludedPages);
    try {
      const response = await request("/api/documents/upload", {
        method: "POST",
        body,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "The PDF could not be uploaded.");
      }
      setNotice(
        verified
          ? "PDF uploaded. It is ready to be added to the search index."
          : "PDF uploaded to the review queue. Verify the source before indexing it.",
      );
      setTitle("");
      setCitation("");
      setSourceUrl("");
      setExcludedPages("");
      setVerified(false);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadDocuments();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "The PDF upload failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function changeVerification(document: LegalDocument) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await request(
        `/api/documents/${encodeURIComponent(document.id)}/verification`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ verified: !document.verified }),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Could not update the review status.");
      }
      setNotice(
        payload.verified
          ? "Source marked reviewed. Rebuild the index to make it searchable."
          : "Source removed from the verified set. Rebuild the index to apply this change.",
      );
      await loadDocuments();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not update the review status.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function showPreview(document: LegalDocument, page = 1) {
    if (preview?.document_id === document.id && preview.page_number === page) {
      setPreview(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await request(
        `/api/documents/${encodeURIComponent(document.id)}/preview?page=${page}`,
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Could not extract text preview.");
      }
      setPreview(payload);
      await loadDocuments();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not extract text preview.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function indexDocuments() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await request("/api/documents/index", {
        method: "POST",
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "The knowledge base could not be indexed.");
      }
      setNotice(
        `Knowledge base ready: ${payload.document_count} PDF(s), ${payload.indexed_page_count} indexed pages, ${payload.chunk_count} searchable passages.`,
      );
      await loadDocuments();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Indexing failed. Check backend logs and Gemini configuration.",
      );
    } finally {
      setBusy(false);
    }
  }

  const needsIndexing = documents.some(
    (document) => document.verified && !document.indexed,
  );

  return (
    <main className="library-shell">
      <header className="library-header">
        <Link className="library-brand" href="/">
          <span className="brand-icon" aria-hidden="true">
            Q
          </span>
          <span>qanoon</span>
        </Link>
        <Link className="back-to-chat" href="/">
          ← Back to assistant
        </Link>
      </header>

      <div className="library-main">
        <div className="library-title-row">
          <div>
            <p className="eyebrow">KNOWLEDGE BASE</p>
            <h1>Legal document library</h1>
            <p className="library-intro">
              Add the source PDFs your assistant can search, review their legal
              details, then build a searchable index.
            </p>
          </div>
          <div className="library-badge">
            <span>§</span> Source-first answers
          </div>
        </div>

        {error && (
          <div className="library-alert error" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="library-alert success" role="status">
            {notice}
          </div>
        )}

        {!authenticated ? (
          <section className="library-panel admin-panel">
            <div className="panel-icon">⌑</div>
            <h2>Admin access</h2>
            <p>
              Document upload and indexing are restricted. Enter the
              administrator key configured in the backend environment.
            </p>
            <form className="admin-form" onSubmit={(event) => void connect(event)}>
              <label htmlFor="admin-key">Backend admin key</label>
              <input
                autoComplete="off"
                id="admin-key"
                onChange={(event) => setAdminKey(event.target.value)}
                placeholder="Enter the ADMIN_API_KEY value"
                type="password"
                value={adminKey}
              />
              <button className="primary-button" disabled={!adminKey.trim()}>
                Connect to library
              </button>
            </form>
            <p className="security-footnote">
              The key is held only in this page’s memory; it is not saved in
              browser storage.
            </p>
          </section>
        ) : (
          <>
            <section className="library-panel upload-panel">
              <div className="panel-heading">
                <div>
                  <span className="step-label">STEP 1 · ADD A SOURCE</span>
                  <h2>Upload a law PDF</h2>
                </div>
                <span className="file-limit">PDF · up to 30 MB</span>
              </div>
              <form className="document-form" onSubmit={(event) => void upload(event)}>
                <div className="form-grid">
                  <label>
                    Document title
                    <input
                      maxLength={200}
                      minLength={2}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder="e.g. The Constitution of Pakistan"
                      required
                      value={title}
                    />
                  </label>
                  <label>
                    Citation
                    <input
                      maxLength={300}
                      minLength={2}
                      onChange={(event) => setCitation(event.target.value)}
                      placeholder="Official title, year, article or case citation"
                      required
                      value={citation}
                    />
                  </label>
                  <label>
                    Jurisdiction
                    <input
                      maxLength={120}
                      minLength={2}
                      onChange={(event) => setJurisdiction(event.target.value)}
                      placeholder="Pakistan, Punjab, Sindh…"
                      required
                      value={jurisdiction}
                    />
                  </label>
                  <label>
                    Official source URL
                    <input
                      maxLength={2000}
                      minLength={8}
                      onChange={(event) => setSourceUrl(event.target.value)}
                      placeholder="https://…"
                      required
                      type="url"
                      value={sourceUrl}
                    />
                  </label>
                  <label>
                    PDF pages to skip (optional)
                    <input
                      maxLength={500}
                      onChange={(event) => setExcludedPages(event.target.value)}
                      placeholder="e.g. 1-11 for cover and contents pages"
                      value={excludedPages}
                    />
                  </label>
                </div>
                <label className="file-picker" htmlFor="legal-pdf">
                  <span className="file-picker-icon" aria-hidden="true">
                    ↑
                  </span>
                  <span>
                    <strong>
                      {selectedFile ? selectedFile.name : "Choose a legal PDF"}
                    </strong>
                    <small>
                      {selectedFile
                        ? `${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB selected`
                        : "Text-based PDFs are supported. Scanned files need OCR first."}
                    </small>
                  </span>
                  <span className="browse-button">Browse files</span>
                  <input
                    accept="application/pdf,.pdf"
                    id="legal-pdf"
                    onChange={(event) =>
                      setSelectedFile(event.target.files?.[0] ?? null)
                    }
                    required
                    ref={fileInputRef}
                    type="file"
                  />
                </label>
                <label className="review-checkbox">
                  <input
                    checked={verified}
                    onChange={(event) => setVerified(event.target.checked)}
                    type="checkbox"
                  />
                  <span>
                    I have checked this document’s authenticity, current
                    version, jurisdiction, citation, and source URL.
                  </span>
                </label>
                <div className="form-actions">
                  <p>
                    Unreviewed documents stay out of answers until verified
                    and indexed.
                  </p>
                  <button
                    className="primary-button"
                    disabled={busy || !selectedFile}
                    type="submit"
                  >
                    {busy ? "Uploading…" : "Upload PDF"}
                    <span aria-hidden="true">→</span>
                  </button>
                </div>
              </form>
            </section>

            <section className="library-panel collection-panel">
              <div className="panel-heading">
                <div>
                  <span className="step-label">STEP 2 · REVIEW & INDEX</span>
                  <h2>Your legal sources</h2>
                </div>
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void loadDocuments()}
                  type="button"
                >
                  Refresh
                </button>
              </div>
              <div className="index-explainer">
                <span aria-hidden="true">✳</span>
                <p>
                  Indexing extracts selectable text, keeps page-level citations,
                  splits it into passages, and creates Gemini embeddings for
                  retrieval. It can take a few minutes for large libraries.
                </p>
              </div>

              {documents.length === 0 ? (
                <div className="empty-library">
                  <span aria-hidden="true">▤</span>
                  <strong>No documents yet</strong>
                  <p>Upload your first legal source PDF above.</p>
                </div>
              ) : (
                <div className="document-list">
                  {documents.map((document) => (
                    <div className="document-entry" key={document.id}>
                      <article className="document-row">
                        <div className="pdf-icon">PDF</div>
                        <div className="document-details">
                          <strong>{document.title}</strong>
                          <span>
                            {document.citation} · {document.jurisdiction}
                          </span>
                          <small>
                            {document.original_filename}
                            {document.quality &&
                              ` · ${document.quality.page_count} pages · ${document.quality.text_character_count.toLocaleString()} extracted characters · ${document.quality.empty_pages.length} blank-text pages`}
                            {document.excluded_pages.length > 0 &&
                              ` · skips PDF pages ${document.excluded_pages.join(", ")}`}
                          </small>
                        </div>
                        <div className={`document-status ${document.status}`}>
                          <span />
                          {document.status === "indexed"
                            ? "Indexed"
                            : document.status === "needs_review"
                              ? "Needs review"
                              : "Ready to index"}
                        </div>
                        {document.source_url && (
                          <a
                            aria-label={`Open original source for ${document.title}`}
                            className="source-link"
                            href={document.source_url}
                            rel="noreferrer"
                            target="_blank"
                            title="Open source"
                          >
                            ↗
                          </a>
                        )}
                        <button
                          className="review-button"
                          disabled={busy}
                          onClick={() => void changeVerification(document)}
                          type="button"
                        >
                          {document.verified ? "Unverify" : "Mark reviewed"}
                        </button>
                        <button
                          className="review-button preview-button"
                          disabled={busy}
                          onClick={() => void showPreview(document)}
                          type="button"
                        >
                          {preview?.document_id === document.id
                            ? "Close preview"
                            : "Preview text"}
                        </button>
                      </article>
                      {preview?.document_id === document.id && (
                        <section
                          aria-label={`Extracted text preview for ${document.title}`}
                          className="text-preview"
                        >
                          <div className="preview-heading">
                            <strong>
                              Extracted text · PDF page {preview.page_number} of{" "}
                              {preview.page_count}
                            </strong>
                            <div>
                              <button
                                disabled={busy || preview.page_number <= 1}
                                onClick={() =>
                                  void showPreview(document, preview.page_number - 1)
                                }
                                type="button"
                              >
                                Previous
                              </button>
                              <button
                                disabled={
                                  busy ||
                                  preview.page_number >= preview.page_count
                                }
                                onClick={() =>
                                  void showPreview(document, preview.page_number + 1)
                                }
                                type="button"
                              >
                                Next
                              </button>
                            </div>
                          </div>
                          <pre>
                            {preview.text ||
                              "No selectable text on this page; this may be a scanned page."}
                          </pre>
                          {preview.truncated && (
                            <p className="preview-truncated">
                              Preview shortened to 6,000 characters.
                            </p>
                          )}
                          <p className="preview-quality">
                            Extracted {preview.quality.extracted_page_count} of{" "}
                            {preview.quality.page_count} pages with{" "}
                            {preview.quality.extraction_method}. Citations use
                                        PDF page numbers; skipped pages are excluded from indexing.
                          </p>
                        </section>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="index-footer">
                <p>
                  <strong>Only reviewed sources are indexed.</strong>
                  {documents.some((document) => document.indexed)
                    ? " Re-index after changing the source library."
                    : " You need a Gemini API key configured on the backend."}
                </p>
                <button
                  className="primary-button"
                  disabled={busy || !needsIndexing}
                  onClick={() => void indexDocuments()}
                  type="button"
                >
                  {busy ? "Building index…" : "Build / refresh search index"}
                  <span aria-hidden="true">✳</span>
                </button>
              </div>
            </section>
          </>
        )}

        <footer className="library-disclaimer">
          This library supports legal research, not legal advice. Confirm the
          source, amendment status, and applicable jurisdiction before relying
          on any generated answer.
        </footer>
      </div>
    </main>
  );
}
