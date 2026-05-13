# CLAUDE.md

Instructions for AI coding agents working in this repository. Read this before making any code changes.

---

## Project Overview

**FirstSpirit Docs RAG** is a single-tenant chat application that lets a small team (today, the Crownpeak / FirstSpirit professional-services team) ask grounded questions of the FirstSpirit + Crownpeak product documentation and internal Obsidian notes. Streaming answers come back with per-chunk citations that deep-link to the source URL or note, anchored at the cited section when available.

The codebase started as a fork of DynaChat (YouTube-RAG) and has been pivoted away from videos: the ingest pipeline is now URL-list crawling plus an Obsidian vault reader, citations point at document/section/anchor instead of YouTube timestamps, and the auth stack is intentionally absent. A single `DEFAULT_USER_ID = "default-user"` anonymous identity satisfies the underlying repository's `user_id`-scoped signatures.

FastAPI + Python backend, React + Vite + TypeScript frontend, Postgres + pgvector for everything (chat tables, document tables, hybrid retrieval). Shipped as a single Docker Compose stack — `docker compose up` brings up Postgres and the app together; no SQLite, no auth provider, no external proxy required.

---

## Tech Stack

**Backend**
- Python 3.11+ (don't rely on 3.12+ features — the runtime image pins 3.11-slim)
- `uv` for package management — `app/backend/pyproject.toml` is the dependency source of truth, `app/backend/uv.lock` pins exact versions
- FastAPI with `uvicorn[standard]` ASGI server
- `asyncpg` for async Postgres access (via connection pool from `db/postgres.py`)
- `alembic` for schema migrations (single migration layer; ships with one initial revision)
- `docling-core[chunking,chunking-openai]` for markdown chunking (HybridChunker with cl100k_base tokenizer, max 512 tokens)
- `openai` SDK pointed at OpenRouter's OpenAI-compatible endpoint (embeddings + chat completions)
- `numpy` for in-process vector math
- `trafilatura` for HTML → markdown extraction during URL-list crawls
- `pymupdf4llm` for PDF → markdown extraction
- `python-frontmatter` for YAML frontmatter parsing in vault files
- `tenacity` for retry/backoff in the crawler
- `python-dotenv` for config loading

**Frontend**
- Bun (not npm, not pnpm — use `bun install`, `bun run dev`, `bun run build`)
- React 18.3, TypeScript 5.4, Vite 5.2
- `react-router-dom` v6 for routing
- `react-markdown` + `remark-gfm` for assistant message rendering
- `react-syntax-highlighter` for code blocks
- Tailwind CSS 3.4 (no component library — components are built from Tailwind primitives)
- Vanilla `fetch()` for API calls (no axios, no SDK) — typed wrappers in `src/lib/api.ts`
- `biome` for lint + format

---

## Repo Layout

```
firstspirit-docs-rag/
├── README.md                # Human-facing quick start
├── CLAUDE.md                # This file — code conventions for AI agents
├── .env.example             # Root env template for manual (non-Docker) dev
├── deploy/
│   ├── Dockerfile           # Multi-stage: Bun build → Python runtime
│   ├── docker-compose.yml   # Postgres + app, one command brings up the stack
│   ├── .env.example         # Compose-specific env template (POSTGRES_*, OPENROUTER_*)
│   ├── .dockerignore        # Keeps secrets and dev cruft out of image layers
│   └── sources/
│       ├── URL List.md      # Seed file — one URL per line, '#' lines ignored
│       └── empty-vault/     # Placeholder so the optional vault mount always has a target
└── app/
    ├── backend/
    │   ├── main.py          # FastAPI app, lifespan: alembic upgrade + pool init
    │   ├── config.py        # All env reads + hardcoded constants
    │   ├── pyproject.toml   # uv deps + tool config (ruff, mypy, pytest)
    │   ├── uv.lock          # pinned lockfile (committed)
    │   ├── alembic/         # Migrations — single initial revision creates all tables
    │   ├── alembic.ini
    │   ├── db/
    │   │   ├── postgres.py  # asyncpg pool init/close
    │   │   └── repository.py # ALL raw SQL lives here — nowhere else
    │   ├── ingest/
    │   │   ├── crawler.py   # httpx + tenacity HTTP fetcher (conditional GET, polite delay)
    │   │   ├── url_list.py  # Reads a markdown file of URLs, crawls and ingests
    │   │   └── vault.py     # Recursively reads an Obsidian-style markdown vault
    │   ├── llm/
    │   │   └── openrouter.py # stream_chat() async generator, SSE-formatted output
    │   ├── rag/
    │   │   ├── catalog.py        # In-process document catalog cache; builds cache_control block
    │   │   ├── citations.py      # `[c:<id>]` marker stripper + cited-chunk extraction
    │   │   ├── document_chunker.py # Docling HybridChunker wrapper for markdown
    │   │   ├── embeddings.py     # embed_text / embed_batch via OpenRouter
    │   │   ├── retriever_hybrid.py # RRF over Postgres tsvector + pgvector cosine
    │   │   └── tools.py          # LLM tool schemas + executor (search / get_document)
    │   ├── routes/
    │   │   ├── sources.py       # POST /api/sources/sync, GET /api/sources/{sync-runs,documents}
    │   │   ├── conversations.py # GET/POST/DELETE /api/conversations*, GET /api/documents
    │   │   └── messages.py      # POST /api/conversations/{id}/messages (streaming SSE)
    │   ├── services/
    │   │   └── extractor.py     # trafilatura / pymupdf4llm / raw-markdown extractors
    │   └── tests/               # pytest suite — 86 tests, mocks all external boundaries
    └── frontend/
        ├── package.json      # Bun deps + scripts
        ├── vite.config.ts
        ├── tsconfig.json
        ├── index.html
        └── src/
            ├── main.tsx
            ├── App.tsx
            ├── components/
            ├── hooks/
            ├── lib/
            │   └── api.ts    # All typed fetch wrappers + TypeScript interfaces
            └── styles/
                └── globals.css
```

**Placement rules** (where new files go):

- New API routes → new file in `app/backend/routes/`, one file per resource. Mount from `main.py`.
- New SQL queries → `app/backend/db/repository.py` only. Never write SQL in route handlers, services, or components.
- New schema changes → a new Alembic revision under `app/backend/alembic/versions/`. The startup lifespan runs `alembic upgrade head` automatically.
- New RAG pipeline steps → `app/backend/rag/`. Keep chunker, embeddings, retriever, and tool definitions as separate modules.
- New ingest sources → `app/backend/ingest/`, one file per source kind (URL list, vault, …). Each module exposes an async `sync_<kind>()` returning the `SyncResponse`-shaped summary defined in `routes/sources.py`.
- New React components → `app/frontend/src/components/`, one component per file, named exports matching filename.
- New React hooks → `app/frontend/src/hooks/`, prefix with `use`.
- New API client functions → `app/frontend/src/lib/api.ts`. Keep all fetch calls in this one file.

---

## Running the App

### Docker (recommended — brings up Postgres + app together)

From the repo root:

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env: set POSTGRES_PASSWORD and OPENROUTER_API_KEY (minimum).
# Optionally point VAULT_HOST_PATH at your real Obsidian vault and set
# SOURCE_VAULT_PATH_IN_CONTAINER=/app/vault.

docker compose -f deploy/docker-compose.yml up -d --build
```

Then visit `http://localhost:8000`. The container serves both `/api/*` (FastAPI) and the built frontend from the same origin, so no separate frontend dev server is needed.

To trigger ingestion once the stack is up:

```bash
# URL list (default reads deploy/sources/URL List.md)
curl -X POST http://localhost:8000/api/sources/sync \
  -H 'Content-Type: application/json' \
  -d '{"kind":"url_list"}'

# Vault (requires VAULT_HOST_PATH + SOURCE_VAULT_PATH_IN_CONTAINER set)
curl -X POST http://localhost:8000/api/sources/sync \
  -H 'Content-Type: application/json' \
  -d '{"kind":"vault"}'
```

### Manual dev (uv + bun, hot reload)

Useful for active development on the backend or frontend. Postgres still needs to be running — either bring up just that service via compose (`docker compose -f deploy/docker-compose.yml up -d postgres`) or run your own.

Copy the root `.env.example` to `.env` and fill in `DATABASE_URL` + `OPENROUTER_API_KEY`.

Backend:

```bash
cd app/backend
uv sync --all-extras                   # creates .venv, installs runtime + dev deps
cd ..
uv --project backend run uvicorn backend.main:app --reload --port 8000
```

Backend **must** be run from `app/`, not `app/backend/` — `backend.main:app` is a module path that requires `app/` on `sys.path`. The `--project backend` flag tells uv where the venv lives.

Frontend (separate terminal):

```bash
cd app/frontend
bun install
bun run dev           # dev server with HMR on http://localhost:5173
bun run build         # production build → dist/
```

In dev, the Vite proxy forwards `/api/*` to the backend on port 8000.

---

## Testing

```bash
# Backend
cd app/backend
uv run pytest tests -xvs
```

86 tests today. All external boundaries (HTTP via `httpx.MockTransport` / `respx`, the embedder, the repository) are mocked — the suite does not require a running Postgres, OpenRouter access, or any secret. `pytest-asyncio` is configured with `asyncio_mode = "auto"`, so plain `async def` test functions Just Work.

```bash
# Frontend
cd app/frontend
bun run test
```

---

## Lint, Format, Type Check

Backend tooling is configured in `app/backend/pyproject.toml`: ruff (lint + format, line-length 100, target py311, ruleset E/F/W/I/B/UP/SIM/RUF), mypy (lenient `strict = false`, `warn_return_any = true`, `ignore_missing_imports = true`), pytest (asyncio auto mode).

```bash
cd app/backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Frontend uses `biome` (one tool, fast):

```bash
cd app/frontend
bun x biome check src
bun x biome format --write src
bun run tsc --noEmit
```

---

## Code Conventions

### Python (backend)

- **Async everywhere.** FastAPI routes are `async def`. Database calls go through the `asyncpg` pool. Sync blocking work in a route handler is a bug — use `asyncio.to_thread` or move it to a background task.
- **Imports:** stdlib, third-party, local — separated by blank lines. No wildcard imports.
- **Type hints:** every function signature and return type. Use `list[str]` / `dict[str, int]` (3.9+ syntax), not `List` / `Dict` from `typing`.
- **No `print()` in runtime code.** Use `logging` with a module-level logger: `logger = logging.getLogger(__name__)`.
- **Errors:** raise specific exceptions with clear messages. No bare `except:`. Avoid `except Exception` outside the outermost request handler. Don't swallow errors — if a tool call fails, the tool result returns `{"ok": false, "error": ...}` so the model can react.
- **SQL:** all queries live in `db/repository.py`. Parameterise — never use f-strings or `%` formatting to build SQL. asyncpg uses `$1, $2, $3` placeholders.
- **Config:** every environment variable is read exactly once in `config.py` and exposed as a module-level constant. Routes and services import the constant, never `os.environ` directly.
- **Pydantic models:** request/response schemas defined in the route file that uses them, unless shared.

### TypeScript (frontend)

- **Function components only.** Named exports, one component per file. File name matches component name.
- **Hooks for state and effects.** Custom hooks live in `src/hooks/`, prefixed `use`, returning a typed object.
- **All API calls go through `src/lib/api.ts`.** Components and hooks import from there. Never `fetch()` inline in a component.
- **Types:** every function signature typed; no `any` except when bridging an untyped dependency with a clear comment explaining why. Prefer `interface` for object shapes, `type` for unions and aliases.
- **Styling:** Tailwind utility classes only. No inline `style={{...}}` except for dynamic values that can't be expressed in Tailwind. No CSS modules, no styled-components.
- **State:** React built-ins (`useState`, `useReducer`, Context) only. No Redux / Zustand / Jotai.
- **SSE parsing:** all SSE consumption goes through the streaming-response hook. Do not parse SSE in components or new hooks.

---

## Database

Postgres via `asyncpg`. Schema is managed by Alembic; on startup the FastAPI lifespan handler runs `alembic upgrade head` before initialising the connection pool. No ORM. No SQLite anywhere.

**Tables (initial migration):**

- Document corpus: `documents`, `chunks` (FK → documents), `source_sync_runs`, `source_sync_items` (FK → source_sync_runs)
- Chat: `conversations`, `messages` (FK → conversations) — both scoped by `user_id`, which the pivot pins to `"default-user"` via `routes/conversations.DEFAULT_USER_ID`
- Vestigial auth tables from the donor (`users`, `user_messages`, `signup_attempts`) still exist but are not wired into any route. They are candidates for removal in a dedicated cleanup migration.

**Rules for database code:**

1. All SQL lives in `db/repository.py` — parameterised, no f-string interpolation.
2. Use `$1, $2, $3...` placeholders for asyncpg.
3. Timestamps are `TIMESTAMPTZ`. Store ISO-8601 strings and let Postgres parse.
4. Document and chat tables use TEXT primary keys (client-friendly UUIDs from `_new_id()`).
5. The hybrid retriever's tsvector column (`chunks.content_tsv`) is maintained by a Postgres trigger declared in the initial migration — application code never writes to it directly.

---

## RAG Pipeline Invariants

These behaviors are part of the product contract and must not regress:

1. **Chunking** uses Docling `HybridChunker` with `OpenAITokenizer(cl100k_base)` and `max_tokens=512` (`HYBRID_CHUNKER_MAX_TOKENS` in `config.py`). Do not swap to recursive-character splitters or LangChain chunkers.
2. **Embeddings** come from OpenRouter's `openai/text-embedding-3-small` (1536-dim). Never call a different embedding model or provider. Never embed on the frontend.
3. **Retrieval** is hybrid: Reciprocal Rank Fusion (k=60, overfetch×2) combining Postgres tsvector keyword search with pgvector cosine similarity. Top-k = 5 by default. Both halves run server-side; do not push retrieval to the client.
4. **Chat completion** uses OpenRouter's `anthropic/claude-sonnet-4.6` via the `openai` SDK pointed at `https://openrouter.ai/api/v1`. The model is configurable via `CHAT_MODEL`, but the provider is not.
5. **Tool-calling loop** (when `LLM_TOOLS_ENABLED=true`, the default): the model can call `search_documents`, `keyword_search_documents`, `semantic_search_documents`, and `get_document`. Per-turn calls are capped by `LLM_TOOLS_MAX_PER_TURN`. The `get_document` tool is whitelisted against the live document catalog.
6. **Streaming format:** Server-Sent Events with JSON-encoded tokens. Each token is framed as `data: <json-string>\n\n`. The `sources` event is emitted as `event: sources\ndata: <json-array>\n\n` **before** the `data: [DONE]\n\n` terminator. Do not change this format — the frontend parser depends on it exactly.
7. **Citation markers** in model output use the content-agnostic form `[c:<chunk_id>]`. The marker stripper in `rag/citations.py` removes them from the streamed text before the client sees it; `extract_cited_chunk_ids` then sets `is_cited=true` on the matching citations. Per-document collapse (`messages._collapse_by_document`) reduces multiple cited chunks from the same document to a single chip.
8. **Refusal detection** (`messages._is_refusal`) recognises the enforced phrase "the documentation library does not cover that topic" plus a curated list of paraphrases observed in evaluation. When a refusal is detected, citations are dropped before persistence so the conversation history stays clean.

---

## Environment Variables

All env var reads happen in `app/backend/config.py`. Add new variables there and import the constant elsewhere.

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** | Authenticates embeddings and chat completions to OpenRouter |
| `DATABASE_URL` | **yes** | Postgres connection string. The app refuses to start without it |
| `CHAT_MODEL` | No | Override the OpenRouter chat-model slug (default `anthropic/claude-sonnet-4.6`) |
| `LLM_REASONING_EFFORT` | No | `low`/`medium`/`high` enables extended thinking on supported models |
| `LLM_TOOLS_ENABLED` | No (default `true`) | Toggle the tool-calling loop |
| `LLM_TOOLS_MAX_PER_TURN` | No (default `6`) | Hard cap on tool calls per user message |
| `RETRIEVAL_EXPANSION_WINDOW` | No (default `1`) | Neighbouring chunks pulled into each retrieval hit |
| `RETRIEVAL_MAX_PER_DOCUMENT` | No (default `3`) | Per-document diversity cap inside a single retrieval |
| `CITATIONS_MAX_COUNT` | No (default `10`) | Max citation chips per assistant message |
| `DOCUMENT_TOOL_MAX_CHARS` | No (default `120000`) | Per-call cap on `get_document` tool output |
| `CATALOG_ENABLED` | No (default `false`) | Inject document catalog into the cached system prompt |
| `CATALOG_TIER` | No (default `standard`) | `standard` (~5 min) or `extended` (1 hour) prompt-cache tier |
| `CATALOG_CACHE_TTL_SECONDS` | No (default `3600`) | TTL for the in-process catalog cache |
| `SOURCE_URL_LIST_PATH` | No | Path to the URL-list markdown file (compose default: `/app/sources/URL List.md`) |
| `SOURCE_VAULT_PATH` | No (blank disables) | Path to an Obsidian vault directory |
| `CRAWLER_USER_AGENT` | No | Polite-crawl identity sent on every HTTP fetch |
| `CRAWLER_REQUEST_DELAY_MS` | No (default `500`) | Throttle between successive crawls |
| `CRAWLER_MAX_RETRIES` | No (default `4`) | Tenacity retry budget for 5xx / network errors |
| `CRAWLER_TIMEOUT_SECONDS` | No (default `30`) | httpx timeout per request |
| `DEFAULT_SOURCE_TYPE` | No (default `firstspirit`) | Tag applied to every ingested document + chunk |
| `CORS_ORIGINS` | No | Comma-separated list. Default permits the same-origin dev server |
| `FRONTEND_DIST` | No | Absolute path to the built frontend bundle (set automatically by Docker) |

**Never commit `.env` files.** The root `.env`, every `.env.*`, and `deploy/.env` are all covered by the gitignore rules. `.env.example` is whitelisted via `!.env.example`.

---

## Deployment

The pivot ships as a single Docker Compose stack (`deploy/docker-compose.yml`):

| Service | Image | Port | Purpose |
|---|---|---|---|
| `firstspirit-docs-rag-postgres` | `pgvector/pgvector:pg16` | `127.0.0.1:5433` | Primary database (loopback-only on host) |
| `firstspirit-docs-rag-app` | Local `deploy/Dockerfile` build | `localhost:8000` | FastAPI + built frontend in one container |

The app container publishes 8000 directly to the host — there's no reverse proxy in front. If you ever sit this behind one (Caddy, nginx, Cloudflare Tunnel), edit the `CMD` in `deploy/Dockerfile` to add `--proxy-headers --forwarded-allow-ips=<proxy-ip>` so Starlette will trust forwarded client IPs from that proxy and *only* that proxy.

The vault mount is optional. When `VAULT_HOST_PATH` is left at its default the compose file binds the committed `deploy/sources/empty-vault/` placeholder so `docker compose up` always succeeds; setting `SOURCE_VAULT_PATH_IN_CONTAINER=/app/vault` then activates the vault ingester.

---

## Commit and PR Conventions

- **Commit messages:** conventional commits — `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`. Subject under 72 characters. Body explains *why*, not *what*.
- **PR title:** same conventional-commits prefix. Under 72 characters.
- **New dependencies:** PR body must include a short note explaining what the dep does, why existing deps don't work, and evidence of active maintenance.
- **One concern per PR.** Don't bundle unrelated fixes.

---

## Dos and Don'ts (Quick Reference)

**Do:**

- Keep all SQL in `db/repository.py`.
- Keep all `fetch()` calls in `src/lib/api.ts`.
- Use Alembic migrations for schema changes — never `CREATE TABLE` at runtime.
- Add tests for every bug fix (regression test) and every new feature.
- Run `ruff check`, `mypy`, `pytest`, and the frontend equivalents before declaring a PR done.

**Don't:**

- Introduce a new LLM provider, embedding model, or vector database. Hybrid RRF over pgvector + tsvector is the contract.
- Add state-management libraries to the frontend (Redux, Zustand, Jotai…).
- Add an ORM to the backend.
- Write SQL outside `db/repository.py` or fetch calls outside `src/lib/api.ts`.
- Wire an auth layer onto a single route — the pivot is auth-free by design. If real auth is needed, that's a project-wide change, not a per-route patch.
- "Improve" code that wasn't part of the change you're making — scope discipline.
- Commit `.env`, `deploy/.env`, or any file containing real secrets.
