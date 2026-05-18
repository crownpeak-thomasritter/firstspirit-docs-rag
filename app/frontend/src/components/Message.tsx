import { useState } from 'react';
import type { Citation } from '../lib/api';
import { MarkdownRenderer } from './MarkdownRenderer';

function citationLabel(citation: Citation): string {
  const breadcrumb = (citation.section_path ?? []).filter(Boolean).join(' › ');
  return breadcrumb ? `${citation.document_title} › ${breadcrumb}` : citation.document_title;
}

interface MessageProps {
  role: 'user' | 'assistant';
  content: string;
  /** When true and content is empty, renders typing indicator */
  isStreaming?: boolean;
  /** RAG citations to display below the message */
  sources?: Citation[];
  /** Called when the user clicks a citation chip */
  onCitationClick?: (citation: Citation) => void;
  /** Current tool-call status during streaming (ephemeral progress indicator) */
  streamingStatus?: { tool: string; subject: string } | null;
  /** When true, render the "Report this answer" button below the message. */
  feedbackEnabled?: boolean;
  /** When true, the assistant message already has a feedback row — render
   *  the "Reported — being reviewed" badge instead of the button. */
  feedbackSubmitted?: boolean;
  /** Called when the user clicks "Report this answer". */
  onReportClick?: () => void;
}

// ── Typing indicator (3 pulsing dots) ────────────────────────────
function TypingIndicator() {
  return (
    <div style={{ display: 'flex', gap: 5, alignItems: 'center', padding: '2px 0' }}>
      <div className="typing-dot" />
      <div className="typing-dot" />
      <div className="typing-dot" />
    </div>
  );
}

// ── Source citations section ──────────────────────────────────────
// Two-tier citation render (issue #176): Tier 1 "Sources cited" (visible by
// default) when any chunk has is_cited=true; Tier 2 "All sources consulted"
// uses the existing toggle. Falls back to the legacy flat list when no chunk
// is marked (legacy data or model-forgot-markers fallback).
function citationChip(
  citation: Citation,
  i: number,
  onCitationClick: ((c: Citation) => void) | undefined,
  dimmed: boolean,
) {
  const label = citationLabel(citation);
  return (
    <button
      key={`${citation.chunk_id}-${i}`}
      onClick={() => onCitationClick?.(citation)}
      title={label}
      className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
      style={{
        display: 'inline-block',
        padding: '3px 10px',
        border: dimmed ? '1px solid var(--border-strong)' : '1px solid var(--accent)',
        borderRadius: 20,
        fontSize: 12,
        color: dimmed ? 'var(--text-secondary)' : 'var(--text-primary)',
        background: dimmed ? 'rgba(26,22,22,0.03)' : 'var(--accent-bg)',
        maxWidth: 280,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {label}
    </button>
  );
}

function SourceCitations({
  sources,
  onCitationClick,
}: {
  sources: Citation[];
  onCitationClick?: (citation: Citation) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!sources || sources.length === 0) return null;

  const cited = sources.filter((s) => s.is_cited === true);
  const consulted = sources.filter((s) => s.is_cited !== true);
  const showTwoTier = cited.length > 0;

  return (
    <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
      {showTwoTier && (
        <>
          <div style={{ color: 'var(--text-secondary)', fontSize: 12, marginBottom: 6 }}>
            Sources cited ({cited.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
            {cited.map((c, i) => citationChip(c, i, onCitationClick, false))}
          </div>
        </>
      )}

      {/* Toggle button (Tier 2 / legacy) */}
      {(!showTwoTier || consulted.length > 0) && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-secondary)',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            padding: 0,
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-secondary)')}
          aria-expanded={expanded}
          aria-label={expanded ? 'Collapse sources' : 'Expand sources'}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s',
            }}
          >
            <polyline points="4,2 8,6 4,10" />
          </svg>
          {showTwoTier
            ? `All sources consulted (${sources.length})`
            : `Sources (${sources.length})`}
        </button>
      )}

      {/* Citation chips */}
      {expanded && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {(showTwoTier ? consulted : sources).map((c, i) =>
            citationChip(c, i, onCitationClick, showTwoTier),
          )}
        </div>
      )}
    </div>
  );
}

// ── Feedback affordance (assistant messages only) ─────────────────
// Renders either the "Report this answer" button or, post-submit, the
// "Reported — being reviewed" badge. Lives *below* the markdown body so
// no-citation refusal answers can still be reported.
function FeedbackAction({
  feedbackEnabled,
  feedbackSubmitted,
  onReportClick,
}: {
  feedbackEnabled: boolean;
  feedbackSubmitted: boolean;
  onReportClick?: () => void;
}) {
  if (feedbackSubmitted) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)] mt-2">
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <polyline points="2.5,6.5 5,9 9.5,3" />
        </svg>
        Reported — being reviewed
      </span>
    );
  }
  if (!feedbackEnabled || !onReportClick) return null;
  return (
    <button
      type="button"
      onClick={onReportClick}
      className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] mt-2 cursor-pointer bg-transparent border-0 p-0 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M2.5,1.5 L2.5,10.5" />
        <path d="M2.5,2 C5,1 7,3 9.5,2 L9.5,6 C7,7 5,5 2.5,6 Z" />
      </svg>
      Report this answer
    </button>
  );
}

// ── Main message component ────────────────────────────────────────
export function Message({
  role,
  content,
  isStreaming,
  sources,
  onCitationClick,
  streamingStatus,
  feedbackEnabled,
  feedbackSubmitted,
  onReportClick,
}: MessageProps) {
  const isUser = role === 'user';
  const hasSources = !isUser && Array.isArray(sources) && sources.length > 0;
  // Suppress the report affordance while the assistant is still streaming
  // — the persisted message rerenders with the proper props once the
  // stream completes.
  const showFeedback = !isUser && !isStreaming && !!content;

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 4,
        padding: '2px 0',
      }}
    >
      <div
        style={{
          maxWidth: isUser ? '70%' : '80%',
          background: isUser ? 'var(--accent)' : 'var(--surface-1)',
          color: isUser ? '#ffffff' : 'var(--text-primary)',
          borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
          padding: '12px 16px',
          lineHeight: 1.7,
          wordBreak: 'break-word',
          border: isUser ? '1px solid var(--accent-dark)' : '1px solid var(--border)',
        }}
      >
        {isStreaming && !content ? (
          streamingStatus ? (
            <div className="text-[var(--text-secondary)] text-[13px] italic">
              {streamingStatus.subject ? `Searching: ${streamingStatus.subject}…` : 'Working…'}
            </div>
          ) : (
            <TypingIndicator />
          )
        ) : isUser ? (
          <span style={{ whiteSpace: 'pre-wrap' }}>{content}</span>
        ) : (
          <>
            <MarkdownRenderer content={content} />
            {hasSources && <SourceCitations sources={sources} onCitationClick={onCitationClick} />}
            {showFeedback && (
              <FeedbackAction
                feedbackEnabled={feedbackEnabled ?? false}
                feedbackSubmitted={feedbackSubmitted ?? false}
                onReportClick={onReportClick}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
