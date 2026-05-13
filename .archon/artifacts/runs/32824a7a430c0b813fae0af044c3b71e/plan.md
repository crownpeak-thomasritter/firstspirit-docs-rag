# Feature: Migrate to Qdrant + SQLite, Add OpenAI Provider

## Summary

Replace the Postgres+pgvector+tsvector stack with Qdrant Cloud (dense + sparse vectors, server-side RRF via Query API) for retrieval, and SQLite+aiosqlite for chat/metadata persistence. Add OpenAI as a first-class provider alongside OpenRouter for both chat completions and embeddings, configurable independently. The route layer, ingest pipelines, SSE framing, citation handling, refusal detection, and per-document collapse behavior all stay externally identical — the swap is contained behind `rag/vector_store.py`, `db/sqlite.py`, and a new `llm/providers.py` abstraction.

## User Story

As a developer running the FirstSpirit Docs RAG app
I want to deploy with Qdrant Cloud + SQLite instead of a self-hosted Postgres, and pick OpenAI or OpenRouter per workload
So that I can drop the Postgres dependency (one fewer container, fewer ops chores), use a managed vector DB, and route to whichever LLM/embedding provider is cheaper or higher-quality for my use case without changing application code

## Problem Statement

The current stack ties three concerns to a single Postgres container: chat metadata, document metadata, vectors, and full-text search. This forces operators to provision and maintain Postgres + pgvector + a tsvector trigger, when 90% of the load on that DB is vector search that a purpose-built service handles natively. Simultaneously, OpenRouter is the only path for both chat and embeddings, blocking direct-to-OpenAI deployments where a customer has an existing OpenAI account/quota and wants to avoid the OpenRouter intermediary surcharge.

## Solution Statement

Three-axis decomposition with one clean abstraction per axis:

1. **Vectors**: New `rag/vector_store.py` wraps `AsyncQdrantClient`. Qdrant collection holds named vectors (`dense` 1536-dim cosine + `bm25` sparse), with chunk metadata as point payload so retrieval returns everything citations need in one round-trip. RRF fusion happens server-side via the Query API's `prefetch + fusion=rrf` flow with `k=60` semantics preserved. Drop in `retriever_hybrid.py` so callers see the same hit shape.

2. **Metadata**: SQLite via `aiosqlite` for `documents`, `conversations`, `messages`, `source_sync_runs`, `source_sync_items`. New `db/sqlite.py` exposes the same `_acquire()` context manager shape as `db/postgres.py` did. `db/repository.py` keeps its function signatures; internals swap `asyncpg` calls for `aiosqlite` (`$1` → `?` placeholders, `::jsonb` casts removed, `TIMESTAMPTZ` → ISO-8601 TEXT). Alembic targets a fresh single migration that mirrors today's tables minus the vestigial auth tables and minus the pgvector/tsvector machinery.

3. **Providers**: New `llm/providers.py` exposes `get_chat_client()` and `get_embedding_client()` returning `AsyncOpenAI` / `OpenAI` configured per env (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`). `llm/openrouter.py` renamed to `llm/chat.py`, its `stream_chat()` reads its client through the provider factory. The Anthropic-specific `cache_control` block is added only when the chat provider is OpenRouter (because Anthropic ignores/uses it via the OpenRouter shim). For native OpenAI, the cache block is omitted.

## Metadata

| Field | Value |
|-------|-------|
| Type | REFACTOR + NEW_CAPABILITY |
| Complexity | HIGH |
| Systems Affected | Database layer, RAG retrieval, LLM/embeddings, Alembic migrations, Docker Compose, tests, env config, docs |
| Dependencies | `qdrant-client>=1.12` (async), `aiosqlite>=0.20`, `fastembed>=0.4` (for BM25 sparse vectors). Remove `asyncpg`. Keep `openai` (already present). Keep `alembic` + `sqlalchemy` (added transitively for sync SQLite migration engine). |
| Estimated Tasks | 28 |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   docker compose up   →   ┌──────────────────┐   ┌──────────────────┐         ║
║                           │  postgres:pg16   │   │  fastapi/app     │         ║
║                           │  + pgvector      │◄──┤  asyncpg pool    │         ║
║                           │  + tsvector trig │   │  alembic upgrade │         ║
║                           └────────┬─────────┘   └────────┬─────────┘         ║
║                                    │ 5433/tcp             │ 8000              ║
║                                    └──────────────────────┘                   ║
║                                                                               ║
║   Chat → POST /api/conversations/{id}/messages                                ║
║          ↓                                                                    ║
║          embed query via OpenRouter ────────────► OpenRouter (only option)    ║
║          ↓                                                                    ║
║          repository.keyword_search()   (Postgres tsvector ts_rank)            ║
║          repository.vector_search_pg() (pgvector ::vector <=> ::vector)       ║
║          ↓                                                                    ║
║          RRF merge in Python (rag/retriever_hybrid.py)                        ║
║          ↓                                                                    ║
║          stream_chat() via OpenRouter SDK ──────► OpenRouter (only option)    ║
║          ↓                                                                    ║
║          SSE: data:<json>\n\n, event: sources, data:[DONE]                    ║
║                                                                               ║
║   PAIN_POINT: Postgres container required for vectors that a managed vector   ║
║   DB handles natively. OpenRouter is the only LLM path — customers with      ║
║   direct OpenAI accounts pay the OR surcharge or can't use this app at all.  ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   docker compose up   →   ┌──────────────────┐                                ║
║                           │  fastapi/app     │ ── volume: sqlite_data         ║
║                           │  aiosqlite       │ ── /app/data/app.db            ║
║                           │  alembic upgrade │                                ║
║                           └────────┬─────────┘                                ║
║                                    │ 8000                                     ║
║                                                                               ║
║   external services (cloud):                                                  ║
║      Qdrant Cloud      ◄── rag/vector_store.py (AsyncQdrantClient + HTTPS)    ║
║      OpenRouter or OpenAI ◄── llm/providers.py (factory by env LLM_PROVIDER)  ║
║                                                                               ║
║   Chat → POST /api/conversations/{id}/messages    (UNCHANGED route shape)     ║
║          ↓                                                                    ║
║          embed query via provider factory ────► OpenRouter OR OpenAI          ║
║          ↓                                                                    ║
║          vector_store.hybrid_search(query, embedding, top_k=5)                ║
║          ↓                                                                    ║
║          Qdrant Query API: prefetch dense + sparse (BM25), fusion='rrf'       ║
║          (server-side RRF k=60 — identical semantics, one round-trip)         ║
║          ↓                                                                    ║
║          stream_chat() via provider factory ──► OpenRouter OR OpenAI          ║
║          ↓                                                                    ║
║          SSE: data:<json>\n\n, event: sources, data:[DONE]  (UNCHANGED)       ║
║                                                                               ║
║   VALUE_ADD:                                                                  ║
║   1. One container instead of two; SQLite file persisted via named volume     ║
║   2. Qdrant managed → no pgvector / tsvector trigger maintenance              ║
║   3. Choose chat+embedding provider per workload (LLM_PROVIDER,               ║
║      EMBEDDING_PROVIDER env vars; can differ)                                 ║
║   4. Retrieval contract preserved: same hit shape, same RRF k=60, same        ║
║      top_k=5, same per-document diversity cap, same citation markers          ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `deploy/docker-compose.yml` | 2 services (postgres + app), `postgres_data` volume, `127.0.0.1:5433` exposed | 1 service (app), `sqlite_data` named volume mounted at `/app/data`, `QDRANT_URL`+`QDRANT_API_KEY` env passthrough | Single service to operate; no Postgres port binding to worry about |
| `.env.example` / `deploy/.env.example` | `DATABASE_URL=postgresql://...`, `POSTGRES_*` | `DATABASE_URL=sqlite+aiosqlite:///./data/app.db`, `QDRANT_URL`, `QDRANT_API_KEY`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` | Operators configure Qdrant Cloud + optional OpenAI keys |
| `POST /api/sources/sync` | Crawls + embeds + writes chunks to Postgres | Crawls + embeds + writes metadata to SQLite, upserts vectors+payload to Qdrant | Same response shape; ingestion now updates two stores transactionally per-document |
| `POST /api/conversations/{id}/messages` | Streams from OpenRouter only; retrieves via PG hybrid | Streams from configured provider; retrieves via Qdrant hybrid (server-side RRF) | Bytes-on-the-wire unchanged. Faster retrieval (one HTTPS round-trip vs two SQL queries) |
| `GET /api/health` | `count_documents()` + `count_chunks()` against Postgres | `count_documents()` against SQLite + chunk count via `vector_store.count()` (Qdrant `count` API) | Same JSON response shape |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `app/backend/db/repository.py` | 1-779 | Every SQL string + signature must be ported; ALL SQL access stays in this one file |
| P0 | `app/backend/rag/retriever_hybrid.py` | 1-186 | Exact RRF semantics, hit shape, document cache pattern — Qdrant must replicate |
| P0 | `app/backend/rag/tools.py` | 1-569 | Four tool executors call into retriever + repository — they're what the LLM sees |
| P0 | `app/backend/routes/messages.py` | 1-330 | SSE streaming contract, citation collapse, refusal detection — must be byte-identical post-migration |
| P0 | `app/backend/llm/openrouter.py` | 1-422 | Full `stream_chat` generator with tool-call loop, heartbeat, error handling — refactor target |
| P0 | `app/backend/alembic/versions/0001_initial.py` | 1-260 | Source-of-truth schema; the new SQLite migration mirrors this minus pgvector/tsvector/auth tables |
| P0 | `app/backend/config.py` | 1-142 | All env reads happen here — new Qdrant/OpenAI vars must be added here, nowhere else |
| P1 | `app/backend/db/postgres.py` | 1-65 | The shape of `init_pg_pool`/`close_pg_pool`/`get_pg_pool`/`_acquire` is what `db/sqlite.py` mirrors |
| P1 | `app/backend/rag/embeddings.py` | 1-115 | Embed signatures (`embed_text`, `embed_batch`) — these stay the same, internals swap |
| P1 | `app/backend/rag/expansion.py` | 1-145 | Neighbor expansion still uses `repository.get_chunk_neighbors` — works unchanged once repository is on SQLite |
| P1 | `app/backend/rag/catalog.py` | 1-85 | Catalog reads `list_documents()` — works unchanged once repository is on SQLite |
| P1 | `app/backend/main.py` | 1-200 | Lifespan handler runs `alembic upgrade head` then inits pool — both halves change |
| P1 | `app/backend/alembic/env.py` | 1-127 | Migration runner — must work for SQLite (sync) instead of Postgres (async) |
| P1 | `app/backend/ingest/url_list.py` | 1-243 | The chunk-insert pipeline — must dual-write to SQLite (metadata) + Qdrant (vectors) |
| P1 | `app/backend/ingest/vault.py` | 1-203 | Same pattern as url_list — dual-write |
| P1 | `app/backend/tests/conftest.py` | 1-25 | The test env-var bootstrap — needs SQLite URL + Qdrant/OpenAI dummy keys |
| P2 | `app/backend/tests/test_rag_tools.py` | 1-end | Monkeypatch pattern for retriever/embedder/repository — extend for Qdrant client |
| P2 | `app/backend/tests/test_ingest_url_list.py` | 1-end | `_FakeRepo` pattern — extend with a `_FakeVectorStore` |
| P2 | `deploy/docker-compose.yml` | 1-78 | Strip postgres service, add sqlite_data volume + Qdrant/OpenAI env passthrough |
| P2 | `deploy/Dockerfile` | 1-50 | No structural change; just ensure `/app/data` is writable by the `app` user |

**External Documentation:**

| Source | Section | Why Needed |
|--------|---------|------------|
| [qdrant-client Python docs — async usage](https://python-client.qdrant.tech/qdrant_client.async_qdrant_client) | `AsyncQdrantClient.query_points` | Async client API; `query_points` is the Query API entry point for hybrid search |
| [Qdrant — Hybrid Queries (Query API)](https://qdrant.tech/documentation/concepts/hybrid-queries/) | "Fusion: RRF" + "Prefetch" | Server-side RRF over named dense+sparse vectors. Confirms `k=60` is built-in |
| [Qdrant — Sparse Vectors](https://qdrant.tech/documentation/concepts/vectors/#sparse-vectors) | "BM25 with FastEmbed" | Configure sparse vector with FastEmbed's `Qdrant/bm25` model on indexing + query |
| [Qdrant — Cloud auth](https://qdrant.tech/documentation/cloud/authentication/) | API key headers | `api_key` arg on `AsyncQdrantClient(url=..., api_key=...)` |
| [aiosqlite docs](https://aiosqlite.omnilib.dev/) | "Connections" + "Transactions" | Async API mirrors stdlib `sqlite3`; supports `async with conn.execute(sql, params)`. **`?` placeholders, not `$1`** |
| [Alembic — SQLite limitations + batch mode](https://alembic.sqlalchemy.org/en/latest/batch.html) | "Batch operations" | Future ALTER TABLE migrations on SQLite need batch mode; not needed for the initial migration |
| [OpenAI Python SDK — streaming + tools](https://github.com/openai/openai-python#streaming-helpers) | `client.chat.completions.create(stream=True, tools=...)` | Same shape as the OpenRouter usage today — provider switch is just a different `base_url`/`api_key` |
| [SQLAlchemy 2.0 — aiosqlite URL form](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#module-sqlalchemy.dialects.sqlite.aiosqlite) | URL: `sqlite+aiosqlite:///./path.db` | Alembic env.py's URL normalization step needs to know the aiosqlite dialect prefix |

---

## Patterns to Mirror

**NAMING_CONVENTION (provider-style modules):**
```python
# SOURCE: app/backend/db/postgres.py:1-65
# COPY THIS PATTERN — module exposes init_*, close_*, get_*, _acquire():

_pool: asyncpg.Pool | None = None

async def init_pg_pool() -> asyncpg.Pool: ...
async def close_pg_pool() -> None: ...
def get_pg_pool() -> asyncpg.Pool: ...
```

**REPOSITORY FUNCTION SHAPE:**
```python
# SOURCE: app/backend/db/repository.py:108-111
# COPY THIS PATTERN — every public function uses _acquire() context manager:

async def get_document(document_id: str) -> dict | None:
    async with _acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM documents WHERE id = $1", document_id)
    return _hydrate_document(row) if row else None
```

**ERROR HANDLING — RuntimeError wrapping for external services:**
```python
# SOURCE: app/backend/rag/embeddings.py:62-70
# COPY THIS PATTERN — log + wrap as RuntimeError, no silent fallback:

try:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
except Exception as exc:
    logger.error("OpenRouter embeddings API call failed: %s", exc)
    raise RuntimeError(f"Embeddings API request failed: {exc}") from exc
```

**LOGGING_PATTERN — module-level logger:**
```python
# SOURCE: app/backend/rag/retriever_hybrid.py:30
# COPY THIS PATTERN:

import logging
logger = logging.getLogger(__name__)
```

**CONFIG_PATTERN — env reads only in config.py:**
```python
# SOURCE: app/backend/config.py:39-49
# COPY THIS PATTERN — `os.environ.get` only in config.py, downstream imports the constant:

OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
LLM_TOOLS_ENABLED: bool = os.environ.get("LLM_TOOLS_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
```

**TEST_STRUCTURE — monkeypatch external boundaries:**
```python
# SOURCE: app/backend/tests/test_rag_tools.py
# COPY THIS PATTERN — monkeypatch the boundary, not the SUT internals:

async def test_execute_search_hybrid_happy_path(monkeypatch):
    async def fake_retrieve(query, embedding, top_k, allowed_source_types=None):
        return [{"chunk_id": "chunk-1", ...}]
    monkeypatch.setattr(
        "backend.rag.retriever_hybrid.retrieve_hybrid", fake_retrieve, raising=False
    )
```

**INGEST FAKE_REPO — in-memory stand-in for repository:**
```python
# SOURCE: app/backend/tests/test_ingest_url_list.py
# COPY THIS PATTERN — in-memory fake that captures writes:

class _FakeRepo:
    def __init__(self) -> None:
        self.documents_by_url: dict[str, dict] = {}
        self.replaced_chunks: dict[str, list[dict]] = {}
    async def create_document(self, *, title, url, ...) -> dict: ...
    async def replace_chunks_for_document(self, document_id, payload, *, source_type): ...
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `app/backend/pyproject.toml` | UPDATE | Remove `asyncpg`, add `qdrant-client[fastembed]`, `aiosqlite`. Keep `openai`. |
| `app/backend/uv.lock` | REGEN | `uv lock` after pyproject change |
| `app/backend/config.py` | UPDATE | Add `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `EMBEDDING_MODEL` (now env-configurable) |
| `app/backend/db/postgres.py` | DELETE | Replaced by `db/sqlite.py` |
| `app/backend/db/sqlite.py` | CREATE | aiosqlite connection pool/handle; mirrors `init_pg_pool/close_pg_pool/get_pg_pool/_acquire` shape |
| `app/backend/db/repository.py` | REWRITE | All SQL converted to aiosqlite (`?` placeholders, no `::jsonb`, no `RETURNING`, no `ANY($::text[])`). Remove `keyword_search` and `vector_search_pg`. |
| `app/backend/db/signup_attempts_repo.py` | DELETE | Vestigial, never imported |
| `app/backend/db/user_messages_repo.py` | DELETE | Vestigial, never imported |
| `app/backend/alembic/versions/0001_initial.py` | REWRITE | Single SQLite-compatible initial migration; drop `users`/`user_messages`/`signup_attempts`/pgvector/tsvector/CREATE EXTENSION. Use TEXT (ISO-8601) for timestamps, TEXT for JSON columns. |
| `app/backend/alembic/env.py` | UPDATE | Use sync SQLAlchemy engine for SQLite migrations (simpler than async; aiosqlite at runtime is enough) |
| `app/backend/alembic.ini` | NO CHANGE | `env(DATABASE_URL)` form already supported by env.py |
| `app/backend/main.py` | UPDATE | Lifespan: `init_sqlite_pool()` + `await vector_store.ensure_collection()` on startup; close both on shutdown. |
| `app/backend/llm/openrouter.py` | RENAME → `llm/chat.py` | Provider-agnostic streaming wrapper. Client construction goes via `llm/providers.py`. Cache control block applied only for OpenRouter. |
| `app/backend/llm/providers.py` | CREATE | `get_chat_client()` / `get_embedding_client()` factories. Reads `LLM_PROVIDER` / `EMBEDDING_PROVIDER` from config. Returns `AsyncOpenAI` / `OpenAI` configured for the chosen provider. |
| `app/backend/rag/embeddings.py` | UPDATE | Use `get_embedding_client()` instead of constructing `OpenAI` directly. Read `EMBEDDING_MODEL` per-provider. |
| `app/backend/rag/vector_store.py` | CREATE | `AsyncQdrantClient` wrapper. Methods: `ensure_collection`, `upsert_chunks(document_id, chunks)`, `delete_document(document_id)`, `hybrid_search(query_text, query_embedding, top_k, allowed_source_types)`, `count()`. RRF fusion via Qdrant Query API. |
| `app/backend/rag/retriever_hybrid.py` | REWRITE | Delegate to `vector_store.hybrid_search()`. Keep public function `retrieve_hybrid()` and `invalidate_cache()` so callers don't change. Remove `_rrf_merge` (server-side now). |
| `app/backend/rag/tools.py` | UPDATE | `execute_search_keyword` and `execute_search_semantic` call new `vector_store.keyword_search()` / `vector_store.semantic_search()` instead of removed `repository.keyword_search` / `repository.vector_search_pg`. Tool schemas unchanged. |
| `app/backend/routes/messages.py` | UPDATE | Replace `from backend.llm.openrouter import stream_chat` with `from backend.llm.chat import stream_chat`. No behavior change. |
| `app/backend/routes/sources.py` | UPDATE | After ingest, also call `vector_store.invalidate_cache()` is not needed — the retriever cache is metadata-only, untouched. But add `await vector_store.delete_document(doc_id)` cleanup for soft-deleted docs (future). For MVP: no-op. |
| `app/backend/ingest/url_list.py` | UPDATE | After `replace_chunks_for_document` succeeds, call `vector_store.upsert_chunks(doc_id, chunks_with_embeddings)`. On error: delete already-upserted vectors. |
| `app/backend/ingest/vault.py` | UPDATE | Same pattern as url_list — vectors + metadata are co-written |
| `app/backend/services/extractor.py` | NO CHANGE | Pure transform, no DB |
| `deploy/docker-compose.yml` | REWRITE | Drop `postgres` service + `postgres_data` volume. Add `sqlite_data` volume mounted at `/app/data`. Pass through `QDRANT_URL`, `QDRANT_API_KEY`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`. `DATABASE_URL=sqlite+aiosqlite:////app/data/app.db`. |
| `deploy/Dockerfile` | UPDATE | Ensure `/app/data` directory exists and is owned by `app:app` (one new line; already partially present) |
| `deploy/.env.example` | UPDATE | Remove `POSTGRES_*`. Add Qdrant/OpenAI vars with placeholder values. |
| `.env.example` (root) | UPDATE | Same as deploy/.env.example for manual dev |
| `README.md` | UPDATE | Tech stack section: SQLite + Qdrant instead of Postgres+pgvector. Quick-start: drop POSTGRES_PASSWORD, add QDRANT_API_KEY. |
| `CLAUDE.md` | UPDATE | Tech stack section (line 17-40), DB section (170-185), env vars table (220-258). |
| `app/backend/tests/conftest.py` | UPDATE | Set `DATABASE_URL=sqlite+aiosqlite:///:memory:`, `QDRANT_URL=http://test`, `QDRANT_API_KEY=test`, `LLM_PROVIDER=openrouter`, `EMBEDDING_PROVIDER=openrouter`, dummy `OPENAI_API_KEY` |
| `app/backend/tests/test_rag_tools.py` | UPDATE | Monkeypatch `vector_store.hybrid_search` etc. instead of `repository.keyword_search` |
| `app/backend/tests/test_ingest_url_list.py` | UPDATE | Add `_FakeVectorStore` capturing `upsert_chunks` calls; assert vectors written |
| `app/backend/tests/test_ingest_vault.py` | UPDATE | Same as test_ingest_url_list |
| `app/backend/tests/test_routes_sources.py` | UPDATE | Mock `vector_store` if needed; cache invalidation now hits Qdrant retriever cache, which is just metadata cache so no change |
| `app/backend/tests/test_vector_store.py` | CREATE | Tests the Qdrant wrapper. Mock `AsyncQdrantClient` via monkeypatch. Verify `query_points` invoked with `prefetch=[...dense, ...sparse]` + `fusion=RRF` |
| `app/backend/tests/test_llm_providers.py` | CREATE | Tests provider factory returns the right base_url/api_key for OpenRouter vs OpenAI. Verify `stream_chat` works against a mocked OpenAI native AsyncOpenAI client (no `cache_control` block in system message). |
| `app/backend/tests/test_repository_sqlite.py` | CREATE (or rename existing test_repository.py if exists) | Smoke test: CREATE+SELECT against an in-memory SQLite — proves the rewritten repository works end-to-end. |
| `app/backend/tests/test_retriever_hybrid.py` | CREATE | Verify `retrieve_hybrid()` delegates to `vector_store.hybrid_search()` with correct args and returns the canonical hit shape. |
| `scripts/migrate_pg_to_qdrant.py` | CREATE | One-shot CLI: reads chunks from a Postgres DSN (asyncpg one-off, not via repository), upserts dense vectors + payloads to Qdrant. Re-derives BM25 sparse vectors via FastEmbed locally. |
| `.gitignore` | UPDATE | Already ignores `app/backend/data/*.db` and `*.db-*` — no change needed |

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No new auth layer.** `DEFAULT_USER_ID = "default-user"` stays. Vestigial auth tables (`users`, `user_messages`, `signup_attempts`) are dropped from the schema entirely.
- **No SQLAlchemy ORM at runtime.** Alembic uses SQLAlchemy under the hood (sync engine for migrations) but `repository.py` stays raw SQL via `aiosqlite`. Same contract as before: all SQL in one file.
- **No automatic Postgres → SQLite/Qdrant migration at runtime.** Existing Postgres installs use the one-shot `scripts/migrate_pg_to_qdrant.py` CLI script offline. The app's startup lifespan does not detect or attempt to read from Postgres.
- **No multi-tenant collection support in Qdrant.** Single collection (`firstspirit_docs`) with `source_type` as a payload filter — mirrors today's single-tenant model.
- **No FastEmbed-based dense embeddings.** Dense vectors continue to come from OpenAI/OpenRouter (`text-embedding-3-small`, 1536-dim). FastEmbed is used ONLY for the BM25 sparse vector path.
- **No prompt-cache support on OpenAI native provider.** OpenAI's auto-prompt-cache works without `cache_control` blocks (transparent). Anthropic-specific `cache_control` blocks are stripped when `LLM_PROVIDER=openai`. If OpenAI introduces an explicit cache primitive later, that's a follow-up.
- **No retrieval cache.** The metadata cache in `retriever_hybrid._document_cache` was a workaround for Postgres `get_document` overhead; Qdrant payloads now carry the metadata so this cache is unnecessary and is removed.
- **No CI workflow.** Tests run locally only (matching current state — no `.github/workflows/`).
- **No frontend changes.** Frontend has no DB or LLM provider awareness; it consumes `/api/*` only.
- **No reranker, no re-embedding strategy change.** Same chunker (Docling HybridChunker, cl100k_base, 512 tokens), same dense model, same RRF k=60.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable. Validation commands assume `cd app/backend` unless otherwise stated.

### Task 1: UPDATE `app/backend/pyproject.toml`

- **ACTION**: UPDATE existing file
- **IMPLEMENT**: Remove `asyncpg` from `[project.dependencies]`. Add `qdrant-client[fastembed]>=1.12`, `aiosqlite>=0.20`, `sqlalchemy>=2.0` (for Alembic). Keep everything else.
- **MIRROR**: existing dependency list pattern in `pyproject.toml:7-22`
- **IMPORTS**: N/A
- **GOTCHA**: `qdrant-client[fastembed]` extra pulls fastembed+onnxruntime, ~150 MB wheel. Pin lower bound only; uv.lock fixes upper.
- **VALIDATE**: `uv lock && uv sync --all-extras && uv run python -c "from qdrant_client import AsyncQdrantClient; import aiosqlite; import fastembed; print('OK')"` — must print OK

### Task 2: UPDATE `app/backend/config.py`

- **ACTION**: UPDATE existing file
- **IMPLEMENT**: After the LLM section (line 49), add:
  - `LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "openrouter").strip().lower()` — values: `openrouter` | `openai`
  - `EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "openrouter").strip().lower()`
  - `OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")`
  - `OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")`
  - Change `EMBEDDING_MODEL` to `os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-small")` — when `EMBEDDING_PROVIDER=openai`, the default `"openai/text-embedding-3-small"` becomes `"text-embedding-3-small"` (resolved in providers.py by stripping the `openai/` prefix for OpenAI native).
  - After Persistence section (line 58), add:
    - `QDRANT_URL: str = os.environ.get("QDRANT_URL", "")`
    - `QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")`
    - `QDRANT_COLLECTION: str = os.environ.get("QDRANT_COLLECTION", "firstspirit_docs")`
    - `QDRANT_DENSE_VECTOR_NAME: str = "dense"`
    - `QDRANT_SPARSE_VECTOR_NAME: str = "bm25"`
    - `QDRANT_BM25_MODEL: str = os.environ.get("QDRANT_BM25_MODEL", "Qdrant/bm25")`
    - `EMBEDDING_DIM: int = 1536`
- **MIRROR**: `app/backend/config.py:39-49` (LLM section pattern)
- **IMPORTS**: no new imports needed
- **GOTCHA**: Don't `raise` on missing `QDRANT_URL` at import time — `main.py` should be the gate (mirrors how `DATABASE_URL` is handled today).
- **VALIDATE**: `uv run python -c "from backend.config import QDRANT_URL, QDRANT_API_KEY, LLM_PROVIDER, EMBEDDING_PROVIDER, OPENAI_API_KEY; print('OK')"`

### Task 3: CREATE `app/backend/db/sqlite.py`

- **ACTION**: CREATE
- **IMPLEMENT**: aiosqlite-backed connection module exposing:
  ```python
  _db_path: str | None = None
  async def init_sqlite_db() -> None: ...        # reads DATABASE_URL, derives path, runs PRAGMA foreign_keys=ON
  async def close_sqlite_db() -> None: ...       # no-op (connections are per-call)
  def get_db_path() -> str: ...                  # returns the path or raises RuntimeError if not initialised
  def _acquire() -> AsyncContextManager[aiosqlite.Connection]: ...
  ```
  `_acquire()` returns an async context manager that opens a fresh `aiosqlite.Connection`, sets `PRAGMA foreign_keys = ON`, sets `row_factory = aiosqlite.Row`, yields it, and closes on exit. (Per-call connection is acceptable for single-tenant, low-QPS workload; avoids needing a pool.)
- **MIRROR**: `app/backend/db/postgres.py:1-65` — same public surface (`init_*`, `close_*`, `_acquire`)
- **IMPORTS**: `aiosqlite`, `contextlib.asynccontextmanager`, `urllib.parse.urlparse`
- **GOTCHA**: `DATABASE_URL` will be `sqlite+aiosqlite:///./data/app.db` or `sqlite+aiosqlite:///:memory:` — strip the dialect prefix to get the file path. Use `:memory:` literal pass-through for tests.
- **VALIDATE**: `uv run python -c "import asyncio; from backend.db.sqlite import init_sqlite_db, _acquire; import os; os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'; asyncio.run((lambda: __import__('asyncio').get_event_loop().run_until_complete(init_sqlite_db()))()); print('OK')"`

### Task 4: REWRITE `app/backend/alembic/versions/0001_initial.py`

- **ACTION**: REWRITE existing file (replace contents — keep filename + revision id `"0001"`)
- **IMPLEMENT**: SQLite-compatible DDL only:
  - **DROP** all `CREATE EXTENSION` lines (citext, pgcrypto, vector)
  - **DROP** `users`, `user_messages`, `signup_attempts` tables entirely
  - For each remaining table (`documents`, `document_chunks`, `conversations`, `messages`, `source_sync_runs`, `source_sync_items`):
    - `UUID` → `TEXT`
    - `TIMESTAMPTZ` → `TEXT` (ISO-8601)
    - `JSONB` → `TEXT` (JSON-encoded string)
    - `INET` → not used after auth-table removal
    - `CITEXT` → not used after auth-table removal
    - `DEFAULT now()` → `DEFAULT (CURRENT_TIMESTAMP)`
    - `DEFAULT gen_random_uuid()` → drop default; IDs always supplied by application via `_new_id()`
  - **DROP** from `document_chunks`: the `search_vector tsvector GENERATED ALWAYS AS (...)` column AND its `USING GIN(...)` index. Chunks no longer carry an `embedding` column either — vectors live in Qdrant. So the SQLite `document_chunks` table is: `id, document_id, content, chunk_index, section_path TEXT, anchor, char_start, char_end, source_type` (no `embedding`, no `search_vector`).
  - Keep `documents`, `conversations`, `messages`, `source_sync_runs`, `source_sync_items` columns intact (modulo type conversions).
  - Keep all CHECK constraints (`role IN ('user','assistant')`, etc.) — SQLite supports them.
  - Keep `ON DELETE CASCADE` — SQLite supports it (with `PRAGMA foreign_keys = ON`, set in `db/sqlite.py`).
  - Keep all indexes (translate `USING GIN(...)` ones away; the remaining ones are plain B-tree).
- **MIRROR**: existing file structure (`upgrade()` / `downgrade()` functions, `op.execute(...)` string-SQL pattern)
- **IMPORTS**: same as before (`from alembic import op`)
- **GOTCHA**: Partial indexes `CREATE UNIQUE INDEX ... WHERE url IS NOT NULL` — SQLite supports these from 3.8.0+; keep verbatim.
- **VALIDATE**: `cd app && uv --project backend run alembic --config backend/alembic.ini upgrade head` (with `DATABASE_URL=sqlite+aiosqlite:///./data/app.db` in env) — must exit 0 and create the file. Then `sqlite3 app/backend/data/app.db ".schema"` should show 6 tables.

### Task 5: UPDATE `app/backend/alembic/env.py`

- **ACTION**: REWRITE the migration runner section
- **IMPLEMENT**: Switch to a **sync** SQLAlchemy engine for migrations. The runtime app uses aiosqlite (async); migrations don't need to be async, and using a sync engine simplifies Alembic massively. Logic:
  ```python
  url = get_database_url()  # already exists; reads DATABASE_URL env
  # Normalize: sqlite:// → sqlite+pysqlite:// (sync), strip async prefix.
  if url.startswith("sqlite+aiosqlite://"):
      url = "sqlite+pysqlite://" + url[len("sqlite+aiosqlite://"):]
  elif url.startswith("postgresql://"):
      url = "postgresql+psycopg2://" + url[len("postgresql://") :]  # only used by the migration script
  engine = create_engine(url, poolclass=pool.NullPool, future=True)
  with engine.connect() as connection:
      context.configure(connection=connection, target_metadata=None)
      with context.begin_transaction():
          context.run_migrations()
  ```
  Delete the old `run_async_migrations()` and its `create_async_engine` invocation.
- **MIRROR**: existing `do_run_migrations()` body inside the sync flow — that's `context.run_migrations()`
- **IMPORTS**: `from sqlalchemy import create_engine, pool` (sync) — replaces `create_async_engine`
- **GOTCHA**: `sqlalchemy` is a new top-level dep — must be in pyproject.toml (Task 1 added it). `psycopg2` is only needed if running migrations against an existing Postgres DB for the export-script path; for the typical SQLite path, only `pysqlite` (built-in to Python) is used.
- **VALIDATE**: Same command as Task 4 — `uv --project backend run alembic ... upgrade head` succeeds against sqlite URL.

### Task 6: REWRITE `app/backend/db/repository.py`

- **ACTION**: REWRITE (large but mechanical — keep public function signatures + behavior)
- **IMPLEMENT**: Convert every function from asyncpg to aiosqlite. Specifically:
  - Replace `import asyncpg` → no replacement (use only aiosqlite types). Replace `from backend.db.postgres import get_pg_pool` → `from backend.db.sqlite import _acquire as _acquire_sqlite` and re-export as `_acquire`.
  - `$1, $2, $3 ...` placeholders → `?, ?, ?` (positional)
  - `conn.fetch(sql, *params)` → `async with conn.execute(sql, params) as cur: rows = await cur.fetchall()`; same for `conn.fetchrow` → `fetchone`, and `conn.execute(sql, *params)` returns nothing useful — for UPDATE/DELETE row counts use `cur.rowcount` (NOT the asyncpg-style `"UPDATE 0"` string check).
  - `conn.fetchval(...)` → `cur.fetchone()[0]`
  - `::jsonb` casts → drop the cast entirely; pass JSON-encoded string into a TEXT column
  - `::vector` casts → DELETE the function (`vector_search_pg` is removed)
  - `WHERE source_type = ANY($3::text[])` → `WHERE source_type IN (<placeholder list>)` — generate placeholders dynamically: `placeholders = ",".join("?" * len(allowed_source_types))`; then `WHERE source_type IN ({placeholders})`. This is the ONE place where the SQL is interpolated — it's safe because the interpolated content is only `?` characters.
  - `to_tsvector` / `plainto_tsquery` / `ts_rank` / `@@` → **REMOVE** the `keyword_search` function entirely; vector store handles it.
  - Remove `vector_search_pg` entirely.
  - `DELETE FROM ... ; result = ...; return result != "DELETE 0"` → `cur = await conn.execute("DELETE ...", params); await conn.commit(); return cur.rowcount > 0`
  - **Don't forget `await conn.commit()`** after every INSERT/UPDATE/DELETE — aiosqlite does NOT autocommit (asyncpg did, in the implicit-transaction model). The current `replace_chunks_for_document` already uses `async with conn.transaction():` — for aiosqlite that becomes `await conn.execute("BEGIN")` ... `await conn.commit()` / `await conn.rollback()` via a try/except, OR use aiosqlite's `async with conn:` context which auto-commits on success.
  - `EXISTS (...)` subquery in `create_message` (line 583) — keep as-is, syntax is SQL-standard.
  - `RETURNING` clauses — none used today; nothing to port.
  - `ILIKE` → SQLite's `LIKE` is case-insensitive on ASCII by default; pattern stays the same. Confirm via test.
  - `_now()` change: keep returning aware `datetime`, but at INSERT time pass `now.isoformat()` (string) — aiosqlite has no native TIMESTAMPTZ adapter. Update `_now()` to return `str` instead, or wrap call sites with `.isoformat()`. **Recommended: change `_now()` to `return datetime.now(UTC).isoformat()`** — simpler and the return-type matches what SQLite stores.
  - `_hydrate_document` / `_hydrate_chunk`: aiosqlite returns `aiosqlite.Row` (sqlite3.Row), which is mapping-like via `dict(row)`. The existing `dict(row)` conversion works. The `metadata` and `sources` JSON columns are now always strings (no JSONB auto-decoding), so `json.loads(d["metadata"])` is unconditional (no `isinstance(d["metadata"], str)` guard needed).
  - **`replace_chunks_for_document`**: Remove the `embedding` column from the INSERT — vectors live in Qdrant. New columns inserted: `id, document_id, content, chunk_index, section_path, anchor, char_start, char_end, source_type`. The caller (ingest pipelines) will additionally call `vector_store.upsert_chunks(doc_id, chunks_with_embeddings)` after this.
  - **`list_chunks_for_document`**: Remove the `embedding` column from the SELECT — chunks no longer carry embeddings here.
  - `count_chunks`: still queries `document_chunks` count from SQLite (used by `/api/health`).
- **MIRROR**: All function signatures stay identical. Only internals change.
- **IMPORTS**: `import aiosqlite` (replaces `import asyncpg`); `from backend.db.sqlite import _acquire`
- **GOTCHA 1**: aiosqlite returns Row objects; `dict(row)` works, but column names are case-sensitive and match what's in the SELECT exactly. The current code uses `d.get("metadata")` style access which works fine on `dict(row)` output.
- **GOTCHA 2**: For `WHERE x IN (?,?,?)` with a dynamic list, you must spread the params with `*` — `await conn.execute(sql, (..., *allowed_source_types, ...))`.
- **GOTCHA 3**: aiosqlite returns `bytes` for TEXT columns sometimes if encoding is mis-set. Open connections with `detect_types=sqlite3.PARSE_DECLTYPES` is NOT needed; defaults are fine. But `text_factory = str` may be useful if you ever see bytes.
- **GOTCHA 4**: `last_crawled_at`, `created_at`, etc. are now strings, not datetime. Code that does `.isoformat()` on them will break. Search and remove — for `create_sync_run`, the `started_at_str` line (line 662) already handles both shapes; check all call sites.
- **VALIDATE**:
  - `uv run ruff check backend/db/repository.py` — exit 0
  - `uv run mypy backend/db/repository.py` — exit 0
  - `uv run pytest tests/ -x --tb=short -k "not test_routes_sources"` — should mostly pass; route tests fail until vector_store mock is added

### Task 7: DELETE `app/backend/db/postgres.py`

- **ACTION**: DELETE
- **IMPLEMENT**: Remove the file. No other module should import from it after Task 6.
- **VALIDATE**: `rg -n "from backend.db.postgres" app/backend/` — must return zero matches; `rg -n "import.*asyncpg" app/backend/` — must return zero matches.

### Task 8: DELETE `app/backend/db/signup_attempts_repo.py` and `app/backend/db/user_messages_repo.py`

- **ACTION**: DELETE both files
- **IMPLEMENT**: Confirm via grep that no module imports them (CLAUDE.md says they're vestigial; Phase 2 agent confirmed).
- **VALIDATE**: `rg -n "signup_attempts_repo|user_messages_repo" app/backend/` — must return zero matches.

### Task 9: CREATE `app/backend/llm/providers.py`

- **ACTION**: CREATE
- **IMPLEMENT**:
  ```python
  """LLM provider factory: returns an OpenAI-SDK-shaped client configured for
  the active provider (OpenRouter or OpenAI native).
  """
  from __future__ import annotations
  import logging
  from openai import AsyncOpenAI, OpenAI
  from backend.config import (
      EMBEDDING_PROVIDER,
      LLM_PROVIDER,
      OPENAI_API_KEY,
      OPENAI_BASE_URL,
      OPENROUTER_API_KEY,
      OPENROUTER_BASE_URL,
  )

  logger = logging.getLogger(__name__)

  _async_chat_client: AsyncOpenAI | None = None
  _sync_embed_client: OpenAI | None = None

  def get_async_chat_client() -> AsyncOpenAI:
      global _async_chat_client
      if _async_chat_client is None:
          if LLM_PROVIDER == "openai":
              _async_chat_client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
          else:
              _async_chat_client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
      return _async_chat_client

  def get_sync_embed_client() -> OpenAI:
      global _sync_embed_client
      if _sync_embed_client is None:
          if EMBEDDING_PROVIDER == "openai":
              _sync_embed_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
          else:
              _sync_embed_client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
      return _sync_embed_client

  def resolve_embedding_model(model: str) -> str:
      """OpenRouter uses 'openai/text-embedding-3-small'; OpenAI native uses 'text-embedding-3-small'."""
      if EMBEDDING_PROVIDER == "openai" and model.startswith("openai/"):
          return model[len("openai/"):]
      return model

  def is_openrouter_chat() -> bool:
      return LLM_PROVIDER != "openai"
  ```
- **MIRROR**: `app/backend/llm/openrouter.py:38-42` (singleton client factory pattern); `app/backend/rag/embeddings.py:28-35`
- **IMPORTS**: stdlib + `openai` + `backend.config`
- **GOTCHA**: Anthropic OpenRouter models accept the OpenAI tools format and `cache_control` blocks; OpenAI native API does NOT accept `cache_control` blocks in system messages — it errors with 400. So `is_openrouter_chat()` is the gate used by `llm/chat.py` to decide whether to add the cache block.
- **VALIDATE**: `uv run python -c "from backend.llm.providers import get_async_chat_client, get_sync_embed_client, resolve_embedding_model; print(resolve_embedding_model('openai/text-embedding-3-small'))"`

### Task 10: RENAME `app/backend/llm/openrouter.py` → `app/backend/llm/chat.py` and refactor

- **ACTION**: RENAME (via `git mv`) and UPDATE
- **IMPLEMENT**:
  - Change `_get_async_client()` to call `providers.get_async_chat_client()` instead of constructing inline.
  - In `build_system_prompt()` (lines 114-146), wrap the `cache_control` lines (132-145) in `if providers.is_openrouter_chat():` — for native OpenAI, do NOT add cache_control. The catalog block itself can still be appended (it's just text), but the `cache_control` key must be omitted from both the catalog block (modify `catalog.build_catalog_block` to accept a `cache: bool` parameter, defaulting True) and the base block.
  - Update the module docstring (lines 1-10) to reflect the provider-agnostic role: "Streaming chat wrapper over the OpenAI-compatible API (works with both OpenRouter and OpenAI native, selected via LLM_PROVIDER)".
  - Everything else (stream_chat, tool-call loop, heartbeats, error handling) stays identical.
- **MIRROR**: `app/backend/llm/openrouter.py` — preserve every other line as-is
- **IMPORTS**: replace `from openai import AsyncOpenAI` direct construction with `from backend.llm import providers`
- **GOTCHA**: The `_async_client` singleton in this module is now redundant with `providers._async_chat_client` — remove `_async_client` here and just call `providers.get_async_chat_client()` per call (it's still a singleton at the provider layer).
- **VALIDATE**: `uv run ruff check backend/llm/` and `uv run mypy backend/llm/` — exit 0.

### Task 11: UPDATE `app/backend/rag/catalog.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Add a parameter to `build_catalog_block(documents, tier, *, cache: bool = True)`. When `cache=False`, omit the `cache_control` key from the returned dict. Update the one caller in `llm/chat.py` (Task 10) to pass `cache=providers.is_openrouter_chat()`.
- **MIRROR**: existing function shape (`catalog.py:46-84`)
- **IMPORTS**: no change
- **GOTCHA**: Tests for catalog (if any) need to pass `cache=True` explicitly OR rely on the default.
- **VALIDATE**: `uv run ruff check backend/rag/catalog.py` exit 0.

### Task 12: UPDATE `app/backend/rag/embeddings.py`

- **ACTION**: UPDATE
- **IMPLEMENT**:
  - Replace `_get_client()` body with `from backend.llm.providers import get_sync_embed_client; return get_sync_embed_client()`.
  - Replace `model=EMBEDDING_MODEL` in both `embeddings.create` calls with `model=resolve_embedding_model(EMBEDDING_MODEL)`.
  - Delete the module-level `_client: OpenAI | None = None` singleton (now lives in providers).
- **MIRROR**: existing structure of `embeddings.py:43-115` — only the inner client construction changes.
- **IMPORTS**: add `from backend.llm.providers import get_sync_embed_client, resolve_embedding_model`. Keep `from backend.config import EMBEDDING_MODEL` (and remove unused `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`).
- **GOTCHA**: For OpenRouter, the model slug is `openai/text-embedding-3-small`. For OpenAI native, it's `text-embedding-3-small`. `resolve_embedding_model()` strips the `openai/` prefix if `EMBEDDING_PROVIDER=openai`.
- **VALIDATE**: `uv run mypy backend/rag/embeddings.py` — exit 0.

### Task 13: CREATE `app/backend/rag/vector_store.py`

- **ACTION**: CREATE
- **IMPLEMENT**:
  ```python
  """Qdrant-backed vector store with server-side hybrid search (dense + BM25).

  One collection holds named vectors:
    - "dense": 1536-dim, COSINE distance — OpenAI text-embedding-3-small
    - "bm25": sparse, IDF modifier — Qdrant/bm25 via FastEmbed

  Hybrid retrieval uses the Query API with prefetch + fusion=RRF, replacing the
  in-process RRF formerly done in rag/retriever_hybrid.py.
  """
  from __future__ import annotations
  import logging
  from typing import Any
  from qdrant_client import AsyncQdrantClient, models
  from fastembed import SparseTextEmbedding

  from backend.config import (
      DEFAULT_SOURCE_TYPE,
      EMBEDDING_DIM,
      HYBRID_K_CONSTANT,
      HYBRID_OVERFETCH_FACTOR,
      QDRANT_API_KEY,
      QDRANT_BM25_MODEL,
      QDRANT_COLLECTION,
      QDRANT_DENSE_VECTOR_NAME,
      QDRANT_SPARSE_VECTOR_NAME,
      QDRANT_URL,
  )

  logger = logging.getLogger(__name__)

  _client: AsyncQdrantClient | None = None
  _bm25: SparseTextEmbedding | None = None

  def _get_client() -> AsyncQdrantClient:
      global _client
      if _client is None:
          if not QDRANT_URL:
              raise RuntimeError("QDRANT_URL is not set.")
          _client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
      return _client

  def _get_bm25() -> SparseTextEmbedding:
      global _bm25
      if _bm25 is None:
          _bm25 = SparseTextEmbedding(model_name=QDRANT_BM25_MODEL)
      return _bm25

  async def ensure_collection() -> None:
      """Create the collection if it doesn't exist. Idempotent — called from lifespan."""
      client = _get_client()
      exists = await client.collection_exists(QDRANT_COLLECTION)
      if exists:
          return
      await client.create_collection(
          collection_name=QDRANT_COLLECTION,
          vectors_config={
              QDRANT_DENSE_VECTOR_NAME: models.VectorParams(
                  size=EMBEDDING_DIM, distance=models.Distance.COSINE,
              ),
          },
          sparse_vectors_config={
              QDRANT_SPARSE_VECTOR_NAME: models.SparseVectorParams(
                  modifier=models.Modifier.IDF,
              ),
          },
      )
      # Payload indexes for filtering / per-document grouping.
      await client.create_payload_index(
          QDRANT_COLLECTION, field_name="document_id", field_schema=models.PayloadSchemaType.KEYWORD,
      )
      await client.create_payload_index(
          QDRANT_COLLECTION, field_name="source_type", field_schema=models.PayloadSchemaType.KEYWORD,
      )
      logger.info("Created Qdrant collection: %s", QDRANT_COLLECTION)

  async def close() -> None:
      global _client
      if _client is not None:
          await _client.close()
          _client = None

  async def upsert_chunks(
      *,
      document_id: str,
      chunks: list[dict[str, Any]],  # each: {chunk_id, content, embedding, chunk_index, section_path, anchor, char_start, char_end, source_type, document_title, document_url, document_content_path}
  ) -> None:
      """Upsert points for one document. `chunk_id` is the Qdrant point id."""
      if not chunks:
          return
      client = _get_client()
      bm25 = _get_bm25()
      texts = [c["content"] for c in chunks]
      sparse_vectors = list(bm25.embed(texts))  # list[SparseEmbedding]
      points: list[models.PointStruct] = []
      for c, sparse in zip(chunks, sparse_vectors, strict=True):
          points.append(
              models.PointStruct(
                  id=c["chunk_id"],
                  vector={
                      QDRANT_DENSE_VECTOR_NAME: c["embedding"],
                      QDRANT_SPARSE_VECTOR_NAME: models.SparseVector(
                          indices=sparse.indices.tolist(), values=sparse.values.tolist(),
                      ),
                  },
                  payload={
                      "chunk_id": c["chunk_id"],
                      "document_id": document_id,
                      "content": c["content"],
                      "chunk_index": c["chunk_index"],
                      "section_path": c.get("section_path", []),
                      "anchor": c.get("anchor"),
                      "char_start": c.get("char_start", 0),
                      "char_end": c.get("char_end", 0),
                      "source_type": c.get("source_type", DEFAULT_SOURCE_TYPE),
                      "document_title": c.get("document_title", ""),
                      "document_url": c.get("document_url"),
                      "document_content_path": c.get("document_content_path"),
                  },
              )
          )
      await client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)

  async def delete_document(document_id: str) -> None:
      client = _get_client()
      await client.delete(
          collection_name=QDRANT_COLLECTION,
          points_selector=models.FilterSelector(
              filter=models.Filter(
                  must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))],
              ),
          ),
          wait=True,
      )

  async def count() -> int:
      client = _get_client()
      result = await client.count(collection_name=QDRANT_COLLECTION, exact=True)
      return result.count

  def _source_type_filter(allowed: list[str] | None) -> models.Filter | None:
      if not allowed:
          return None
      return models.Filter(
          must=[models.FieldCondition(key="source_type", match=models.MatchAny(any=allowed))],
      )

  def _hit_to_dict(point) -> dict:
      """Map Qdrant ScoredPoint → the canonical hit shape consumed by tools/messages."""
      p = point.payload or {}
      return {
          "chunk_id": p.get("chunk_id") or str(point.id),
          "content": p.get("content", ""),
          "document_id": p.get("document_id", ""),
          "document_title": p.get("document_title", "Untitled"),
          "document_url": p.get("document_url"),
          "document_content_path": p.get("document_content_path"),
          "source_type": p.get("source_type", DEFAULT_SOURCE_TYPE),
          "section_path": p.get("section_path") or [],
          "anchor": p.get("anchor"),
          "chunk_index": p.get("chunk_index", 0),
          "score": point.score or 0.0,
      }

  async def hybrid_search(
      query_text: str,
      query_embedding: list[float],
      top_k: int = 5,
      allowed_source_types: list[str] | None = None,
  ) -> list[dict]:
      """Server-side RRF over dense + BM25 sparse. Returns the canonical hit shape."""
      client = _get_client()
      bm25 = _get_bm25()
      sparse = next(iter(bm25.query_embed(query_text)))
      fetch_k = top_k * HYBRID_OVERFETCH_FACTOR
      query_filter = _source_type_filter(allowed_source_types)
      result = await client.query_points(
          collection_name=QDRANT_COLLECTION,
          prefetch=[
              models.Prefetch(
                  query=query_embedding, using=QDRANT_DENSE_VECTOR_NAME,
                  limit=fetch_k, filter=query_filter,
              ),
              models.Prefetch(
                  query=models.SparseVector(indices=sparse.indices.tolist(), values=sparse.values.tolist()),
                  using=QDRANT_SPARSE_VECTOR_NAME, limit=fetch_k, filter=query_filter,
              ),
          ],
          query=models.FusionQuery(fusion=models.Fusion.RRF),
          limit=top_k,
          with_payload=True,
      )
      return [_hit_to_dict(p) for p in result.points]

  async def keyword_search(query_text: str, top_k: int = 10, allowed_source_types: list[str] | None = None) -> list[dict]:
      """Sparse-only search (BM25) — used by the keyword_search_documents LLM tool."""
      client = _get_client()
      bm25 = _get_bm25()
      sparse = next(iter(bm25.query_embed(query_text)))
      result = await client.query_points(
          collection_name=QDRANT_COLLECTION,
          query=models.SparseVector(indices=sparse.indices.tolist(), values=sparse.values.tolist()),
          using=QDRANT_SPARSE_VECTOR_NAME,
          query_filter=_source_type_filter(allowed_source_types),
          limit=top_k,
          with_payload=True,
      )
      return [_hit_to_dict(p) for p in result.points]

  async def semantic_search(query_embedding: list[float], top_k: int = 10, allowed_source_types: list[str] | None = None) -> list[dict]:
      """Dense-only search — used by the semantic_search_documents LLM tool."""
      client = _get_client()
      result = await client.query_points(
          collection_name=QDRANT_COLLECTION,
          query=query_embedding,
          using=QDRANT_DENSE_VECTOR_NAME,
          query_filter=_source_type_filter(allowed_source_types),
          limit=top_k,
          with_payload=True,
      )
      return [_hit_to_dict(p) for p in result.points]
  ```
- **MIRROR**: `app/backend/rag/embeddings.py:25-35` (module-level singleton client pattern); `app/backend/rag/retriever_hybrid.py:60-72` (canonical hit-shape dict)
- **IMPORTS**: `qdrant_client.AsyncQdrantClient`, `qdrant_client.models`, `fastembed.SparseTextEmbedding`, config constants
- **GOTCHA 1**: `HYBRID_K_CONSTANT=60` is the default `k` in Qdrant's RRF — Qdrant doesn't currently expose `k` as a tunable. If the value diverges in the Qdrant version pinned, document the discrepancy. As of `qdrant-client>=1.12`, RRF k is fixed at 60.
- **GOTCHA 2**: `fastembed` lazily downloads the BM25 model to `~/.cache/fastembed` on first use. In Docker, the `app` user must have write access to that cache (or set `FASTEMBED_CACHE_PATH` to `/app/data/fastembed-cache`).
- **GOTCHA 3**: `bm25.embed()` and `bm25.query_embed()` return generators of objects with `.indices` (numpy array) and `.values` (numpy array). Convert to Python lists via `.tolist()` before handing to `SparseVector(...)`.
- **GOTCHA 4**: Qdrant's `query_points` API replaced the older `search`/`recommend` endpoints. The `prefetch=[...]` + `query=FusionQuery(fusion=RRF)` shape is the supported way to do server-side hybrid.
- **VALIDATE**: `uv run mypy backend/rag/vector_store.py` exit 0; tests come in Task 25.

### Task 14: REWRITE `app/backend/rag/retriever_hybrid.py`

- **ACTION**: REWRITE
- **IMPLEMENT**: Reduce to a thin delegating shim. Keep the public `retrieve_hybrid` and `invalidate_cache` exports so callers (`rag/tools.py`, `routes/sources.py`) don't change.
  ```python
  """Hybrid retriever — thin shim over rag.vector_store.hybrid_search.

  Server-side RRF (k=60) is done by Qdrant; this module exists only as a
  stable public surface and for future ranking/post-processing extensions.
  """
  from __future__ import annotations
  import logging
  from backend.config import DEFAULT_SOURCE_TYPE
  from backend.rag import vector_store

  logger = logging.getLogger(__name__)

  def invalidate_cache() -> None:
      """No-op now — payloads carry document metadata; no in-process cache.

      Retained as a public function because routes/sources.py calls it after
      ingest. Future caching layers can hook here.
      """
      logger.debug("retriever_hybrid.invalidate_cache called (no-op).")

  async def retrieve_hybrid(
      query_text: str,
      query_embedding: list[float],
      top_k: int = 5,
      allowed_source_types: list[str] | None = None,
  ) -> list[dict]:
      if allowed_source_types is None:
          allowed_source_types = [DEFAULT_SOURCE_TYPE]
      return await vector_store.hybrid_search(
          query_text=query_text,
          query_embedding=query_embedding,
          top_k=top_k,
          allowed_source_types=allowed_source_types,
      )
  ```
- **MIRROR**: existing `retriever_hybrid.retrieve_hybrid` signature on lines 43-77 — keep identical args.
- **IMPORTS**: `from backend.rag import vector_store`
- **GOTCHA**: Existing `_document_cache` lookup is gone — vector_store includes document metadata in payloads. If any downstream code reads e.g. `chunk["document_title"]`, those keys are populated by the Qdrant payload.
- **VALIDATE**: existing tests `test_rag_tools.py` should still pass with `vector_store.hybrid_search` monkeypatched.

### Task 15: UPDATE `app/backend/rag/tools.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: In `execute_search_keyword` (~ lines 435-467) and `execute_search_semantic` (~ lines 470-501):
  - Replace `repository.keyword_search(query, top_k, language=KEYWORD_LANGUAGE)` → `vector_store.keyword_search(query, top_k, allowed_source_types)`
  - Replace `repository.vector_search_pg(embedding, top_k)` → `vector_store.semantic_search(embedding, top_k, allowed_source_types)`
  - Drop `_hydrate_chunks()` (which fetched document metadata via repository.get_document) — vectors now carry metadata in payload, so the keys are already populated.
  - Tool schemas (lines 35-140): unchanged. Tool descriptions still mention "Postgres tsvector" / "pgvector cosine" — UPDATE the description text to say "BM25 sparse vector index" and "dense vector cosine similarity" to keep model guidance accurate. Don't change tool NAMES — backward-compat with prompts and the model.
- **MIRROR**: `tools.py` existing executor structure (parse args, validate, call data source, normalize, expand, format, return)
- **IMPORTS**: `from backend.rag import vector_store` — replace direct `from backend.db import repository` use in search executors. `repository.get_document` and `repository.list_chunks_for_document` ARE still imported (needed by `execute_get_document` — that one reads metadata + chunks from SQLite).
- **GOTCHA**: `execute_get_document` is special — it returns the full document body. The chunks come from SQLite (`list_chunks_for_document` — no embedding column now, that's fine; it never used the embedding). Document fetch via `repository.get_document` is unchanged.
- **VALIDATE**: `uv run pytest tests/test_rag_tools.py -xvs` — once mocks are updated (Task 24), all tests pass.

### Task 16: UPDATE `app/backend/main.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: In the lifespan handler:
  - Replace `await init_pg_pool()` with `await init_sqlite_db()`
  - After `init_sqlite_db()`, add `await vector_store.ensure_collection()` (so the Qdrant collection is created idempotently on first startup)
  - On shutdown: `await close_sqlite_db()` then `await vector_store.close()`
  - Update imports: replace `from backend.db.postgres import init_pg_pool, close_pg_pool` with `from backend.db.sqlite import init_sqlite_db, close_sqlite_db` and `from backend.rag import vector_store`.
  - Update the validation guard at top of lifespan (line ~42): the `DATABASE_URL` check stays, but now expects a `sqlite+aiosqlite://` URL. Add a parallel guard: `if not QDRANT_URL: raise RuntimeError("QDRANT_URL is not set — the app refuses to start without a Qdrant endpoint.")`
- **MIRROR**: existing lifespan structure (main.py:38-76 in current code)
- **IMPORTS**: as above
- **GOTCHA**: The `subprocess.run([..., "alembic", ..., "upgrade", "head"])` block stays — Alembic now runs SQLite migrations (per Task 5). Confirm it picks up `DATABASE_URL` from the env. (It does — `alembic/env.py:get_database_url` reads env first.)
- **VALIDATE**: `docker compose -f deploy/docker-compose.yml up --build` after Task 22 — the app starts cleanly, no Postgres errors.

### Task 17: UPDATE `app/backend/ingest/url_list.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: After every successful `repository.replace_chunks_for_document(doc_id, payload, source_type=...)` call, also call `vector_store.upsert_chunks(document_id=doc_id, chunks=enriched)`, where `enriched` is the payload list augmented with `chunk_id` (UUIDs generated client-side instead of in repository — see below) and document metadata (`document_title`, `document_url`, `document_content_path`, `source_type`).
  - Because chunk IDs need to match between SQLite and Qdrant, **generate chunk_ids in the ingest pipeline before calling either**. Pass them into both `replace_chunks_for_document` (extended to accept a pre-supplied `chunk_id` per entry) and `vector_store.upsert_chunks`.
  - Update `replace_chunks_for_document` (Task 6 already in scope): if `c.get("chunk_id")` is set, use it; else fall back to `_new_id()`. This keeps the contract backwards-compatible.
  - On failure: if the Qdrant upsert fails, delete the document vectors from Qdrant and the chunk rows from SQLite, mark the sync_item as `error`. Use a try/except wrapping both writes. (Don't bother with two-phase commit — single-tenant, low-volume.)
- **MIRROR**: existing per-document ingest section (around `ingest/url_list.py:226-241`)
- **IMPORTS**: `from backend.rag import vector_store`
- **GOTCHA**: The `chunks` list passed to `vector_store.upsert_chunks` must include `embedding` (the dense vector) — fetched from the same `embed_batch(texts)` call that already happens. Confirm the per-chunk dict shape matches what `vector_store.upsert_chunks` expects.
- **VALIDATE**: existing `tests/test_ingest_url_list.py` (after Task 26 adds `_FakeVectorStore`) passes — `_FakeVectorStore.upsert_chunks` is called once per document.

### Task 18: UPDATE `app/backend/ingest/vault.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Same pattern as Task 17 — add `vector_store.upsert_chunks(...)` after every successful `replace_chunks_for_document(...)`, with the same error-handling discipline.
- **MIRROR**: Task 17's pattern
- **GOTCHA**: same as Task 17
- **VALIDATE**: `tests/test_ingest_vault.py` passes after Task 26.

### Task 19: UPDATE `app/backend/routes/sources.py`

- **ACTION**: UPDATE (minor)
- **IMPLEMENT**: No behavior change; just import: `from backend.rag import retriever_hybrid, catalog` still works (retriever_hybrid still exports `invalidate_cache`, now a no-op). The `vector_store` doesn't need invalidation in the sync flow (Qdrant is the source of truth, not cached).
- **VALIDATE**: `tests/test_routes_sources.py` passes after Task 27.

### Task 20: UPDATE `app/backend/routes/messages.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Replace one import line: `from backend.llm.openrouter import stream_chat` → `from backend.llm.chat import stream_chat`. No behavior change. All other logic — citation marker stripping, SSE event framing, `_collapse_by_document`, `_is_refusal` — untouched.
- **MIRROR**: existing import section
- **GOTCHA**: Anywhere else in the codebase importing `backend.llm.openrouter` — grep and update. Likely only `routes/messages.py` and tests.
- **VALIDATE**: `rg "backend.llm.openrouter" app/backend/` returns zero matches after this task.

### Task 21: REWRITE `deploy/docker-compose.yml`

- **ACTION**: REWRITE
- **IMPLEMENT**:
  ```yaml
  services:
    app:
      build:
        context: ..
        dockerfile: deploy/Dockerfile
      image: firstspirit-docs-rag:latest
      container_name: firstspirit-docs-rag-app
      restart: unless-stopped
      environment:
        # --- LLM / embeddings ---
        LLM_PROVIDER: ${LLM_PROVIDER:-openrouter}
        EMBEDDING_PROVIDER: ${EMBEDDING_PROVIDER:-openrouter}
        OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
        OPENAI_API_KEY: ${OPENAI_API_KEY:-}
        OPENAI_BASE_URL: ${OPENAI_BASE_URL:-https://api.openai.com/v1}
        CHAT_MODEL: ${CHAT_MODEL:-anthropic/claude-sonnet-4.6}
        EMBEDDING_MODEL: ${EMBEDDING_MODEL:-openai/text-embedding-3-small}
        LLM_REASONING_EFFORT: ${LLM_REASONING_EFFORT:-}

        # --- Vector store (Qdrant Cloud) ---
        QDRANT_URL: ${QDRANT_URL:?QDRANT_URL must be set in deploy/.env}
        QDRANT_API_KEY: ${QDRANT_API_KEY:?QDRANT_API_KEY must be set in deploy/.env}
        QDRANT_COLLECTION: ${QDRANT_COLLECTION:-firstspirit_docs}

        # --- Database (SQLite) ---
        DATABASE_URL: sqlite+aiosqlite:////app/data/app.db

        # --- FastEmbed cache (BM25 model download) ---
        FASTEMBED_CACHE_PATH: /app/data/fastembed-cache

        # --- RAG tuning + crawler (UNCHANGED from before) ---
        RETRIEVAL_EXPANSION_WINDOW: ${RETRIEVAL_EXPANSION_WINDOW:-1}
        RETRIEVAL_MAX_PER_DOCUMENT: ${RETRIEVAL_MAX_PER_DOCUMENT:-3}
        CITATIONS_MAX_COUNT: ${CITATIONS_MAX_COUNT:-10}
        LLM_TOOLS_ENABLED: ${LLM_TOOLS_ENABLED:-true}
        LLM_TOOLS_MAX_PER_TURN: ${LLM_TOOLS_MAX_PER_TURN:-6}
        DOCUMENT_TOOL_MAX_CHARS: ${DOCUMENT_TOOL_MAX_CHARS:-120000}
        CATALOG_ENABLED: ${CATALOG_ENABLED:-false}
        CATALOG_TIER: ${CATALOG_TIER:-standard}
        CATALOG_CACHE_TTL_SECONDS: ${CATALOG_CACHE_TTL_SECONDS:-3600}
        SOURCE_URL_LIST_PATH: "/app/sources/URL List.md"
        SOURCE_VAULT_PATH: "${SOURCE_VAULT_PATH_IN_CONTAINER:-}"
        CRAWLER_USER_AGENT: "${CRAWLER_USER_AGENT:-FirstSpiritDocsRAG/1.0 (+contact: you@example.com)}"
        CRAWLER_REQUEST_DELAY_MS: ${CRAWLER_REQUEST_DELAY_MS:-500}
        CRAWLER_MAX_RETRIES: ${CRAWLER_MAX_RETRIES:-4}
        CRAWLER_TIMEOUT_SECONDS: ${CRAWLER_TIMEOUT_SECONDS:-30.0}
        DEFAULT_SOURCE_TYPE: ${DEFAULT_SOURCE_TYPE:-firstspirit}
        CORS_ORIGINS: "${CORS_ORIGINS:-http://localhost:8000,http://127.0.0.1:8000}"
        FRONTEND_DIST: /app/frontend/dist
      ports:
        - "${APP_HOST_PORT:-8000}:8000"
      volumes:
        - sqlite_data:/app/data
        - ${URL_LIST_HOST_PATH:-./sources}:/app/sources:ro
        - ${VAULT_HOST_PATH:-./sources/empty-vault}:/app/vault:ro
      healthcheck:
        test:
          - "CMD"
          - "python"
          - "-c"
          - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"
        interval: 10s
        timeout: 5s
        start_period: 30s
        retries: 5

  volumes:
    sqlite_data:
  ```
  Drop the `postgres` service entirely. Drop the `depends_on:` block. Drop the `postgres_data` volume.
- **MIRROR**: existing `app:` service block; mostly preserved.
- **GOTCHA 1**: SQLite path uses **four** slashes: `sqlite+aiosqlite:////app/data/app.db` — three for the scheme + one for absolute path.
- **GOTCHA 2**: `OPENROUTER_API_KEY` is no longer required at compose time — it's only required if `LLM_PROVIDER=openrouter` or `EMBEDDING_PROVIDER=openrouter`. Same for `OPENAI_API_KEY`. So no `?` (required) on either of those vars. App layer raises a clear error at first call if the active provider's key is missing.
- **GOTCHA 3**: `FASTEMBED_CACHE_PATH` directs the BM25 model download into the persistent volume, so it doesn't re-download on every container restart.
- **VALIDATE**: `docker compose -f deploy/docker-compose.yml config` parses without errors; `docker compose -f deploy/docker-compose.yml up --build` brings up the single container.

### Task 22: UPDATE `deploy/Dockerfile`

- **ACTION**: UPDATE (minimal)
- **IMPLEMENT**: The line `RUN mkdir -p /app/data && chown -R app:app /app` already exists — confirm it's still present, and that `WORKDIR /app` is set before `USER app` switches. Add `RUN mkdir -p /app/data/fastembed-cache && chown -R app:app /app/data` if not present.
- **VALIDATE**: `docker build -f deploy/Dockerfile .` succeeds; image runs without permission errors writing to `/app/data`.

### Task 23: UPDATE `deploy/.env.example` and `.env.example` (root)

- **ACTION**: UPDATE both
- **IMPLEMENT**:
  - **`deploy/.env.example`**: Remove `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_HOST_PORT`. Add:
    ```
    # Vector store (Qdrant Cloud) — required
    QDRANT_URL=
    QDRANT_API_KEY=
    # QDRANT_COLLECTION=firstspirit_docs

    # Provider selection
    # LLM_PROVIDER=openrouter
    # EMBEDDING_PROVIDER=openrouter
    # Required when LLM_PROVIDER=openrouter or EMBEDDING_PROVIDER=openrouter
    OPENROUTER_API_KEY=
    # Required when LLM_PROVIDER=openai or EMBEDDING_PROVIDER=openai
    OPENAI_API_KEY=
    # OPENAI_BASE_URL=https://api.openai.com/v1

    # Model overrides
    # CHAT_MODEL=anthropic/claude-sonnet-4.6
    # EMBEDDING_MODEL=openai/text-embedding-3-small
    ```
    Keep everything else (crawler, RAG knobs, source mounts) intact.
  - **Root `.env.example`**: Same updates; `DATABASE_URL=sqlite+aiosqlite:///./data/app.db` (relative for manual dev — note that `cd app && uv --project backend run uvicorn ...` from CLAUDE.md means cwd is `app/`, so the path resolves to `app/data/app.db`).
- **MIRROR**: existing `.env.example` comment structure (sectioned with `# ---`)
- **VALIDATE**: `cp deploy/.env.example deploy/.env` and fill in `QDRANT_URL`, `QDRANT_API_KEY`, `OPENROUTER_API_KEY`. `docker compose -f deploy/docker-compose.yml config` succeeds.

### Task 24: UPDATE `app/backend/tests/conftest.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Replace the Postgres dummy URL with SQLite in-memory, and add Qdrant/OpenAI dummies:
  ```python
  os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
  os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
  os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
  os.environ.setdefault("QDRANT_URL", "http://qdrant.test")
  os.environ.setdefault("QDRANT_API_KEY", "test-qdrant-key")
  os.environ.setdefault("LLM_PROVIDER", "openrouter")
  os.environ.setdefault("EMBEDDING_PROVIDER", "openrouter")
  os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
  os.environ["CRAWLER_REQUEST_DELAY_MS"] = "0"
  ```
- **MIRROR**: existing `conftest.py` env-bootstrap pattern
- **GOTCHA**: Some tests may want a fresh on-disk SQLite — for those, use `tmp_path` and override the env var per-test.
- **VALIDATE**: `uv run pytest tests/ --collect-only` — no import errors.

### Task 25: CREATE `app/backend/tests/test_vector_store.py`

- **ACTION**: CREATE
- **IMPLEMENT**: Mock `AsyncQdrantClient` at the module level. Tests:
  - `test_hybrid_search_uses_query_api_with_rrf_fusion`: Call `vector_store.hybrid_search("foo", [0.1]*1536, top_k=5)`. Assert the mock `query_points` was called with `prefetch=[Prefetch(using="dense"), Prefetch(using="bm25")]` and `query=FusionQuery(fusion=RRF)`. (Inspect `kwargs` of the mock call.)
  - `test_hybrid_search_returns_canonical_hit_shape`: Make the mock return a fake `QueryResponse` with one point whose payload has all the canonical fields. Assert the returned dict has keys `chunk_id, content, document_id, document_title, document_url, document_content_path, source_type, section_path, anchor, chunk_index, score`.
  - `test_upsert_chunks_writes_dense_and_sparse`: Call with a single chunk. Assert `upsert(points=[PointStruct(...)])` was called with both `dense` and `bm25` vectors populated.
  - `test_keyword_search_uses_sparse_only`, `test_semantic_search_uses_dense_only`.
  - `test_source_type_filter_applied_when_provided`.
  - `test_ensure_collection_idempotent`: When `collection_exists` returns True, `create_collection` is not called.
- **MIRROR**: `tests/test_rag_tools.py` monkeypatch pattern
- **IMPORTS**: `pytest`, `from unittest.mock import AsyncMock`, `from backend.rag import vector_store`
- **GOTCHA 1**: `fastembed.SparseTextEmbedding` should also be mocked to avoid downloading the BM25 model in tests. Monkeypatch `vector_store._get_bm25` to return a fake whose `embed`/`query_embed` returns a generator yielding objects with `.indices` and `.values` numpy arrays (use small fake arrays).
- **GOTCHA 2**: Use `monkeypatch.setattr(vector_store, "_client", AsyncMock())` to inject the fake client without touching `_get_client`.
- **VALIDATE**: `uv run pytest tests/test_vector_store.py -xvs` — all pass.

### Task 26: UPDATE `app/backend/tests/test_ingest_url_list.py` and `test_ingest_vault.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Add a `_FakeVectorStore` class mirroring `_FakeRepo`:
  ```python
  class _FakeVectorStore:
      def __init__(self):
          self.upserted: dict[str, list[dict]] = {}
          self.deleted: list[str] = []
      async def upsert_chunks(self, *, document_id, chunks):
          self.upserted[document_id] = list(chunks)
      async def delete_document(self, document_id):
          self.deleted.append(document_id)
  ```
  Monkeypatch `url_list_mod.vector_store` (and `vault_mod.vector_store`) with this fake. Add assertions to existing tests:
  - After ingesting a doc, `fake_vector_store.upserted[doc_id]` has the right number of chunks.
  - Each chunk dict has the full payload shape (`chunk_id`, `embedding`, `content`, `document_title`, …).
- **MIRROR**: existing `_FakeRepo` pattern in those test files.
- **VALIDATE**: `uv run pytest tests/test_ingest_url_list.py tests/test_ingest_vault.py -xvs` — all pass.

### Task 27: UPDATE `app/backend/tests/test_rag_tools.py`

- **ACTION**: UPDATE
- **IMPLEMENT**: Replace monkeypatches that targeted `backend.db.repository.keyword_search` / `vector_search_pg` with `backend.rag.vector_store.keyword_search` / `semantic_search` / `hybrid_search`. Same fake-result shapes; the canonical hit dict is unchanged.
- **MIRROR**: existing monkeypatch lines in this file
- **GOTCHA**: `_hydrate_chunks` was dropped from `tools.py` — tests that asserted hydration behavior are now testing `vector_store` instead.
- **VALIDATE**: `uv run pytest tests/test_rag_tools.py -xvs` — all pass.

### Task 28: CREATE `scripts/migrate_pg_to_qdrant.py`

- **ACTION**: CREATE
- **IMPLEMENT**: Standalone CLI for users with an existing Postgres install:
  ```python
  """One-shot migration: Postgres+pgvector → SQLite + Qdrant Cloud.

  Reads all rows from documents + document_chunks via asyncpg. Inserts
  documents into the new SQLite DB. Upserts chunks into Qdrant (dense via the
  stored embedding JSON; sparse re-derived via FastEmbed BM25 locally).

  Usage:
    uv run python scripts/migrate_pg_to_qdrant.py \
        --pg-dsn postgresql://docs_rag:pw@localhost:5433/docs_rag \
        --sqlite-path ./data/app.db \
        --qdrant-url https://xxx.cloud.qdrant.io \
        --qdrant-api-key <key>

  Idempotent: re-running upserts the same chunk_ids into Qdrant (no-op for
  unchanged) and skips already-present SQLite rows.
  """
  import asyncio, argparse, json, asyncpg, aiosqlite
  from backend.rag import vector_store
  # ... read pg, write sqlite + qdrant ...
  ```
- **MIRROR**: `app/backend/ingest/url_list.py` flow — fetch, embed (already done), upsert in batches
- **GOTCHA 1**: The script is the ONLY place in the migrated codebase that depends on asyncpg. Keep it isolated (don't import from `backend.db.repository` since that's SQLite-only now). The script can install asyncpg ad-hoc via `uv run --with asyncpg python scripts/migrate_pg_to_qdrant.py ...` if `asyncpg` was removed from main deps.
- **GOTCHA 2**: Document this in README under a "Migrating an existing install" section.
- **VALIDATE**: `uv run --with asyncpg python scripts/migrate_pg_to_qdrant.py --help` shows usage.

### Task 29: UPDATE `README.md` and `CLAUDE.md`

- **ACTION**: UPDATE both
- **IMPLEMENT**:
  - **README.md**: Tech-stack list now says "SQLite (chat + metadata) + Qdrant Cloud (vectors, hybrid search via Query API)". Quick-start: remove `POSTGRES_PASSWORD`, add `QDRANT_URL`, `QDRANT_API_KEY`. Add a "Choosing a provider" subsection covering `LLM_PROVIDER` and `EMBEDDING_PROVIDER`. Add a "Migrating from a Postgres install" subsection pointing to `scripts/migrate_pg_to_qdrant.py`.
  - **CLAUDE.md**:
    - Lines 17-40 (Tech Stack): swap `asyncpg` → `aiosqlite + qdrant-client`. Drop `pgvector` mention. Note dual-provider support.
    - Lines 170-185 (Database section): rename to "Database + Vector Store". SQLite for chat/sync/documents metadata, Qdrant for vectors+payloads. All SQL still lives in `db/repository.py`. All vector queries live in `rag/vector_store.py`.
    - Lines 220-258 (Env vars table): drop POSTGRES_*; add QDRANT_*, LLM_PROVIDER, EMBEDDING_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL.
    - RAG Pipeline Invariants section: invariant #3 (retrieval) now reads "Hybrid via Qdrant Query API — server-side RRF (k=60 fixed by Qdrant), prefetch over dense (1536-dim cosine) + sparse (BM25 IDF modifier via FastEmbed). Top-k = 5 by default. Both halves run server-side; do not push retrieval to the client."
- **MIRROR**: existing structure of those files
- **VALIDATE**: visual inspection; spell-check; cross-reference with config.py.

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|-----------|------------|-----------|
| `tests/test_vector_store.py` | hybrid_search uses Query API with RRF fusion; upsert writes dense+sparse; keyword_search uses sparse only; semantic_search uses dense only; ensure_collection idempotent; source_type filter applied | Qdrant adapter contract |
| `tests/test_llm_providers.py` | get_async_chat_client returns OpenRouter-configured AsyncOpenAI when LLM_PROVIDER=openrouter; same for openai (different base_url); resolve_embedding_model strips `openai/` prefix only for openai provider; is_openrouter_chat reflects the env | Provider factory |
| `tests/test_repository_sqlite.py` | create_document + get_document round-trip; create_conversation + list_messages cascade-delete via ON DELETE CASCADE; replace_chunks_for_document deletes prior chunks then inserts new ones in a transaction; search ILIKE works on SQLite | Repository SQLite port |
| `tests/test_retriever_hybrid.py` | retrieve_hybrid delegates to vector_store.hybrid_search with allowed_source_types defaulted to [DEFAULT_SOURCE_TYPE]; invalidate_cache is a no-op | Shim correctness |

Existing test updates (Tasks 26, 27): `test_ingest_url_list`, `test_ingest_vault`, `test_rag_tools`, `test_routes_sources`.

### Edge Cases Checklist

- [ ] Empty `query` string in `vector_store.hybrid_search` raises (input validation)
- [ ] `allowed_source_types=None` defaults to `[DEFAULT_SOURCE_TYPE]`
- [ ] `allowed_source_types=[]` (explicit empty) means no filter (or document the choice)
- [ ] Document with zero chunks: `upsert_chunks(chunks=[])` is a no-op (early return), no Qdrant call
- [ ] Document deletion: `delete_document` removes BOTH the SQLite rows (via ON DELETE CASCADE — confirm SQLite FK behavior with `PRAGMA foreign_keys=ON`) AND the Qdrant points (filter by document_id)
- [ ] LLM provider mismatch: `LLM_PROVIDER=openai` but `OPENAI_API_KEY=""` → first chat call raises a clean RuntimeError (not a 401 buried in SDK exception)
- [ ] `EMBEDDING_PROVIDER` differs from `LLM_PROVIDER` (e.g. embeddings via OpenAI native, chat via OpenRouter) — independent clients work side-by-side
- [ ] `cache_control` is omitted when `LLM_PROVIDER=openai` (OpenAI native rejects unknown keys with 400)
- [ ] SQLite IN-list with one element vs many: `(?, ?, ?)` vs `(?)` placeholder generation works
- [ ] FastEmbed BM25 cache directory is writable in container (test in integration smoke run, not unit)
- [ ] Qdrant collection auto-creation race: parallel app starts both call `ensure_collection` — Qdrant's `create_collection` should error gracefully on second call (we guard with `collection_exists` first, but document the race)
- [ ] Alembic upgrade from a fresh SQLite file succeeds; re-running is a no-op (idempotent)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd app/backend && uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

**EXPECT**: Exit 0, no errors or warnings.

### Level 2: UNIT_TESTS

```bash
cd app/backend && uv run pytest tests/ -xvs
```

**EXPECT**: All tests pass. Test count goes from 86 to ~95+ (added vector_store, llm_providers, repository_sqlite, retriever_hybrid tests).

### Level 3: FRONTEND

```bash
cd app/frontend && bun install && bun run tsc --noEmit && bun x biome check src && bun run test
```

**EXPECT**: All pass — frontend should be untouched.

### Level 4: INTEGRATION (manual, with real Qdrant Cloud)

```bash
# Set deploy/.env with real QDRANT_URL, QDRANT_API_KEY, OPENROUTER_API_KEY
docker compose -f deploy/docker-compose.yml up --build -d
# Wait for healthy:
docker compose -f deploy/docker-compose.yml ps
# Trigger ingest:
curl -X POST http://localhost:8000/api/sources/sync -H 'Content-Type: application/json' -d '{"kind":"url_list"}'
# Wait until done, then chat:
# (POST a message and confirm streaming response with citations)
```

**EXPECT**: App boots, ingest writes to both SQLite and Qdrant, retrieval returns hits with valid citation markers, SSE stream is byte-compatible with the old behavior.

### Level 5: PROVIDER SMOKE

Run Level 4 twice — once with `LLM_PROVIDER=openrouter EMBEDDING_PROVIDER=openrouter`, once with `LLM_PROVIDER=openai EMBEDDING_PROVIDER=openai CHAT_MODEL=gpt-4o EMBEDDING_MODEL=text-embedding-3-small`. Both must produce streaming responses with citations.

---

## Acceptance Criteria

- [ ] `docker compose -f deploy/docker-compose.yml up --build` brings up a single `app` container (no postgres). The container reaches healthy state.
- [ ] `POST /api/sources/sync {"kind":"url_list"}` ingests the seed URL list. Chunks land in Qdrant (verifiable via Qdrant Cloud UI or `client.count`). Document rows land in SQLite (verifiable via `sqlite3 /app/data/app.db ".tables"` inside the container).
- [ ] `POST /api/conversations/{id}/messages` streams an answer with `[c:<id>]` citation markers, an `event: sources` payload, and a `data: [DONE]` terminator. The frontend renders source chips and deep-link anchors identically to before.
- [ ] All 4 LLM tools (`search_documents`, `keyword_search_documents`, `semantic_search_documents`, `get_document`) work end-to-end against Qdrant + SQLite.
- [ ] Refusal phrase "the documentation library does not cover that topic" still triggers citation suppression in `_is_refusal`.
- [ ] Per-document chunk collapse still works (one chip per document, not one per chunk).
- [ ] `LLM_PROVIDER=openai` produces a working chat stream against `https://api.openai.com/v1`; switching back to `openrouter` works without code changes.
- [ ] `EMBEDDING_PROVIDER=openai` produces working ingest embeddings against OpenAI native.
- [ ] No `asyncpg`, no `pgvector`, no `tsvector`, no `:vector` casts in the codebase (`rg -n "asyncpg|pgvector|tsvector|::vector|search_vector|to_tsvector" app/backend/` returns zero matches).
- [ ] `scripts/migrate_pg_to_qdrant.py --help` runs and produces sensible output for an existing Postgres user.
- [ ] All 4 levels of validation commands pass with exit 0.

---

## Completion Checklist

- [ ] All 29 tasks completed in dependency order
- [ ] Each task validated immediately after completion
- [ ] All acceptance criteria met
- [ ] README + CLAUDE.md updated (Task 29)
- [ ] `.env.example` files updated (Task 23)
- [ ] PR description includes the validation evidence (test counts, smoke-test outputs)

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Qdrant RRF k constant differs from 60 in some client/server version | LOW | LOW | Qdrant 1.12+ has RRF with fixed k=60. Document the version requirement in pyproject.toml. If diverged, fall back to client-side RRF using the same formula as before. |
| BM25 keyword quality lower than Postgres tsvector on test queries (especially proper nouns / acronyms) | MEDIUM | MEDIUM | BM25 is the industry-standard sparse retriever and outperforms ts_rank on real-world queries. Run an A/B eval on 10-20 known queries before merging. Tune by switching `Qdrant/bm25` to `Qdrant/bm42` if quality is lower. |
| FastEmbed model download fails in container (no network at startup) | MEDIUM | HIGH | First call to `bm25.embed()` downloads the model (~30MB ONNX). Mount `/app/data/fastembed-cache` as a persistent volume (already in compose). Pre-warm by calling `vector_store._get_bm25()` once in `ensure_collection()`. |
| SQLite `WHERE x IN (?,?,?)` placeholder generation introduces SQL injection | LOW | HIGH | Generate the placeholder string from the LENGTH of the list (`",".join("?" * len(values))`), not from the values themselves. The only thing interpolated is `?` characters. Code-review every dynamic-IN site. |
| Per-call SQLite connection adds latency vs the asyncpg pool | LOW | LOW | aiosqlite open-on-each-call is ~1ms for a local file; the chat workload is dominated by LLM streaming. If profiling shows a hotspot, introduce a connection pool. Defer. |
| Anthropic prompt caching no longer works on OpenAI native | EXPECTED | LOW | OpenAI has automatic prompt caching that requires no `cache_control` blocks. The catalog block is still sent as system content — caching just happens transparently on the OpenAI side. Document the difference in README. |
| Existing users have data in Postgres that's hard to move | MEDIUM | MEDIUM | `scripts/migrate_pg_to_qdrant.py` is shipped specifically for this. README section explains the one-shot migration flow. The script is idempotent — re-runs are safe. |
| Qdrant collection schema drift between dev/prod | LOW | MEDIUM | `ensure_collection()` is idempotent. Schema changes (new payload index, new sparse model) require an explicit migration step — documented in README under "Schema changes". |
| `pyproject.toml` deps not pinned tightly → CI fragility | MEDIUM | LOW | `uv.lock` pins exact versions. CI uses `uv sync --frozen --no-dev`. Document the upgrade path in README. |
| `aiosqlite` autocommit semantics surprise (forgetting `await conn.commit()`) | MEDIUM | HIGH | Add a code-review checklist item: every INSERT/UPDATE/DELETE path in `repository.py` must end with `await conn.commit()`. Better: refactor `_acquire()` to return a context manager that auto-commits on success and rolls back on exception, mirroring asyncpg's implicit-transaction semantics. |

---

## Notes

### Why server-side RRF instead of keeping client-side?

Qdrant's Query API (≥1.10) supports server-side RRF with the canonical `k=60` constant. Pushing the fusion server-side:

1. Eliminates one round-trip (was: two queries + Python merge; now: one query with prefetch),
2. Lets Qdrant pre-filter by `source_type` once instead of twice,
3. Keeps the implementation in the vector store where it belongs.

Trade-off: we lose the ability to tune `k` (Qdrant hardcodes 60). The original RRF paper says 60 is reasonable for most workloads; we've been using 60 already.

### Why SQLite and not "just keep using a single named-volume Postgres"?

The user explicitly requested SQLite to remove the Postgres dependency from docker-compose. This collapses two containers into one and simplifies operator setup. SQLite handles the load (single-tenant, low QPS, small dataset). If the workload grows to multi-tenant or high concurrency, SQLite → Postgres is a straightforward swap because `repository.py` is the only SQL surface.

### Why drop the auth tables?

Per CLAUDE.md (line 173-178): `users`, `user_messages`, `signup_attempts` are vestigial. They're not wired into any route. Keeping them in the new SQLite migration is dead weight. Removed.

### Why keep the chunk_id as application-generated UUID?

Qdrant accepts arbitrary string IDs. Citation markers `[c:<chunk_id>]` already encode UUIDs. Generating IDs application-side (in ingest, before either write) keeps SQLite and Qdrant in sync without a coordination step.

### Anthropic `cache_control` vs OpenAI prompt caching

OpenRouter passes `cache_control: {"type": "ephemeral"}` through to Anthropic, which caches the marked content block for ~5min (or 1h with `ttl=3600`). OpenAI's native API has automatic prompt caching with no caller-side block needed — caching is transparent. So for `LLM_PROVIDER=openai`, we strip the `cache_control` key (otherwise OpenAI returns 400 "extra inputs are not permitted"). This is gated through `providers.is_openrouter_chat()`.

### Backwards-compatible chunk_id

`replace_chunks_for_document` accepts an optional pre-supplied `chunk_id` per entry, falling back to a fresh `_new_id()` when omitted. This keeps existing call sites that pre-date this change working, while the ingest pipelines (url_list, vault) start supplying IDs explicitly so the same ID lands in both Qdrant and SQLite.

### Confidence

Confidence score: **8/10** for one-pass implementation success.

Rationale: The migration is large but mechanical — every function has a clear before/after target, the public surfaces (route handlers, SSE format, tool schemas, hit shapes) all stay identical, and tests are constructed to verify each boundary independently. The two real risk areas are (1) BM25 retrieval quality vs tsvector on real queries, and (2) the discipline of `await conn.commit()` on every aiosqlite write path. Both are caught by the test suite and the validation steps, but they're where a careless implementation could silently regress retrieval quality or persistence. The migration script is the simplest piece (one direction, one-shot, idempotent) and is isolated from the main app.
