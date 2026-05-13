import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { type Document, type SyncKind, getDocuments, syncSources } from '../lib/api';

function highlightMatch(title: string, query: string): string | React.ReactElement {
  if (!title) return title;
  const q = query.trim();
  if (!q) return title;
  const idx = title.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return title;
  return (
    <>
      {title.slice(0, idx)}
      <mark className="bg-blue-500/35 text-inherit p-0 rounded-sm">
        {title.slice(idx, idx + q.length)}
      </mark>
      {title.slice(idx + q.length)}
    </>
  );
}

function SkeletonCard() {
  return (
    <div className="bg-slate-800 border border-white/10 rounded-lg p-3.5">
      <div className="skeleton h-3.5 w-3/5 mb-2.5" />
      <div className="skeleton h-2.5 w-9/10 mb-1.5" />
      <div className="skeleton h-2.5 w-3/4" />
    </div>
  );
}

function DocumentCard({ document, query = '' }: { document: Document; query?: string }) {
  const href = document.url ?? null;

  return (
    <div className="bg-slate-800 border border-white/10 rounded-lg p-3.5 transition-colors duration-150">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-semibold text-slate-100 mb-1.5 leading-tight no-underline hover:underline focus-visible:underline block"
        >
          {highlightMatch(document.title, query)}
        </a>
      ) : (
        <p className="text-sm font-semibold text-slate-100 mb-1.5 leading-tight">
          {highlightMatch(document.title, query)}
        </p>
      )}

      {document.description ? (
        <p className="text-xs text-slate-400 mb-2 leading-relaxed">
          {document.description.length > 140
            ? document.description.slice(0, 137) + '…'
            : document.description}
        </p>
      ) : null}

      <p className="text-[11px] text-slate-500 mb-0">
        {document.source_type}
        {document.lang ? ` · ${document.lang}` : ''}
      </p>
    </div>
  );
}

interface DocumentExplorerProps {
  isOpen: boolean;
  onClose: () => void;
}

export function DocumentExplorer({ isOpen, onClose }: DocumentExplorerProps) {
  const { user } = useAuth();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [syncKind, setSyncKind] = useState<SyncKind>('url_list');
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  const closeSync = () => {
    setSyncOpen(false);
    setSyncError(null);
    setSyncResult(null);
  };

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDocuments();
      setDocuments(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load documents');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSync = async () => {
    setSyncing(true);
    setSyncError(null);
    setSyncResult(null);
    try {
      const result = await syncSources({ kind: syncKind });
      setSyncResult(
        `${result.status}: ${result.items_new} new, ${result.items_updated} updated, ${result.items_unchanged} unchanged, ${result.items_error} errors`,
      );
      const refreshed = await getDocuments();
      setDocuments(refreshed);
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : 'Failed to sync sources.');
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => {
    if (isOpen && documents.length === 0 && !loading && !error) {
      fetchDocuments();
    }
  }, [isOpen, documents.length, loading, error, fetchDocuments]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    if (!isOpen) {
      setSearchQuery('');
      setDebouncedQuery('');
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  const q = debouncedQuery.trim().toLowerCase();
  const filteredDocuments = q
    ? documents.filter((d) =>
        [d.title, d.description, d.url, d.content_path]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(q),
      )
    : documents;

  return (
    <>
      {isOpen && <div onClick={onClose} className="fixed inset-0 bg-black/50 z-30" />}

      <div
        role="dialog"
        aria-label="Document Library"
        aria-modal="true"
        className="fixed top-0 right-0 h-full w-[380px] max-w-[90vw] bg-gray-900 border-l border-white/10 z-40 flex flex-col transition-transform duration-300 shadow-[-8px_0_32px_rgba(0,0,0,0.4)]"
        style={{ transform: isOpen ? 'translateX(0)' : 'translateX(100%)' }}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 flex-shrink-0">
          <div>
            <h2 className="m-0 text-base font-semibold text-slate-100">Document Library</h2>
            {!loading && documents.length > 0 && (
              <p className="mt-0.5 text-xs text-slate-400">
                {q
                  ? `${filteredDocuments.length} of ${documents.length} documents`
                  : `${documents.length} documents in knowledge base`}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close document library"
            className="bg-transparent border border-white/10 rounded-lg text-slate-400 cursor-pointer p-2 flex items-center justify-center transition-colors duration-150 mr-2 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="3" y1="3" x2="11" y2="11" />
              <line x1="11" y1="3" x2="3" y2="11" />
            </svg>
          </button>
          {user?.is_admin && (
            <button
              onClick={() => setSyncOpen(true)}
              className="px-3 py-1.5 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              title="Sync sources"
            >
              ↻ Sync
            </button>
          )}
        </div>

        {!loading && !error && documents.length > 0 && (
          <div className="px-5 py-3 border-b border-white/10 flex-shrink-0">
            <input
              type="search"
              placeholder="Search documents…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border outline-none focus:border-blue-500 transition-colors"
              aria-label="Search documents"
            />
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-2.5">
          {loading && (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
            </>
          )}

          {!loading && error && (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <p className="m-0 text-red-500 text-sm">Failed to load documents</p>
              <p className="m-0 text-slate-600 text-xs">{error}</p>
              <button
                onClick={fetchDocuments}
                className="bg-slate-800 border border-white/10 rounded-lg text-slate-100 cursor-pointer px-5 py-2 text-sm focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && documents.length === 0 && (
            <div className="py-8 text-center text-slate-500 text-sm">
              No documents in the knowledge base yet.
            </div>
          )}

          {!loading && !error && documents.length > 0 && filteredDocuments.length === 0 && (
            <div className="py-8 text-center text-slate-500 text-sm">
              No documents match &ldquo;{debouncedQuery}&rdquo;
            </div>
          )}

          {!loading &&
            !error &&
            filteredDocuments.map((doc) => (
              <DocumentCard key={doc.id} document={doc} query={debouncedQuery} />
            ))}
        </div>

        {syncOpen && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
            <div className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[420px] max-w-[calc(100vw-48px)] shadow-2xl">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-slate-100 text-base font-semibold m-0">Sync Sources</h3>
                <button
                  onClick={closeSync}
                  className="bg-none border-none text-slate-400 cursor-pointer text-lg focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
                >
                  ×
                </button>
              </div>
              {syncError && <p className="text-red-400 mb-3 text-sm">{syncError}</p>}
              {syncResult && <p className="text-emerald-400 mb-3 text-sm">{syncResult}</p>}
              <div className="mb-4">
                <label htmlFor="sync-kind" className="block text-slate-400 text-xs mb-1">
                  Source kind
                </label>
                <select
                  id="sync-kind"
                  value={syncKind}
                  onChange={(e) => setSyncKind(e.target.value as SyncKind)}
                  className="w-full p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border"
                  disabled={syncing}
                >
                  <option value="url_list">URL list (crawler)</option>
                  <option value="vault">Obsidian vault (markdown)</option>
                </select>
              </div>
              <div className="flex gap-2 justify-end mt-4">
                <button
                  onClick={closeSync}
                  className="px-4 py-2 bg-transparent border border-white/20 rounded-md text-slate-400 text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
                >
                  Close
                </button>
                <button
                  onClick={handleSync}
                  disabled={syncing}
                  className="px-4 py-2 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer disabled:opacity-75 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
                >
                  {syncing ? 'Syncing…' : 'Start Sync'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
