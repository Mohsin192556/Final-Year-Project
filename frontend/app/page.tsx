"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { apiUrl } from "../lib/api";

type Source = {
  source_id: string;
  title: string;
  citation: string;
  jurisdiction: string;
  source_url: string | null;
  excerpt: string;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  disclaimer?: string;
};

const suggestions = [
  "What should I know before signing a rental agreement?",
  "How do Supreme Court judgments affect existing laws?",
  "What documents might be relevant to a family matter?",
];

function renderAnswer(text: string) {
  const formatted = text
    .replace(/\r\n/g, "\n")
    .replace(/(^|\s)(#{1,6})\s+/g, "$1\n\n$2 ")
    .replace(/\s+\*\s+(?=\*\*)/g, "\n• ")
    .replace(/^\s*---\s*$/gm, "");

  function renderInline(line: string, keyPrefix: string) {
    return line
      .split(/(\[S\d+\]|\*\*[^*]+\*\*|\*[^*]+\*)/g)
      .map((part, partIndex) => {
        const key = `${keyPrefix}-${partIndex}`;
        if (/^\[S\d+\]$/.test(part)) {
          return (
            <sup className="citation-mark" key={key}>
              {part}
            </sup>
          );
        }
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={key}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("*") && part.endsWith("*")) {
          return <em key={key}>{part.slice(1, -1)}</em>;
        }
        return <span key={key}>{part}</span>;
      });
  }

  const lines = formatted.split("\n").map((line) => line.trim()).filter(Boolean);
  const blocks = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      blocks.push(
        <h4 className="answer-heading" key={`heading-${index}`}>
          {renderInline(heading[1], `heading-${index}`)}
        </h4>,
      );
      continue;
    }

    const bullet = line.match(/^(?:[-*•])\s+(.+)$/);
    if (bullet) {
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(/^(?:[-*•])\s+(.+)$/);
        if (!item) break;
        items.push(
          <li key={`bullet-${index}`}>
            {renderInline(item[1], `bullet-${index}`)}
          </li>,
        );
        index += 1;
      }
      index -= 1;
      blocks.push(
        <ul className="answer-list" key={`list-${index}`}>
          {items}
        </ul>,
      );
      continue;
    }

    const numberedHeading = line.match(/^(\d+\.\s+.{1,60})$/);
    if (numberedHeading) {
      blocks.push(
        <h4 className="answer-heading" key={`numbered-heading-${index}`}>
          {renderInline(numberedHeading[1], `numbered-heading-${index}`)}
        </h4>,
      );
      continue;
    }

    blocks.push(
      <p className="answer-paragraph" key={`paragraph-${index}`}>
        {renderInline(line, `paragraph-${index}`)}
      </p>,
    );
  }
  return blocks;
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [health, setHealth] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;
    let healthRequest: Promise<Response>;
    try {
      healthRequest = fetch(apiUrl("/api/health"));
    } catch {
      setHealth("offline");
      return () => {
        active = false;
      };
    }

    healthRequest
      .then((response) => {
        if (active) setHealth(response.ok ? "online" : "offline");
      })
      .catch(() => {
        if (active) setHealth("offline");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function sendMessage(event?: FormEvent, suggested?: string) {
    event?.preventDefault();
    const content = (suggested ?? question).trim();
    if (!content || busy) return;

    const priorMessages = messages;
    setQuestion("");
    setError("");
    setBusy(true);
    setMessages([...priorMessages, { role: "user", content }]);

    try {
      const response = await fetch(apiUrl("/api/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: content,
          history: priorMessages.slice(-8).map(({ role, content: text }) => ({
            role,
            content: text,
          })),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "The assistant could not answer. Please try again.",
        );
      }
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: payload.answer,
          sources: payload.sources,
          disclaimer: payload.disclaimer,
        },
      ]);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not connect to the legal assistant. Check that the backend is running.",
      );
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="Qanoon home">
          <span className="brand-icon" aria-hidden="true">
            Q
          </span>
          <span>
            <strong>qanoon</strong>
            <small>PAKISTAN LAW, MADE CLEAR</small>
          </span>
        </Link>

        <button
          className="new-chat-button"
          type="button"
          onClick={() => {
            setMessages([]);
            setError("");
            inputRef.current?.focus();
          }}
        >
          <span aria-hidden="true">＋</span> New conversation
        </button>

        <div className="sidebar-section">
          <p className="section-label">YOUR GUIDE</p>
          <div className="guide-card">
            <span className="guide-icon" aria-hidden="true">
              §
            </span>
            <div>
              <strong>Pakistani law</strong>
              <p>Understand the law with sources you can check.</p>
            </div>
            <Link className="library-link" href="/library">
              <span aria-hidden="true">▤</span>
              Legal document library
              <span className="library-arrow" aria-hidden="true">
                ↗
              </span>
            </Link>
          </div>
        </div>

        <div className="sidebar-bottom">
          <div className="privacy-note">
            <span aria-hidden="true">◇</span>
            <p>
              Your questions are sent to the AI service to generate an answer.
              Avoid sharing CNICs or other sensitive details.
            </p>
          </div>
          <a className="about-link" href="#about">
            About this assistant <span aria-hidden="true">↗</span>
          </a>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="mobile-brand">
            <span className="brand-icon" aria-hidden="true">
              Q
            </span>
            <strong>qanoon</strong>
          </div>
          <div className="jurisdiction">
            <span className="jurisdiction-dot" />
            Pakistan <span className="chevron">⌄</span>
          </div>
          <Link className="library-top-link" href="/library">
            Documents
          </Link>
          <div className={`connection-status ${health}`}>
            <span className="status-dot" />
            {health === "online"
              ? "Assistant ready"
              : health === "checking"
                ? "Connecting"
                : "Backend offline"}
          </div>
        </header>

        <div className="conversation">
          {messages.length === 0 ? (
            <div className="welcome">
              <div className="welcome-emblem" aria-hidden="true">
                <span>§</span>
              </div>
              <p className="eyebrow">A clearer way to understand the law</p>
              <h1>
                Legal questions,
                <br />
                <span>meet clear answers.</span>
              </h1>
              <p className="welcome-copy">
                Explore Pakistani law with plain-language explanations grounded
                in legal sources. Start with a question below.
              </p>

              <div className="suggestions-label">GET STARTED WITH</div>
              <div className="suggestions">
                {suggestions.map((suggestion, index) => (
                  <button
                    className="suggestion-card"
                    key={suggestion}
                    type="button"
                    onClick={() => void sendMessage(undefined, suggestion)}
                  >
                    <span className={`suggestion-icon icon-${index}`}>
                      {["⌂", "§", "◇"][index]}
                    </span>
                    <span>{suggestion}</span>
                    <span className="suggestion-arrow" aria-hidden="true">
                      ↗
                    </span>
                  </button>
                ))}
              </div>

              <div className="welcome-disclaimer">
                <span aria-hidden="true">i</span>
                Information, not legal advice. Always verify before acting.
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message, index) => (
                <article
                  className={`message ${message.role}`}
                  key={`${index}-${message.role}`}
                >
                  <div className="message-avatar" aria-hidden="true">
                    {message.role === "assistant" ? "Q" : "Y"}
                  </div>
                  <div className="message-body">
                    <div className="message-heading">
                      {message.role === "assistant" ? "Qanoon" : "You"}
                    </div>
                    <div className="message-text">
                      {message.role === "assistant"
                        ? renderAnswer(message.content)
                        : message.content}
                    </div>
                    {message.sources && message.sources.length > 0 && (
                      <div className="sources">
                        <p className="sources-heading">SOURCES</p>
                        {message.sources.map((source) => (
                          <article className="source-card" key={source.source_id}>
                            <span className="source-id">
                              {source.source_id}
                            </span>
                            <div className="source-content">
                              <strong>{source.title}</strong>
                              <span>
                                {source.citation} · {source.jurisdiction}
                              </span>
                              {source.excerpt && (
                                <details className="source-excerpt">
                                  <summary>Show supporting passage</summary>
                                  <p>&ldquo;{source.excerpt}&rdquo;</p>
                                </details>
                              )}
                              {source.source_url && (
                                <a
                                  href={source.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  View original source ↗
                                </a>
                              )}
                            </div>
                          </article>
                        ))}
                      </div>
                    )}
                    {message.disclaimer && (
                      <p className="answer-disclaimer">{message.disclaimer}</p>
                    )}
                  </div>
                </article>
              ))}
              {busy && (
                <div className="thinking">
                  <span className="message-avatar" aria-hidden="true">
                    Q
                  </span>
                  <span className="thinking-dots">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span>Checking the legal sources…</span>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <div className="composer-wrap">
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button type="button" onClick={() => setError("")}>
                Dismiss
              </button>
            </div>
          )}
          <form className="composer" onSubmit={(event) => void sendMessage(event)}>
            <textarea
              ref={inputRef}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder="Ask a question about Pakistani law…"
              rows={1}
              maxLength={4000}
              aria-label="Your legal question"
            />
            <button
              className="send-button"
              type="submit"
              disabled={!question.trim() || busy}
              aria-label="Send question"
            >
              <span aria-hidden="true">↑</span>
            </button>
          </form>
          <p className="composer-footnote">
            Qanoon can make mistakes. Check cited sources and seek a lawyer for
            advice.
          </p>
        </div>
      </section>

      <footer id="about" className="sr-only">
        Qanoon is a legal information assistant and does not provide legal
        advice or legal representation.
      </footer>
    </main>
  );
}
