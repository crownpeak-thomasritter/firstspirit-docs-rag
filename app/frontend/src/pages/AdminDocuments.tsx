import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useToast } from '../hooks/useToast';
import {
  type AdminDocumentRow,
  type SyncKind,
  type SyncRun,
  getSourceDocuments,
  getSyncRuns,
  syncSources,
} from '../lib/api';

export function AdminDocuments() {
  const { status, user } = useAuth();
  const { addToast } = useToast();
  const [documents, setDocuments] = useState<AdminDocumentRow[]>([]);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncKind, setSyncKind] = useState<SyncKind>('url_list');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const [docsResp, runsResp] = await Promise.all([getSourceDocuments(), getSyncRuns()]);
      setDocuments(docsResp.documents);
      setRuns(runsResp.runs);
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Failed to load library', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (status === 'authed' && user?.is_admin) {
      refetch();
    }
  }, [status, user?.is_admin, refetch]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(t);
  }, [searchQuery]);

  if (status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] text-[var(--text-secondary)]">
        Loading…
      </div>
    );
  }
  if (status === 'anon') {
    return <Navigate to="/login" replace state={{ from: '/admin' }} />;
  }
  if (!user?.is_admin) {
    return <Navigate to="/" replace />;
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await syncSources({ kind: syncKind });
      addToast(
        `Sync ${res.status}: ${res.items_new} new, ${res.items_updated} updated, ${res.items_unchanged} unchanged, ${res.items_error} errors`,
        res.status === 'completed' ? 'success' : 'error',
      );
      await refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Sync failed', 'error');
    } finally {
      setSyncing(false);
    }
  }

  const q = debouncedQuery.trim().toLowerCase();
  const filtered = q
    ? documents.filter((d) =>
        [d.title, d.description, d.url, d.content_path]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(q),
      )
    : documents;

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--text-primary)] p-6">
      <div className="max-w-6xl mx-auto">
        <header className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold">Library admin</h1>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Sync FirstSpirit docs from the configured URL list or Obsidian vault.
            </p>
          </div>
          <Link to="/" className="text-sm text-[var(--accent)] hover:underline">
            ← Back to chat
          </Link>
        </header>

        <div className="flex gap-2 mb-4 items-center">
          <select
            value={syncKind}
            onChange={(e) => setSyncKind(e.target.value as SyncKind)}
            disabled={syncing}
            className="px-3 py-2 rounded border border-[var(--border)] bg-[var(--surface-1)] text-[var(--text-primary)] text-sm"
          >
            <option value="url_list">URL list (crawler)</option>
            <option value="vault">Obsidian vault (markdown)</option>
          </select>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="px-3 py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50"
          >
            {syncing ? 'Syncing…' : 'Run sync'}
          </button>
          <button
            type="button"
            onClick={refetch}
            disabled={loading || syncing}
            className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        <input
          type="text"
          placeholder="Search documents..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full px-3 py-2 mb-3 rounded-lg bg-[var(--surface-1)] border border-[var(--border)] text-[var(--text-primary)] text-[13px] outline-none transition-colors focus:border-[var(--accent)]"
        />

        <div className="bg-[var(--surface-1)] border border-[var(--border)] rounded-lg overflow-hidden mb-8">
          {loading ? (
            <div className="p-6 text-center text-[var(--text-secondary)]">Loading…</div>
          ) : filtered.length === 0 ? (
            <div className="p-6 text-center text-[var(--text-secondary)]">
              {q
                ? `No matches for "${debouncedQuery}"`
                : 'No documents yet. Run a sync to populate the library.'}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-[var(--surface-2)] text-left">
                <tr>
                  <th className="px-4 py-2 font-medium">Title</th>
                  <th className="px-4 py-2 font-medium">Source</th>
                  <th className="px-4 py-2 font-medium">Chunks</th>
                  <th className="px-4 py-2 font-medium">Last crawled</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((d) => (
                  <tr key={d.id} className="border-t border-[var(--border)]">
                    <td className="px-4 py-2">
                      {d.url ? (
                        <a
                          href={d.url}
                          target="_blank"
                          rel="noreferrer"
                          className="hover:underline"
                        >
                          {d.title || '(untitled)'}
                        </a>
                      ) : (
                        <span>{d.title || '(untitled)'}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{d.source_type}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{d.chunk_count}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">
                      {d.last_crawled_at ? d.last_crawled_at.slice(0, 10) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <h2 className="text-lg font-semibold mb-3">Recent sync runs</h2>
        <div className="bg-[var(--surface-1)] border border-[var(--border)] rounded-lg overflow-hidden">
          {runs.length === 0 ? (
            <div className="p-6 text-center text-[var(--text-secondary)]">No sync runs yet.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-[var(--surface-2)] text-left">
                <tr>
                  <th className="px-4 py-2 font-medium">Started</th>
                  <th className="px-4 py-2 font-medium">Kind</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">New</th>
                  <th className="px-4 py-2 font-medium">Updated</th>
                  <th className="px-4 py-2 font-medium">Unchanged</th>
                  <th className="px-4 py-2 font-medium">Errors</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t border-[var(--border)]">
                    <td className="px-4 py-2 text-[var(--text-secondary)]">
                      {r.started_at.replace('T', ' ').slice(0, 19)}
                    </td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.kind}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.status}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.items_new}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.items_updated}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.items_unchanged}</td>
                    <td className="px-4 py-2 text-[var(--text-secondary)]">{r.items_error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
