/**
 * Typed fetch wrappers for the FirstSpirit Docs RAG API.
 */

const BASE = '/api';

/**
 * Thrown when POST /api/conversations/{id}/messages returns 429.
 * Carries the shape of the rate_limit_exceeded JSON body so the chat UI
 * can render a friendly "daily limit hit, resets at HH:MM" message.
 */
export class RateLimitError extends Error {
  limit: number;
  windowHours: number;
  resetAt: string;

  constructor(body: { limit: number; window_hours: number; reset_at: string }) {
    super('rate_limit_exceeded');
    this.limit = body.limit;
    this.windowHours = body.window_hours;
    this.resetAt = body.reset_at;
  }
}

export interface Document {
  id: string;
  title: string;
  description: string;
  url: string | null;
  content_path: string | null;
  source_type: string;
  lang: string | null;
  last_crawled_at: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  preview?: string | null;
}

export interface Citation {
  chunk_id: string;
  document_id: string;
  document_title: string;
  document_url: string | null;
  document_content_path: string | null;
  source_type: string;
  section_path: string[];
  anchor: string | null;
  content: string;
  chunk_index: number;
  /**
   * True when the LLM emitted a `[c:<chunk_id>]` marker referencing this
   * chunk in its final answer. Drives the two-tier "Sources cited" /
   * "All sources consulted" render.
   */
  is_cited?: boolean;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  /** RAG citations — only populated for freshly-streamed assistant messages */
  sources?: Citation[];
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

/**
 * Thrown by `request()` whenever a non-2xx response comes back. Carries the
 * status and parsed `detail` so callers can render friendly UI.
 */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`API error ${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    if (typeof parsed?.detail === 'string') return parsed.detail;
  } catch {
    // not JSON — keep text as-is
  }
  return text;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (res.status === 401) {
    if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
      const returnTo = window.location.pathname + window.location.search;
      window.location.assign(`/login?from=${encodeURIComponent(returnTo)}`);
    }
    throw new ApiError(401, 'Not authenticated');
  }
  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorDetail(res));
  }
  return res.json() as Promise<T>;
}

// Conversations
export const getConversations = () => request<Conversation[]>('/conversations');
export const searchConversations = (q: string) =>
  request<Conversation[]>(`/conversations/search?q=${encodeURIComponent(q)}`);
export const createConversation = () =>
  request<Conversation>('/conversations', { method: 'POST', body: '{}' });
export const getConversation = (id: string) =>
  request<ConversationWithMessages>(`/conversations/${id}`);
export const deleteConversation = (id: string) =>
  fetch(`${BASE}/conversations/${id}`, { method: 'DELETE', credentials: 'include' });
export const renameConversation = (id: string, title: string) =>
  request<Conversation>(`/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });

// Documents
export const getDocuments = () => request<Document[]>('/documents');

// Sources (admin: trigger ingest from url_list or vault)
export type SyncKind = 'url_list' | 'vault';

export interface SyncRequest {
  kind: SyncKind;
  source_type?: string;
}

export interface SyncResponse {
  sync_run_id: string;
  status: string;
  items_total: number;
  items_new: number;
  items_updated: number;
  items_unchanged: number;
  items_error: number;
}

export interface SyncRun {
  id: string;
  kind: SyncKind;
  status: string;
  started_at: string;
  finished_at: string | null;
  items_total: number;
  items_new: number;
  items_updated: number;
  items_unchanged: number;
  items_error: number;
}

export interface SyncRunsResponse {
  sync_runs: SyncRun[];
}

export interface AdminDocumentRow extends Document {
  chunk_count: number;
}

export interface AdminDocumentsResponse {
  documents: AdminDocumentRow[];
}

export const syncSources = (body: SyncRequest) =>
  request<SyncResponse>('/sources/sync', {
    method: 'POST',
    body: JSON.stringify(body),
  });

export const getSyncRuns = () => request<SyncRunsResponse>('/sources/sync-runs');

export const getSourceDocuments = () =>
  request<AdminDocumentsResponse>('/sources/documents');

// Health
export const getHealth = () =>
  request<{ status: string; document_count: number; chunk_count: number }>('/health');
