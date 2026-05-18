import { useEffect, useState } from 'react';
import { ApiError, type Citation, type Message, submitFeedback } from '../lib/api';

interface FeedbackModalProps {
  message: Message;
  prevUserMessage: Message;
  onClose: () => void;
  /** Invoked after a successful POST /api/feedback; receives the reported
   *  message id so the parent can flip its local feedback_submitted flag.
   */
  onSubmitted: (messageId: string) => void;
}

// Match FEEDBACK_MAX_CORRECTION_CHARS on the backend; out-of-sync values
// only impact client-side validation — the server still rejects with 422.
const MAX_CORRECTION_CHARS = 5000;
const MIN_CORRECTION_CHARS = 10;

function citationLine(c: Citation): string {
  const breadcrumb = (c.section_path ?? []).filter(Boolean).join(' › ');
  return breadcrumb ? `${c.document_title} › ${breadcrumb}` : c.document_title;
}

function citationHref(c: Citation): string | null {
  if (!c.document_url) return null;
  return c.anchor ? `${c.document_url}#${c.anchor}` : c.document_url;
}

export function FeedbackModal({
  message,
  prevUserMessage,
  onClose,
  onSubmitted,
}: FeedbackModalProps) {
  const [correction, setCorrection] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isValid = correction.trim().length >= MIN_CORRECTION_CHARS;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose, submitting]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  const handleSubmit = async () => {
    if (!isValid || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await submitFeedback({
        message_id: message.id,
        suggested_correction: correction,
      });
      onSubmitted(message.id);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : 'Failed';
      setError(msg);
      setSubmitting(false);
    }
  };

  const sources = message.sources ?? [];

  return (
    <div
      className="fixed inset-0 bg-[rgba(26,22,22,0.45)] z-50 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Report assistant answer"
    >
      <div
        className="bg-[var(--surface-1)] border border-[var(--border)] rounded-xl p-6 w-[720px] max-w-[calc(100vw-48px)] max-h-[min(720px,90vh)] flex flex-col gap-4 shadow-[var(--shadow-card)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-start gap-4">
          <div className="min-w-0">
            <h3 className="text-[var(--text-primary)] text-base font-semibold m-0">
              Report this answer
            </h3>
            <p className="text-[var(--text-secondary)] text-xs m-0 mt-0.5">
              Review what will be sent, then describe what the correct answer should be.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="bg-none border-none text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer text-xl leading-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Question panel */}
        <div className="min-h-0">
          <h4 className="text-[var(--text-secondary)] text-xs font-medium uppercase tracking-wide m-0 mb-1">
            User question
          </h4>
          <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words bg-[rgba(26,22,22,0.03)] border border-[var(--border)] rounded-md p-3 text-[var(--text-primary)] text-sm leading-relaxed font-sans m-0">
            {prevUserMessage.content}
          </pre>
        </div>

        {/* Answer panel */}
        <div className="min-h-0">
          <h4 className="text-[var(--text-secondary)] text-xs font-medium uppercase tracking-wide m-0 mb-1">
            Assistant answer
          </h4>
          <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words bg-[rgba(26,22,22,0.03)] border border-[var(--border)] rounded-md p-3 text-[var(--text-primary)] text-sm leading-relaxed font-sans m-0">
            {message.content}
          </pre>
        </div>

        {/* Citations */}
        <div className="min-h-0">
          <h4 className="text-[var(--text-secondary)] text-xs font-medium uppercase tracking-wide m-0 mb-1">
            Cited sources
          </h4>
          {sources.length === 0 ? (
            <p className="text-[var(--text-secondary)] text-sm m-0">No citations on this answer.</p>
          ) : (
            <ul className="m-0 pl-5 max-h-24 overflow-y-auto text-[var(--text-primary)] text-sm leading-relaxed">
              {sources.map((c) => {
                const href = citationHref(c);
                const label = citationLine(c);
                return (
                  <li key={c.chunk_id}>
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[var(--accent)] hover:underline"
                      >
                        {label}
                      </a>
                    ) : (
                      <span>{label}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Notice — must remain visible at ≥768px viewport height */}
        <div
          id="feedback-modal-notice"
          className="shrink-0 bg-[var(--accent-bg)] border border-[var(--accent)] text-[var(--text-primary)] p-3 rounded-md text-sm"
        >
          The information above will be sent to a public GitHub repository so the team can review
          and improve the answer. Do not include personal, confidential, or proprietary data in your
          correction.
        </div>

        {/* Correction */}
        <div className="shrink-0">
          <label
            htmlFor="feedback-modal-correction"
            className="block text-[var(--text-secondary)] text-xs font-medium uppercase tracking-wide mb-1"
          >
            Suggested correction
            <span className="text-[var(--text-tertiary)] normal-case ml-1 font-normal">
              (at least {MIN_CORRECTION_CHARS} characters)
            </span>
          </label>
          <textarea
            id="feedback-modal-correction"
            value={correction}
            onChange={(e) => setCorrection(e.target.value)}
            maxLength={MAX_CORRECTION_CHARS}
            rows={4}
            // biome-ignore lint/a11y/noAutofocus: modal is a focused interaction; autofocus is expected UX
            autoFocus
            aria-describedby="feedback-modal-notice"
            disabled={submitting}
            placeholder="Describe what the correct answer should be and, if possible, why."
            className="w-full resize-y bg-[var(--surface-1)] border border-[var(--border)] rounded-md p-3 text-[var(--text-primary)] text-sm font-sans focus:outline-none focus:border-[var(--accent)] disabled:opacity-50 disabled:cursor-not-allowed"
          />
        </div>

        {/* Error */}
        {error && (
          <p className="shrink-0 text-[var(--danger)] text-sm m-0" role="alert">
            {error}
          </p>
        )}

        {/* Footer */}
        <div className="shrink-0 flex justify-end gap-2 mt-1">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="bg-transparent border border-[var(--border)] text-[var(--text-primary)] rounded-md px-4 py-2 text-sm cursor-pointer hover:bg-[rgba(26,22,22,0.05)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!isValid || submitting}
            className="bg-[var(--accent)] border-none text-white rounded-md px-4 py-2 text-sm font-medium cursor-pointer hover:bg-[var(--accent-dark)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? 'Submitting…' : 'Submit'}
          </button>
        </div>
      </div>
    </div>
  );
}
