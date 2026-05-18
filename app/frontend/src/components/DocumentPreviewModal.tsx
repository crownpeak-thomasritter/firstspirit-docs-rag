import { useEffect } from 'react';
import type { Citation } from '../lib/api';

interface DocumentPreviewModalProps {
  citation: Citation;
  onClose: () => void;
}

function buildBreadcrumb(citation: Citation): string {
  const parts = [citation.document_title, ...(citation.section_path ?? [])].filter(Boolean);
  return parts.join(' › ');
}

function buildExternalUrl(citation: Citation): string | null {
  if (!citation.document_url) return null;
  return citation.anchor ? `${citation.document_url}#${citation.anchor}` : citation.document_url;
}

export function DocumentPreviewModal({ citation, onClose }: DocumentPreviewModalProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  const breadcrumb = buildBreadcrumb(citation);
  const externalUrl = buildExternalUrl(citation);

  return (
    <div
      className="fixed inset-0 bg-[rgba(26,22,22,0.45)] z-50 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Document citation"
    >
      <div
        className="bg-[var(--surface-1)] border border-[var(--border)] rounded-xl p-6 w-[720px] max-w-[calc(100vw-48px)] max-h-[90vh] flex flex-col shadow-[var(--shadow-card)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-4 gap-4">
          <div className="min-w-0">
            <h3 className="text-[var(--text-primary)] text-base font-semibold m-0 break-words">
              {citation.document_title}
            </h3>
            {citation.section_path?.length ? (
              <p className="text-[var(--text-secondary)] text-xs m-0 mt-0.5 break-words">
                {citation.section_path.join(' › ')}
              </p>
            ) : null}
          </div>
          <button
            onClick={onClose}
            className="bg-none border-none text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer text-xl leading-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="flex-1 min-h-0 mb-4 overflow-y-auto">
          <pre className="whitespace-pre-wrap break-words text-[var(--text-primary)] text-sm leading-relaxed font-sans m-0">
            {citation.content}
          </pre>
        </div>

        <div className="flex justify-between items-center gap-4">
          <span className="text-[var(--text-tertiary)] text-xs truncate" title={breadcrumb}>
            {breadcrumb}
          </span>
          {externalUrl ? (
            <a
              href={externalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--text-secondary)] hover:text-[var(--accent)] text-xs flex items-center gap-1 transition-colors shrink-0"
            >
              Open source
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M1 9L9 1M9 1H3M9 1v6" />
              </svg>
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
