import { type MutableRefObject, type RefObject, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useConversations } from '../hooks/useConversations';
import { useToast } from '../hooks/useToast';
import { type Conversation, createConversation, deleteConversation } from '../lib/api';
import { DocumentExplorer } from './DocumentExplorer';

// ── Relative time helper ─────────────────────────────────────────
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHrs = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHrs / 24);

  if (diffSecs < 60) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHrs < 24) return `${diffHrs}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// ── Skeleton row ─────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
      <div className="skeleton" style={{ height: 14, width: '70%', marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 11, width: '40%', marginBottom: 6 }} />
      <div className="skeleton" style={{ height: 11, width: '90%' }} />
    </div>
  );
}

// ── Highlight matched substring in a title ──────────────────────
function highlightMatch(title: string, query: string) {
  const q = query.trim();
  if (!q) return title;
  const idx = title.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return title;
  return (
    <>
      {title.slice(0, idx)}
      <mark
        style={{
          background: 'var(--accent-bg-2)',
          color: 'inherit',
          padding: 0,
          borderRadius: 2,
        }}
      >
        {title.slice(idx, idx + q.length)}
      </mark>
      {title.slice(idx + q.length)}
    </>
  );
}

// ── Single conversation item ─────────────────────────────────────
interface ConvItemProps {
  conv: Conversation;
  isActive: boolean;
  searchQuery: string;
  onSelect: () => void;
  onDeleteRequest: (id: string) => void;
  onRename: (id: string, title: string) => void;
}

function ConvItem({
  conv,
  isActive,
  searchQuery,
  onSelect,
  onDeleteRequest,
  onRename,
}: ConvItemProps) {
  const [hovered, setHovered] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(conv.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const preview = conv.preview
    ? conv.preview.length > 80
      ? conv.preview.slice(0, 77) + '…'
      : conv.preview
    : null;

  const handleRenameCommit = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== conv.title) {
      onRename(conv.id, trimmed);
    }
    setEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleRenameCommit();
    if (e.key === 'Escape') {
      setEditing(false);
      setEditValue(conv.title);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === 'Enter' && !editing && onSelect()}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
      style={{
        position: 'relative',
        padding: '10px 16px',
        cursor: 'pointer',
        borderBottom: '1px solid var(--border)',
        background: isActive ? 'var(--surface-1)' : hovered ? 'rgba(26,22,22,0.05)' : 'transparent',
        borderLeft: isActive ? '3px solid var(--accent)' : '3px solid transparent',
        paddingLeft: 13,
        transition: 'background 0.15s, border-color 0.15s',
        userSelect: 'none',
      }}
    >
      {/* Title */}
      {editing ? (
        <input
          ref={inputRef}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleRenameCommit}
          onClick={(e) => e.stopPropagation()}
          style={{
            fontSize: 14,
            fontWeight: 500,
            color: 'var(--text-primary)',
            background: 'var(--bg)',
            border: '1px solid var(--accent)',
            borderRadius: 4,
            padding: '1px 6px',
            width: '100%',
            outline: 'none',
          }}
        />
      ) : (
        <div
          onDoubleClick={(e) => {
            e.stopPropagation();
            setEditing(true);
            setEditValue(conv.title);
          }}
          style={{
            fontSize: 14,
            fontWeight: 500,
            color: 'var(--text-primary)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            paddingRight: hovered ? 56 : 0,
          }}
        >
          {highlightMatch(conv.title, searchQuery)}
        </div>
      )}

      {/* Timestamp */}
      <div
        style={{
          fontSize: 12,
          color: 'var(--text-secondary)',
          marginTop: 2,
          marginBottom: preview ? 3 : 0,
        }}
      >
        {formatRelativeTime(conv.updated_at)}
      </div>

      {/* Preview */}
      {preview && (
        <div
          style={{
            fontSize: 12,
            color: 'var(--text-tertiary)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {preview}
        </div>
      )}

      {/* Pencil icon — visible on hover (rename) */}
      {hovered && !editing && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            setEditing(true);
            setEditValue(conv.title);
          }}
          aria-label="Rename conversation"
          title="Rename conversation"
          className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          style={{
            position: 'absolute',
            right: 30,
            top: '50%',
            transform: 'translateY(-50%)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-tertiary)',
            padding: 4,
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--accent)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-tertiary)')}
        >
          ✏️
        </button>
      )}

      {/* Delete button — visible on hover */}
      {hovered && !editing && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDeleteRequest(conv.id);
          }}
          title="Delete conversation"
          className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          style={{
            position: 'absolute',
            right: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-tertiary)',
            padding: 4,
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--danger)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-tertiary)')}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <polyline points="2,4 12,4" />
            <path d="M5,4V2.5a.5.5,0,0,1,.5-.5h3a.5.5,0,0,1,.5.5V4" />
            <path d="M3,4l.7,7.5a.5.5,0,0,0,.5.5h5.6a.5.5,0,0,0,.5-.5L11,4" />
          </svg>
        </button>
      )}
    </div>
  );
}

// ── Delete confirm dialog ─────────────────────────────────────────
interface ConfirmDialogProps {
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
  error: boolean;
}

function ConfirmDialog({ onConfirm, onCancel, deleting, error }: ConfirmDialogProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(26,22,22,0.45)',
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: 'var(--surface-1)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 24,
          width: 320,
          maxWidth: 'calc(100vw - 48px)',
          boxShadow: '0 25px 50px -12px rgba(26,22,22,0.18)',
        }}
      >
        <p style={{ margin: '0 0 8px', fontWeight: 600, color: 'var(--text-primary)' }}>
          Delete conversation?
        </p>
        <p style={{ margin: '0 0 20px', fontSize: 14, color: 'var(--text-secondary)' }}>
          This action cannot be undone.
        </p>
        {error && (
          <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--danger)' }}>
            Failed to delete. Please try again.
          </p>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 8,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '8px 16px',
              fontSize: 14,
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={deleting}
            className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              background: 'var(--danger)',
              border: 'none',
              borderRadius: 8,
              color: '#fff',
              cursor: deleting ? 'not-allowed' : 'pointer',
              padding: '8px 16px',
              fontSize: 14,
              opacity: deleting ? 0.7 : 1,
            }}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Daily quota counter ──────────────────────────────────────────
interface DailyQuotaCounterProps {
  used: number;
  remaining: number;
  resetsAt: string | null;
}

function DailyQuotaCounter({ used, remaining, resetsAt }: DailyQuotaCounterProps) {
  const cap = used + remaining;
  const atLimit = remaining === 0;
  const resetLabel = resetsAt
    ? new Date(resetsAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="quota-counter"
      style={{
        padding: '8px 12px',
        borderTop: '1px solid var(--border)',
        fontSize: 12,
        color: atLimit ? 'var(--danger)' : 'var(--text-secondary)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
      }}
    >
      <span>
        <strong
          style={{ color: atLimit ? 'var(--danger)' : 'var(--text-primary)', fontWeight: 600 }}
        >
          {used}/{cap}
        </strong>{' '}
        messages today
      </span>
      {atLimit && resetLabel && (
        <span style={{ fontSize: 11, opacity: 0.85 }}>resets at {resetLabel}</span>
      )}
    </div>
  );
}

// ── Main Sidebar component ───────────────────────────────────────
interface SidebarProps {
  activeConversationId?: string;
  isOpen: boolean;
  onClose: () => void;
  conversationsRef?: MutableRefObject<(() => Promise<void>) | null>;
}

export function Sidebar({ activeConversationId, isOpen, onClose, conversationsRef }: SidebarProps) {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const { conversations, loading, refetch, rename, filteredConversations } =
    useConversations(debouncedQuery);
  const { user, logout } = useAuth();
  const [creatingNew, setCreatingNew] = useState(false);
  const [newChatError, setNewChatError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const { addToast } = useToast();

  // Debounce search query — 250ms per issue #92
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Store refetch in the shared ref so ChatArea can trigger it
  useEffect(() => {
    if (conversationsRef) {
      conversationsRef.current = refetch;
    }
  }, [refetch, conversationsRef]);

  // ── New Chat ──
  const handleNewChat = async () => {
    // Guard: if already on an empty conversation, reuse it instead of creating a duplicate
    if (activeConversationId) {
      const activeConv = conversations.find((c) => c.id === activeConversationId);
      if (activeConv && !activeConv.preview) {
        // Already on an empty conversation — no-op, just close sidebar
        onClose();
        return;
      }
    }
    setCreatingNew(true);
    setNewChatError(null);
    try {
      const conv = await createConversation();
      await refetch();
      navigate(`/c/${conv.id}`);
      onClose();
    } catch (e) {
      setNewChatError('Could not create conversation. Please try again.');
    } finally {
      setCreatingNew(false);
    }
  };

  // ── Delete flow ──
  const handleDeleteRequest = (id: string) => {
    setConfirmId(id);
    setDeleteError(false);
  };

  const handleDeleteConfirm = async () => {
    if (!confirmId) return;
    setDeleting(true);
    setDeleteError(false);
    try {
      const res = await deleteConversation(confirmId);
      if (!res.ok && res.status !== 204) {
        throw new Error('Delete failed');
      }
      setConfirmId(null);
      await refetch();
      if (activeConversationId === confirmId) {
        navigate('/');
      }
    } catch (e) {
      console.error('[Sidebar] Delete conversation failed:', e);
      setDeleteError(true);
    } finally {
      setDeleting(false);
    }
  };

  const handleDeleteCancel = () => {
    setConfirmId(null);
    setDeleteError(false);
  };

  // ── Navigate to conversation ──
  const handleSelect = (id: string) => {
    navigate(`/c/${id}`);
    onClose();
  };

  // ── Logout ──
  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
      onClose();
      navigate('/login');
    } finally {
      setLoggingOut(false);
    }
  };

  // ── Rename ──
  const handleRename = async (id: string, title: string) => {
    const { ok, error } = await rename(id, title);
    if (!ok && error) {
      addToast(`Rename failed: ${error}`, 'error');
    }
  };

  return (
    <>
      <aside className={`sidebar-container${isOpen ? ' open' : ''}`}>
        {/* ── New Chat button ── */}
        <div style={{ padding: '12px 12px 8px' }}>
          <button
            onClick={handleNewChat}
            disabled={creatingNew}
            className="active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none focus-visible:shadow-[0_0_12px_var(--accent-glow)]"
            style={{
              width: '100%',
              background: 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 8,
              padding: '10px 16px',
              cursor: creatingNew ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              fontSize: 14,
              opacity: creatingNew ? 0.75 : 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              transition: 'background 0.15s, filter 0.15s',
            }}
            onMouseEnter={(e) =>
              !creatingNew && (e.currentTarget.style.background = 'var(--accent-dark)')
            }
            onMouseLeave={(e) => {
              if (!creatingNew) {
                e.currentTarget.style.background = 'var(--accent)';
              }
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            >
              <line x1="7" y1="2" x2="7" y2="12" />
              <line x1="2" y1="7" x2="12" y2="7" />
            </svg>
            {creatingNew ? 'Creating…' : 'New Chat'}
          </button>

          {newChatError && (
            <p
              style={{
                fontSize: 12,
                color: 'var(--danger)',
                margin: '8px 0 0',
                textAlign: 'center',
              }}
            >
              {newChatError}
            </p>
          )}
        </div>

        {/* ── Search conversations ── */}
        <div style={{ padding: '0 12px 8px' }}>
          <input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: 8,
              background: 'var(--surface-1)',
              border: '1px solid var(--border-strong)',
              color: 'var(--text-primary)',
              fontSize: 13,
              outline: 'none',
              transition: 'border-color 0.15s',
            }}
            onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
            onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border-strong)')}
          />
        </div>

        {/* ── Conversation list ── */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading ? (
            <>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </>
          ) : filteredConversations.length === 0 ? (
            // Empty state — distinct copy when the user is searching
            <div
              style={{
                padding: '40px 16px',
                textAlign: 'center',
                color: 'var(--text-tertiary)',
              }}
            >
              <svg
                width="36"
                height="36"
                viewBox="0 0 36 36"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                style={{ margin: '0 auto 12px', display: 'block', opacity: 0.5 }}
              >
                <path d="M6,4 L30,4 A2,2 0 0,1 32,6 L32,24 A2,2 0 0,1 30,26 L10,26 L4,32 L4,6 A2,2 0 0,1 6,4 Z" />
              </svg>
              {debouncedQuery.trim() ? (
                <p style={{ margin: 0, fontSize: 13 }}>
                  No matches for{' '}
                  <strong style={{ color: 'var(--text-secondary)' }}>"{debouncedQuery}"</strong>
                </p>
              ) : (
                <>
                  <p style={{ margin: 0, fontSize: 13 }}>No conversations yet</p>
                  <button
                    onClick={handleNewChat}
                    className="active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
                    style={{
                      marginTop: 10,
                      background: 'transparent',
                      border: '1px solid var(--accent)',
                      borderRadius: 8,
                      color: 'var(--accent)',
                      cursor: 'pointer',
                      fontSize: 13,
                      padding: '7px 16px',
                      transition: 'background 0.15s, filter 0.15s',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-bg)')}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    Start your first chat →
                  </button>
                </>
              )}
            </div>
          ) : (
            filteredConversations.map((conv) => (
              <ConvItem
                key={conv.id}
                conv={conv}
                isActive={conv.id === activeConversationId}
                searchQuery={debouncedQuery}
                onSelect={() => handleSelect(conv.id)}
                onDeleteRequest={handleDeleteRequest}
                onRename={handleRename}
              />
            ))
          )}
        </div>

        {/* ── Daily message quota counter (MISSION §10 #1: hardcoded 25/24h) ── */}
        {user && (
          <DailyQuotaCounter
            used={user.messages_used_today}
            remaining={user.messages_remaining_today}
            resetsAt={user.rate_window_resets_at}
          />
        )}

        {/* ── User identity + logout row ── */}
        {user && (
          <div
            style={{
              padding: '8px 12px',
              borderTop: '1px solid var(--border)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
            }}
          >
            <span
              title={user.email}
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                flex: 1,
                minWidth: 0,
              }}
            >
              {user.email}
            </span>
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              title="Log out"
              aria-label="Log out"
              className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              style={{
                background: 'transparent',
                border: '1px solid var(--border)',
                borderRadius: 7,
                color: 'var(--text-secondary)',
                cursor: loggingOut ? 'not-allowed' : 'pointer',
                padding: '5px 10px',
                fontSize: 12,
                opacity: loggingOut ? 0.6 : 1,
                transition: 'background 0.15s, color 0.15s, border-color 0.15s',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                if (loggingOut) return;
                e.currentTarget.style.background = 'var(--surface-1)';
                e.currentTarget.style.color = 'var(--text-primary)';
                e.currentTarget.style.borderColor = 'var(--danger)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = 'var(--text-secondary)';
                e.currentTarget.style.borderColor = 'var(--border)';
              }}
            >
              {loggingOut ? 'Signing out…' : 'Log out'}
            </button>
          </div>
        )}

        {/* ── Sidebar footer: branding + library button ── */}
        <div
          style={{
            padding: '10px 12px',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
          }}
        >
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>FirstSpirit Docs</span>

          {/* Library admin link — admin-only. is_admin is a server-computed
              hint only; the /api/admin/* endpoints re-verify on every call. */}
          {user?.is_admin && (
            <Link
              to="/admin"
              title="Manage video library"
              className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              style={{
                fontSize: 12,
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
                borderRadius: 7,
                padding: '5px 7px',
                textDecoration: 'none',
                transition: 'background 0.15s, color 0.15s, border-color 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--surface-1)';
                e.currentTarget.style.color = 'var(--text-primary)';
                e.currentTarget.style.borderColor = 'var(--accent)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = 'var(--text-secondary)';
                e.currentTarget.style.borderColor = 'var(--border)';
              }}
            >
              Admin
            </Link>
          )}

          {/* Library / DocumentExplorer button */}
          <button
            onClick={() => setExplorerOpen(true)}
            title="Browse document library"
            aria-label="Browse document library"
            className="focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 7,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '5px 7px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 5,
              fontSize: 12,
              transition: 'background 0.15s, color 0.15s, border-color 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--surface-1)';
              e.currentTarget.style.color = 'var(--text-primary)';
              e.currentTarget.style.borderColor = 'var(--accent)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = 'var(--text-secondary)';
              e.currentTarget.style.borderColor = 'var(--border)';
            }}
          >
            {/* Play/video icon */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="1" y="2" width="12" height="10" rx="2" />
              <polygon points="5,4.5 9.5,7 5,9.5" fill="currentColor" stroke="none" />
            </svg>
            Library
          </button>
        </div>
      </aside>

      {/* ── Confirm delete dialog ── */}
      {confirmId && (
        <ConfirmDialog
          onConfirm={handleDeleteConfirm}
          onCancel={handleDeleteCancel}
          deleting={deleting}
          error={deleteError}
        />
      )}

      {/* ── Video Explorer panel ── */}
      <DocumentExplorer isOpen={explorerOpen} onClose={() => setExplorerOpen(false)} />
    </>
  );
}
