# Plan Confirmation

**Generated**: 2026-05-13 19:02
**Workflow ID**: `32824a7a430c0b813fae0af044c3b71e`
**Status**: CONFIRMED (with one minor warning)

---

## Pattern Verification

| Pattern | File | Status | Notes |
|---------|------|--------|-------|
| `init_pg_pool` / `close_pg_pool` / `get_pg_pool` shape | `app/backend/db/postgres.py:1-65` | ✅ | Present (file is 76 lines; `_init_connection`, `init_pg_pool`, `close_pg_pool`, `get_pg_pool` all intact). Plan target `db/sqlite.py` should mirror this surface |
| `async with _acquire() as conn` repo signature | `app/backend/db/repository.py:108-111` | ✅ | `get_document(document_id)` at line 108 still uses `async with _acquire() as conn` + `$1` placeholder. Same pattern at lines 114, 120 etc. |
| RuntimeError wrapping for external services | `app/backend/rag/embeddings.py:62-70` | ✅ | `try / except / logger.error / raise RuntimeError(...) from exc` block intact |
| `retrieve_hybrid()` + `invalidate_cache()` public surface | `app/backend/rag/retriever_hybrid.py:1-186` | ✅ | File is 185 lines (1 off — fine). `invalidate_cache` at line 36, `retrieve_hybrid` at line 43 |
| SSE contract (`data:`, `event: sources`, `[DONE]`) | `app/backend/routes/messages.py:1-330` | ✅ | Exactly 330 lines. `stream_chat` import at 31, `data: [DONE]\n\n` terminator at 116, `event: sources\ndata: {sources_json}\n\n` at 149 |
| `stream_chat` generator with tool-call loop / cache_control | `app/backend/llm/openrouter.py:1-422` | ✅ | Exactly 422 lines. `stream_chat` at line 177, `cache_control` at 145 (Anthropic ephemeral), tool-call loop intact |
| Alembic initial schema (table set + indices) | `app/backend/alembic/versions/0001_initial.py:1-260` | ✅ | 259 lines (1 off — fine). Contains `CREATE EXTENSION vector`, `tsvector`, `users`, `user_messages`, `signup_attempts`, `documents`, `document_chunks`, `conversations`, `messages`, `source_sync_runs`, `source_sync_items` — exactly the set the plan rewrites |
| Env-var reads centralised in `config.py` | `app/backend/config.py:1-142` | ✅ | 141 lines (1 off — fine). `OPENROUTER_API_KEY` at 39, `CHAT_MODEL` at 48, `EMBEDDING_MODEL` at 47, `DATABASE_URL` at 56. New vars from the plan slot in here |

**Pattern Summary**: 8 of 8 patterns verified — all source line ranges are off by at most 1 line (i.e. plan rounded up `EOF` line), no semantic drift.

---

## Target Files

### Files to Create — verified absent

| File | Status |
|------|--------|
| `app/backend/db/sqlite.py` | ✅ Does not exist (ready to create) |
| `app/backend/llm/providers.py` | ✅ Does not exist (ready to create) |
| `app/backend/rag/vector_store.py` | ✅ Does not exist (ready to create) |
| `app/backend/tests/test_vector_store.py` | ✅ Does not exist |
| `app/backend/tests/test_llm_providers.py` | ✅ Does not exist |
| `app/backend/tests/test_repository_sqlite.py` | ✅ Does not exist |
| `app/backend/tests/test_retriever_hybrid.py` | ✅ Does not exist |
| `scripts/migrate_pg_to_qdrant.py` | ✅ Does not exist (`scripts/` directory will be created by mkdir) |

### Files to Update / Rewrite / Rename / Delete — verified present

All 21 UPDATE/REWRITE/DELETE/RENAME targets exist:

| File | Status |
|------|--------|
| `app/backend/pyproject.toml` | ✅ Exists |
| `app/backend/uv.lock` | ✅ Exists |
| `app/backend/config.py` | ✅ Exists |
| `app/backend/db/postgres.py` | ✅ Exists (will be deleted) |
| `app/backend/db/repository.py` | ✅ Exists (rewrite) |
| `app/backend/db/signup_attempts_repo.py` | ✅ Exists (delete — confirmed unused via grep; only self-references) |
| `app/backend/db/user_messages_repo.py` | ✅ Exists (delete — confirmed unused) |
| `app/backend/alembic/versions/0001_initial.py` | ✅ Exists (rewrite) |
| `app/backend/alembic/env.py` | ✅ Exists |
| `app/backend/main.py` | ✅ Exists |
| `app/backend/llm/openrouter.py` | ✅ Exists (rename to `chat.py`) |
| `app/backend/rag/embeddings.py` | ✅ Exists |
| `app/backend/rag/retriever_hybrid.py` | ✅ Exists (rewrite) |
| `app/backend/rag/tools.py` | ✅ Exists |
| `app/backend/routes/messages.py` | ✅ Exists |
| `app/backend/routes/sources.py` | ✅ Exists |
| `app/backend/ingest/url_list.py` | ✅ Exists |
| `app/backend/ingest/vault.py` | ✅ Exists |
| `app/backend/tests/conftest.py` | ✅ Exists |
| `app/backend/tests/test_rag_tools.py` | ✅ Exists |
| `app/backend/tests/test_ingest_url_list.py` | ✅ Exists |
| `app/backend/tests/test_ingest_vault.py` | ✅ Exists |
| `app/backend/tests/test_routes_sources.py` | ✅ Exists |
| `deploy/docker-compose.yml` | ✅ Exists |
| `deploy/Dockerfile` | ✅ Exists |
| `deploy/.env.example` | ✅ Exists |
| `.env.example` (root) | ✅ Exists |
| `README.md` | ✅ Exists |
| `CLAUDE.md` | ✅ Exists |

---

## Validation Commands

| Command | Available | Notes |
|---------|-----------|-------|
| `uv run ruff check .` / `format --check .` / `mypy .` | ✅ | uv 0.9.26 installed; backend deps install cleanly (104 packages) |
| `uv run pytest tests/ -xvs` | ✅ | Same toolchain |
| `bun install` / `bun run tsc --noEmit` / `bun x biome check src` / `bun run test` | ✅ | bun 1.3.6 installed; `app/frontend/package.json` exists |
| `docker compose -f deploy/docker-compose.yml up --build -d` | ✅ | docker 29.2.0 + compose v5.0.2 installed |

**Local Python note**: Local `uv` uses Python 3.12.8, but `deploy/Dockerfile` pins `python:3.11-slim` per CLAUDE.md. Plan does not need 3.12+ features, so this is fine — just be aware that local-only behavior should be re-validated in the Docker image before merge.

---

## Issues Found

### Warnings

1. **Doc-drift between `CLAUDE.md` and actual schema** (non-blocking)
   - `CLAUDE.md` "Database" section describes the table as `chunks` with a `content_tsv` tsvector column.
   - Actual `alembic/versions/0001_initial.py` defines it as `document_chunks` with a `search_vector` column, and `repository.py` queries `FROM document_chunks` (line 208, 224, 269, 309, 329).
   - This does **not** block the plan — the plan rewrites all SQL and all schema in one pass, so the new SQLite migration will name tables fresh. But the plan's task list and `CLAUDE.md` update step should converge on a single consistent name (recommend keeping `document_chunks` to minimize diff in repository SQL).

### Blockers

None.

---

## Recommendation

✅ **PROCEED**: Plan research is valid. All 8 P0 pattern files exist with their referenced ranges intact (±1 line for EOF rounding), all 29 target files are in the expected state (CREATE files absent, UPDATE/DELETE files present), and all validation toolchains (uv, bun, docker) are installed and working.

The one warning (doc-drift on table name in `CLAUDE.md`) is minor and inside scope of the plan's existing `CLAUDE.md` update task.

---

## Next Step

Continue to `archon-implement-tasks` to execute the 29-task plan.
