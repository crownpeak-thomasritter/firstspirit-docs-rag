# Implementation Progress

**Generated**: 2026-05-13 21:30
**Workflow ID**: `32824a7a430c0b813fae0af044c3b71e`
**Status**: COMPLETE

---

## Tasks Completed

| # | Task | File | Status | Notes |
|---|------|------|--------|-------|
| 1 | UPDATE pyproject.toml | `app/backend/pyproject.toml` | ✅ | Removed `asyncpg`, added `aiosqlite>=0.20`, `sqlalchemy>=2.0`, `qdrant-client[fastembed]>=1.12`; added `qdrant_client.*` / `fastembed.*` to mypy ignore list |
| 2 | REGEN uv.lock | `app/backend/uv.lock` | ✅ | `uv lock` removed `asyncpg`, added `qdrant-client 1.18.0` + `aiosqlite 0.22.1` + `fastembed 0.8.0` + transitive deps |
| 3 | UPDATE config.py | `app/backend/config.py` | ✅ | Added `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `QDRANT_DENSE_VECTOR_NAME`, `QDRANT_SPARSE_VECTOR_NAME`, `QDRANT_BM25_MODEL`, `EMBEDDING_DIM`; `EMBEDDING_MODEL` now env-configurable |
| 4 | CREATE db/sqlite.py | `app/backend/db/sqlite.py` | ✅ | aiosqlite per-call connection module; `_acquire()` auto-commits on clean exit, rolls back on exception |
| 5 | REWRITE 0001_initial.py | `app/backend/alembic/versions/0001_initial.py` | ✅ | Dropped pgvector/tsvector/auth tables; SQLite types (TEXT for timestamps, JSON, no GENERATED tsvector column); kept ON DELETE CASCADE and CHECK constraints |
| 6 | UPDATE alembic/env.py | `app/backend/alembic/env.py` | ✅ | Sync SQLAlchemy engine; `sqlite+aiosqlite://` → `sqlite+pysqlite://` URL normalization |
| 7 | REWRITE repository.py | `app/backend/db/repository.py` | ✅ | aiosqlite, `?` placeholders, no `::jsonb`/`::vector` casts; removed `keyword_search` and `vector_search_pg`; `_now()` returns ISO-8601 string; `replace_chunks_for_document` accepts optional `chunk_id` per chunk |
| 8 | DELETE postgres.py | `app/backend/db/postgres.py` | ✅ | Removed (replaced by `db/sqlite.py`) |
| 9 | DELETE vestigial repos | `app/backend/db/signup_attempts_repo.py`, `user_messages_repo.py` | ✅ | Removed — confirmed no imports |
| 10 | CREATE llm/providers.py | `app/backend/llm/providers.py` | ✅ | `get_async_chat_client()` / `get_sync_embed_client()` factories; `resolve_embedding_model()` strips `openai/` prefix only for native OpenAI; `is_openrouter_chat()` gates Anthropic cache_control |
| 11 | RENAME openrouter.py → chat.py | `app/backend/llm/chat.py` | ✅ | `git mv`; `_get_async_client` removed in favor of `providers.get_async_chat_client()`; `cache_control` gated by `providers.is_openrouter_chat()`; error messages no longer say "OpenRouter" |
| 12 | UPDATE rag/catalog.py | `app/backend/rag/catalog.py` | ✅ | `build_catalog_block(documents, tier, *, cache: bool = True)` — omits `cache_control` when `cache=False` |
| 13 | UPDATE rag/embeddings.py | `app/backend/rag/embeddings.py` | ✅ | Uses `get_sync_embed_client()` + `resolve_embedding_model()`; removed module-level singleton |
| 14 | CREATE rag/vector_store.py | `app/backend/rag/vector_store.py` | ✅ | `AsyncQdrantClient` wrapper. `ensure_collection`, `upsert_chunks`, `delete_document`, `hybrid_search` (Query API + prefetch + RRF), `keyword_search` (sparse), `semantic_search` (dense), `count`, `close` |
| 15 | REWRITE retriever_hybrid.py | `app/backend/rag/retriever_hybrid.py` | ✅ | Thin shim that delegates to `vector_store.hybrid_search`; `invalidate_cache` retained as a no-op |
| 16 | UPDATE rag/tools.py | `app/backend/rag/tools.py` | ✅ | Removed `_hydrate_chunks`; keyword/semantic executors call `vector_store.keyword_search`/`semantic_search`; tool descriptions updated to mention BM25 + dense cosine |
| 17 | UPDATE main.py | `app/backend/main.py` | ✅ | Lifespan: `init_sqlite_db()` + `vector_store.ensure_collection()`; shutdown closes both; `QDRANT_URL` gated on startup |
| 18 | UPDATE ingest/url_list.py | `app/backend/ingest/url_list.py` | ✅ | Generates chunk_ids before DB write; dual-writes to SQLite + Qdrant; rolls back both stores on Qdrant upsert failure |
| 19 | UPDATE ingest/vault.py | `app/backend/ingest/vault.py` | ✅ | Same dual-write pattern; carries `document_content_path` into Qdrant payload |
| 20 | routes/sources.py | `app/backend/routes/sources.py` | ✅ | No behavior change; `retriever_hybrid.invalidate_cache()` still works |
| 21 | UPDATE routes/messages.py | `app/backend/routes/messages.py` | ✅ | Import swap: `from backend.llm.chat import stream_chat` |
| 22 | REWRITE docker-compose.yml | `deploy/docker-compose.yml` | ✅ | Dropped `postgres` service + `postgres_data` volume; added `sqlite_data` named volume at `/app/data`; passes through `QDRANT_*`, `OPENAI_*`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `FASTEMBED_CACHE_PATH` |
| 23 | UPDATE Dockerfile | `deploy/Dockerfile` | ✅ | `/app/data` + `/app/data/fastembed-cache` directories created and `chown`'d to `app:app`; healthcheck comment updated |
| 24 | UPDATE env examples | `deploy/.env.example`, `.env.example` | ✅ | Removed `POSTGRES_*`; added `QDRANT_URL`, `QDRANT_API_KEY`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| 25 | UPDATE conftest.py | `app/backend/tests/conftest.py` | ✅ | `DATABASE_URL=sqlite+aiosqlite:///:memory:`; added `QDRANT_URL`/`QDRANT_API_KEY`/`OPENAI_API_KEY`/provider dummies |
| 26 | CREATE test_vector_store.py | `app/backend/tests/test_vector_store.py` | ✅ | 10 tests — Qdrant client + FastEmbed BM25 fully mocked; verifies Query API + RRF fusion + sparse/dense vector kinds + source-type filter + idempotent `ensure_collection` |
| 27 | UPDATE ingest tests | `tests/test_ingest_url_list.py`, `tests/test_ingest_vault.py` | ✅ | Added `_FakeVectorStore` and `fake_vector_store` fixtures; assertions on chunk_id parity and payload metadata |
| 28 | UPDATE test_rag_tools.py | `app/backend/tests/test_rag_tools.py` | ✅ | Replaced repository.keyword_search monkeypatches with `vector_store.keyword_search` / `vector_store.semantic_search` |
| 29 | CREATE migration script | `scripts/migrate_pg_to_qdrant.py` | ✅ | Standalone CLI: reads from Postgres via `asyncpg` (uv-run-with), writes documents+chunks to SQLite, upserts dense vectors + payload to Qdrant; `--help` verified working |
| 30 | UPDATE docs | `README.md`, `CLAUDE.md` | ✅ | Tech stack, architecture diagram, quick start, "Choosing a provider", "Migrating from a Postgres install"; CLAUDE.md repo layout, Database+Vector Store section, env-var table, RAG invariants #3 + #4, Dos and Don'ts |
| Extra | CREATE test_llm_providers.py | `app/backend/tests/test_llm_providers.py` | ✅ | 8 tests — factory base_url + api_key per provider, prefix-strip logic, missing-key RuntimeError |
| Extra | CREATE test_repository_sqlite.py | `app/backend/tests/test_repository_sqlite.py` | ✅ | 8 tests — CRUD round-trip, FK CASCADE, supplied chunk_id, get_chunk_neighbors window, LIKE search |
| Extra | CREATE test_retriever_hybrid.py | `app/backend/tests/test_retriever_hybrid.py` | ✅ | 3 tests — shim delegation, default source types, invalidate_cache no-op |

**Progress**: 32 of 32 tasks completed (29 planned + 3 extra test files specified in plan)

---

## Files Changed

| File | Action | Notes |
|------|--------|-------|
| `app/backend/pyproject.toml` | UPDATE | Deps: removed `asyncpg`; added `aiosqlite`, `sqlalchemy`, `qdrant-client[fastembed]` |
| `app/backend/uv.lock` | REGEN | Resolved 124 packages |
| `app/backend/config.py` | UPDATE | +21 lines provider / Qdrant config |
| `app/backend/db/sqlite.py` | CREATE | 90 lines — aiosqlite connection module |
| `app/backend/db/repository.py` | REWRITE | 530 lines — aiosqlite SQL port; `_acquire`/`_fetchall`/`_fetchone`/`_execute` helpers |
| `app/backend/db/postgres.py` | DELETE | Removed |
| `app/backend/db/signup_attempts_repo.py` | DELETE | Removed |
| `app/backend/db/user_messages_repo.py` | DELETE | Removed |
| `app/backend/alembic/versions/0001_initial.py` | REWRITE | 170 lines — SQLite-compatible schema |
| `app/backend/alembic/env.py` | UPDATE | Sync engine for migrations |
| `app/backend/main.py` | UPDATE | Lifespan: SQLite + Qdrant init |
| `app/backend/llm/providers.py` | CREATE | 96 lines — provider factory |
| `app/backend/llm/chat.py` | RENAME+UPDATE | (was openrouter.py) — provider-agnostic streaming |
| `app/backend/llm/openrouter.py` | DELETE | Replaced by chat.py |
| `app/backend/rag/catalog.py` | UPDATE | `cache=` kwarg added |
| `app/backend/rag/embeddings.py` | UPDATE | Uses `providers` factory |
| `app/backend/rag/vector_store.py` | CREATE | 290 lines — Qdrant wrapper |
| `app/backend/rag/retriever_hybrid.py` | REWRITE | 60 lines — shim |
| `app/backend/rag/tools.py` | UPDATE | Removed `_hydrate_chunks`; calls `vector_store` directly |
| `app/backend/ingest/url_list.py` | UPDATE | Dual-write + rollback |
| `app/backend/ingest/vault.py` | UPDATE | Dual-write + rollback |
| `app/backend/routes/messages.py` | UPDATE | Import rename |
| `app/backend/tests/conftest.py` | UPDATE | New env defaults |
| `app/backend/tests/test_ingest_url_list.py` | UPDATE | `_FakeVectorStore` + fixture |
| `app/backend/tests/test_ingest_vault.py` | UPDATE | `_FakeVectorStore` + fixture |
| `app/backend/tests/test_rag_tools.py` | UPDATE | vector_store mock replaces repository.keyword_search |
| `app/backend/tests/test_vector_store.py` | CREATE | 200 lines, 10 tests |
| `app/backend/tests/test_llm_providers.py` | CREATE | 155 lines, 8 tests |
| `app/backend/tests/test_repository_sqlite.py` | CREATE | 210 lines, 8 tests |
| `app/backend/tests/test_retriever_hybrid.py` | CREATE | 55 lines, 3 tests |
| `scripts/migrate_pg_to_qdrant.py` | CREATE | 250 lines — one-shot migration CLI |
| `deploy/docker-compose.yml` | REWRITE | Single app service; Qdrant external |
| `deploy/Dockerfile` | UPDATE | `/app/data` + fastembed cache dir |
| `deploy/.env.example` | UPDATE | Qdrant + provider envs |
| `.env.example` | UPDATE | Same as deploy/.env.example for manual dev |
| `README.md` | UPDATE | Tech stack, quick start, architecture diagram, provider + migration sections |
| `CLAUDE.md` | UPDATE | Tech stack, repo layout, env-var table, RAG invariants, Database+Vector Store section, Dos/Don'ts |

---

## Tests Written

| Test File | Test Cases |
|-----------|------------|
| `tests/test_vector_store.py` | `test_ensure_collection_idempotent`, `test_ensure_collection_creates_when_missing`, `test_upsert_chunks_writes_dense_and_sparse`, `test_upsert_chunks_empty_list_is_noop`, `test_hybrid_search_uses_query_api_with_rrf_fusion`, `test_hybrid_search_empty_query_raises`, `test_keyword_search_uses_sparse_only`, `test_semantic_search_uses_dense_only`, `test_source_type_filter_applied_when_provided`, `test_source_type_filter_none_means_no_filter` |
| `tests/test_llm_providers.py` | `test_get_async_chat_client_openrouter`, `test_get_async_chat_client_openai_native`, `test_get_sync_embed_client_openrouter`, `test_get_sync_embed_client_openai_native`, `test_resolve_embedding_model_openrouter_keeps_slug`, `test_resolve_embedding_model_openai_strips_prefix`, `test_is_openrouter_chat_reflects_env`, `test_get_async_chat_client_missing_key_raises` |
| `tests/test_repository_sqlite.py` | `test_create_and_get_document_roundtrip`, `test_count_documents_reflects_inserts`, `test_delete_document_cascades_chunks`, `test_replace_chunks_preserves_supplied_chunk_id`, `test_get_chunk_neighbors_window`, `test_create_message_and_cascade`, `test_create_message_returns_none_when_conversation_missing`, `test_search_documents_admin_uses_like` |
| `tests/test_retriever_hybrid.py` | `test_retrieve_hybrid_delegates_with_default_source_types`, `test_retrieve_hybrid_passes_through_explicit_source_types`, `test_invalidate_cache_is_a_noop` |
| `tests/test_rag_tools.py` (modified) | `test_execute_search_keyword_returns_canonical_chunks`, `test_execute_search_semantic_returns_canonical_chunks` (replaced the old hydration test) |
| `tests/test_ingest_url_list.py` (modified) | Existing tests extended with `fake_vector_store` fixture asserting `upsert_chunks` was called with the same chunk_id that landed in SQLite |
| `tests/test_ingest_vault.py` (modified) | Same — Qdrant upsert assertions added |

---

## Deviations from Plan

### Deviation 1: `_acquire()` placement

**Task**: 3 and 6
**Expected**: Plan mentioned `_acquire` going in `db/postgres.py:1-65` for the legacy code. The new `db/sqlite.py` was to mirror that surface.
**Actual**: `_acquire` lives in `db/sqlite.py` (auto-committing async context manager) and `repository.py` imports it. The legacy code had `_acquire` in `db/repository.py` (one-liner around `get_pg_pool().acquire()`).
**Reason**: Putting the context manager in `db/sqlite.py` keeps it next to the connection-construction logic and lets the auto-commit / rollback discipline live in the module that owns the connection lifecycle. `repository.py` just imports `_acquire` and re-exports it.

### Deviation 2: Retriever expansion still uses SQLite, not Qdrant

**Task**: 14 and 15
**Expected**: Plan said the retriever shim would just delegate to Qdrant.
**Actual**: True — `retriever_hybrid` itself is purely a shim. However `rag/expansion.py` (which `tools.py` uses to fetch neighbour chunks) still queries `repository.get_chunk_neighbors` against SQLite. Qdrant payloads don't carry the full document body, only chunk-level metadata.
**Reason**: This is correct behavior — neighbour expansion is an in-document operation and SQLite has every chunk for every document. No change needed.

### Deviation 3: Test for keyword search keeps the "hydration" name slot

**Task**: 28
**Expected**: Plan said "replace repository.keyword_search monkeypatches with vector_store ones."
**Actual**: Done, but the old `test_execute_search_keyword_hydrates_chunks` test was renamed to `test_execute_search_keyword_returns_canonical_chunks` because `_hydrate_chunks` was removed and the assertion now reads directly from the Qdrant payload (no hydration step).
**Reason**: The test still asserts the same thing the LLM cares about (`document_title`, `document_url` present on returned chunks); the implementation path is just different. Added a parallel `test_execute_search_semantic_returns_canonical_chunks`.

### Deviation 4: Health endpoint still counts SQLite chunks

**Task**: 17
**Expected**: Plan suggested counting Qdrant points via `vector_store.count()` in `/api/health`.
**Actual**: Kept `repository.count_chunks()` (counts SQLite rows).
**Reason**: Health checks fire frequently. Hitting Qdrant on every health check would add a network round-trip per probe; the SQLite count is local and free and SQLite chunk rows are 1:1 with Qdrant points. If they ever drift, the chunk count discrepancy would itself be visible via the ingest summary endpoints.

### Deviation 5: ruff format applied automatically

**Task**: validation
**Expected**: Validate with `ruff format --check`.
**Actual**: Initial run flagged 6 files; ran `ruff format .` to fix them (test_llm_providers.py, test_retriever_hybrid.py, main.py, llm/providers.py, ingest/url_list.py, ingest/vault.py).
**Reason**: Formatting nits only — long-line `monkeypatch.setattr(...)` calls split across lines, `else:` blocks collapsed to ternaries. No behavior change.

---

## Type-Check Status

- [x] Passes after all changes — `Success: no issues found in 43 source files`

---

## Test Status

- [x] All tests pass — **116 passed in 30.78s**
- Tests added: 29 new tests across 4 new files
- Tests modified: 7 existing tests (ingest dual-write + tools mock swap)
- Existing tests preserved: 87

Test count grew from 86 → 116 (+30 net; one keyword-hydration test was replaced 1:1).

---

## Static Analysis Status

- `uv run ruff check .` — All checks passed!
- `uv run ruff format --check .` — 43 files already formatted
- `uv run mypy .` — Success: no issues found in 43 source files

---

## Acceptance Criteria Check

Verified against the plan's acceptance criteria:

- [x] **No asyncpg/pgvector/tsvector live code references.** `rg -n "asyncpg|pgvector|tsvector|::vector|search_vector|to_tsvector" app/backend/` returns 4 lines, all of which are either docstrings ("no pgvector, no tsvector"), the backward-compat URL normalization in `alembic/env.py` (only used by the migration script), or a "Mirrors asyncpg's discipline" comment in `db/sqlite.py`. No live code path imports asyncpg or queries pgvector/tsvector.
- [x] **`scripts/migrate_pg_to_qdrant.py --help`** prints usage cleanly via `uv run --with asyncpg ...`.
- [x] **Alembic `upgrade head` against SQLite** runs cleanly: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001`.
- [x] **All 4 LLM tool names preserved** in `TOOL_SCHEMAS` — `search_documents`, `keyword_search_documents`, `semantic_search_documents`, `get_document`.
- [x] **SSE contract preserved** — `messages.py` was not touched beyond the `from backend.llm.chat import stream_chat` rename.

Acceptance criteria requiring real Qdrant Cloud + OpenRouter/OpenAI keys (containerised smoke test, real chat stream with citations end-to-end) are deferred to Level 4 / Level 5 manual validation — they cannot be verified inside the sandboxed sub-agent environment.

---

## Issues Encountered

### Issue 1: Initial ruff lint warnings

**Problem**: One SIM108 (`if/else` → ternary) on `update_sync_run.finished_at_str` and one I001 import sort on `test_repository_sqlite.py`.
**Resolution**: Collapsed the `if/else` to a ternary; split the `from backend.db import repository, sqlite as sqlite_mod` import into two lines (per ruff's preferred form).

### Issue 2: Format drift in newly written files

**Problem**: Several new files had long lines that ruff format would have rewritten.
**Resolution**: Ran `uv run ruff format .` once; 6 files reformatted, no behavior change.

---

## Next Step

Continue to `archon-validate` for the full validation suite (frontend tsc + biome + tests, Docker stack smoke, provider matrix).
