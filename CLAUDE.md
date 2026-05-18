# CLAUDE.md

Instructions for AI coding agents working in this repository. Read this before making any code changes.

---

## Project Overview

**FirstSpirit Docs RAG** is a single-tenant chat application that lets a small team (today, the Crownpeak / FirstSpirit professional-services team) ask grounded questions of the FirstSpirit + Crownpeak product documentation and internal Obsidian notes. Streaming answers come back with per-chunk citations that deep-link to the source URL or note, anchored at the cited section when available.

The codebase started as a fork of DynaChat (YouTube-RAG) and has been pivoted away from videos: the ingest pipeline is now URL-list crawling plus an Obsidian vault reader, citations point at document/section/anchor instead of YouTube timestamps, and the auth stack is intentionally absent. A single `DEFAULT_USER_ID = "default-user"` anonymous identity satisfies the underlying repository's `user_id`-scoped signatures.

FastAPI + Python backend, React + Vite + TypeScript frontend. SQLite (via `aiosqlite`) holds chat and document metadata; Qdrant Cloud holds the dense + BM25 vectors and serves hybrid retrieval server-side via the Query API. LLM chat and embedding providers are independently configurable — OpenRouter (default) or OpenAI native. Shipped as a single Docker Compose stack: `docker compose up` brings up the FastAPI app container against managed Qdrant; no Postgres container, no auth provider, no external proxy required.

---

## Tech Stack

**Backend**
- Python 3.11+ (don't rely on 3.12+ features — the runtime image pins 3.11-slim)
- `uv` for package management — `app/backend/pyproject.toml` is the dependency source of truth, `app/backend/uv.lock` pins exact versions
- FastAPI with `uvicorn[standard]` ASGI server
- `aiosqlite` for async SQLite access (per-call connection via `db/sqlite.py`)
- `sqlalchemy` (sync engine) used only by Alembic for the migration runner
- `qdrant-client[fastembed]` for the Qdrant Cloud client and the in-process BM25 sparse encoder
- `alembic` for schema migrations (single migration layer; ships with one initial revision)
- `docling-core[chunking,chunking-openai]` for markdown chunking (HybridChunker with cl100k_base tokenizer, max 512 tokens)
- `openai` SDK pointed at either OpenRouter's OpenAI-compatible endpoint or OpenAI native — provider chosen per workload via `llm/providers.py`
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
│   ├── docker-compose.yml   # Single app container; SQLite volume + managed Qdrant
│   ├── .env.example         # Compose-specific env template (QDRANT_*, OPENROUTER_/OPENAI_*)
│   ├── .dockerignore        # Keeps secrets and dev cruft out of image layers
│   └── sources/
│       ├── URL List.md      # Seed file — one URL per line, '#' lines ignored
│       └── empty-vault/     # Placeholder so the optional vault mount always has a target
├── scripts/
│   └── migrate_pg_to_qdrant.py  # One-shot CLI for legacy Postgres → SQLite + Qdrant
└── app/
    ├── backend/
    │   ├── main.py          # FastAPI app, lifespan: alembic upgrade + SQLite init + Qdrant ensure_collection
    │   ├── config.py        # All env reads + hardcoded constants
    │   ├── pyproject.toml   # uv deps + tool config (ruff, mypy, pytest)
    │   ├── uv.lock          # pinned lockfile (committed)
    │   ├── alembic/         # Migrations — single initial revision creates all tables
    │   ├── alembic.ini
    │   ├── db/
    │   │   ├── sqlite.py    # aiosqlite per-call connection + auto-commit _acquire()
    │   │   └── repository.py # ALL raw SQL lives here — nowhere else
    │   ├── ingest/
    │   │   ├── crawler.py   # httpx + tenacity HTTP fetcher (conditional GET, polite delay)
    │   │   ├── url_list.py  # Reads a markdown file of URLs, crawls and ingests
    │   │   └── vault.py     # Recursively reads an Obsidian-style markdown vault
    │   ├── llm/
    │   │   ├── chat.py      # stream_chat() async generator, SSE-formatted output (provider-agnostic)
    │   │   └── providers.py # AsyncOpenAI / OpenAI client factory (OpenRouter or OpenAI native)
    │   ├── rag/
    │   │   ├── catalog.py        # In-process document catalog cache; builds cache_control block
    │   │   ├── citations.py      # `[c:<id>]` marker stripper + cited-chunk extraction
    │   │   ├── document_chunker.py # Docling HybridChunker wrapper for markdown
    │   │   ├── embeddings.py     # embed_text / embed_batch via the configured provider
    │   │   ├── vector_store.py   # Qdrant wrapper — dense + BM25 sparse, server-side RRF
    │   │   ├── retriever_hybrid.py # Thin shim over vector_store.hybrid_search
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

### Docker (recommended — single app container against managed Qdrant)

From the repo root:

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env: set QDRANT_URL, QDRANT_API_KEY, and OPENROUTER_API_KEY (default
# provider) or OPENAI_API_KEY (set LLM_PROVIDER=openai / EMBEDDING_PROVIDER=openai
# to use OpenAI native). Optionally point VAULT_HOST_PATH at your real Obsidian
# vault and set SOURCE_VAULT_PATH_IN_CONTAINER=/app/vault.

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

Useful for active development on the backend or frontend. SQLite is local (the data directory is created on first start) and Qdrant Cloud is managed — no other services need to be brought up by hand.

Copy the root `.env.example` to `.env` and fill in `DATABASE_URL` (default `sqlite+aiosqlite:///./data/app.db`), `QDRANT_URL`, `QDRANT_API_KEY`, and the API key for your active provider (`OPENROUTER_API_KEY` by default; `OPENAI_API_KEY` when `LLM_PROVIDER=openai` / `EMBEDDING_PROVIDER=openai`).

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

Test suite covers the repository SQLite port, the Qdrant vector-store wrapper, the LLM provider factory, the retriever shim, the ingest pipelines (dual-write to SQLite + Qdrant), and the LLM tool dispatcher. All external boundaries (HTTP via `httpx.MockTransport` / `respx`, the embedder, the Qdrant client, FastEmbed BM25, the repository) are mocked — the suite does not require a running Qdrant, OpenRouter/OpenAI access, or any secret. `pytest-asyncio` is configured with `asyncio_mode = "auto"`, so plain `async def` test functions Just Work.

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

- **Async everywhere.** FastAPI routes are `async def`. Database calls go through `aiosqlite` connections acquired via `db/sqlite._acquire`. Sync blocking work in a route handler is a bug — use `asyncio.to_thread` or move it to a background task.
- **Imports:** stdlib, third-party, local — separated by blank lines. No wildcard imports.
- **Type hints:** every function signature and return type. Use `list[str]` / `dict[str, int]` (3.9+ syntax), not `List` / `Dict` from `typing`.
- **No `print()` in runtime code.** Use `logging` with a module-level logger: `logger = logging.getLogger(__name__)`.
- **Errors:** raise specific exceptions with clear messages. No bare `except:`. Avoid `except Exception` outside the outermost request handler. Don't swallow errors — if a tool call fails, the tool result returns `{"ok": false, "error": ...}` so the model can react.
- **SQL:** all queries live in `db/repository.py`. Parameterise — never use f-strings or `%` formatting to build SQL. aiosqlite uses positional `?` placeholders. For dynamic `IN` lists, generate the placeholder string from `len(values)` (never interpolate the values themselves) and spread the params with `*`.
- **Vector queries:** all Qdrant calls live in `rag/vector_store.py`. Route handlers and tool executors talk to Qdrant only via that module.
- **Provider clients:** LLM and embedding clients come from `llm/providers.py` factories — `get_async_chat_client()` / `get_sync_embed_client()`. Never construct `AsyncOpenAI` / `OpenAI` directly elsewhere.
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

## Database + Vector Store

SQLite (via `aiosqlite`) is the chat + metadata store. Qdrant Cloud is the vector store. Schema for SQLite is managed by Alembic — on startup the FastAPI lifespan handler runs `alembic upgrade head` before initialising the SQLite layer; the Qdrant collection is ensured separately on lifespan startup via `vector_store.ensure_collection()`. No ORM at runtime — `repository.py` is raw SQL via `aiosqlite`.

**SQLite tables (managed via Alembic — `alembic upgrade head` on startup):**

- Document corpus: `documents`, `document_chunks` (FK → documents, ON DELETE CASCADE), `source_sync_runs`, `source_sync_items` (FK → source_sync_runs)
- Chat: `conversations`, `messages` (FK → conversations) — both scoped by `user_id`, which the pivot pins to `"default-user"` via `routes/conversations.DEFAULT_USER_ID`
- Feedback: `feedback_submissions` — one row per "Report this answer" submission. FK cascades on both `message_id` and `conversation_id`. `payload_json` is a snapshot of the question + answer + citations taken at submit time so the audit trail survives later edits or deletes of the underlying message.
- `document_chunks` no longer carries `embedding` or `search_vector` columns — vectors live in Qdrant.

**Qdrant collection (default `firstspirit_docs`):**

- One collection with named vectors: `dense` (1536-dim cosine, OpenAI `text-embedding-3-small`) and `bm25` (sparse, IDF modifier, FastEmbed `Qdrant/bm25`).
- Each point's payload carries the full chunk metadata (`chunk_id`, `document_id`, `content`, `chunk_index`, `section_path`, `anchor`, `char_start`, `char_end`, `source_type`, `document_title`, `document_url`, `document_content_path`) so retrieval returns everything citations need in one round-trip — no second SQLite lookup.
- Payload indexes on `document_id` and `source_type` for filtering.

**Rules for persistence code:**

1. All SQL lives in `db/repository.py` — parameterised, no f-string interpolation.
2. Use positional `?` placeholders for aiosqlite. For dynamic `IN` lists, generate the placeholder string from `len(values)` and spread the params with `*`.
3. Timestamps are stored as ISO-8601 TEXT (the app calls `datetime.isoformat()` before insert).
4. Document and chat tables use TEXT primary keys (client-friendly UUIDs from `_new_id()`).
5. Chunk IDs are generated client-side in the ingest pipeline and supplied to both `repository.replace_chunks_for_document` and `vector_store.upsert_chunks` so SQLite and Qdrant stay in sync. The ingester rolls back the SQLite chunks if the Qdrant upsert fails.
6. The `_acquire()` context manager in `db/sqlite.py` auto-commits on clean exit and rolls back on exception. Don't add explicit `await conn.commit()` calls in repository functions.
7. All Qdrant calls live in `rag/vector_store.py` — never call the Qdrant client from elsewhere.

---

## RAG Pipeline Invariants

These behaviors are part of the product contract and must not regress:

1. **Chunking** uses Docling `HybridChunker` with `OpenAITokenizer(cl100k_base)` and `max_tokens=512` (`HYBRID_CHUNKER_MAX_TOKENS` in `config.py`). Do not swap to recursive-character splitters or LangChain chunkers.
2. **Embeddings** come from OpenRouter's `openai/text-embedding-3-small` (1536-dim). Never call a different embedding model or provider. Never embed on the frontend.
3. **Retrieval** is hybrid via Qdrant's Query API — server-side RRF (k=60 fixed by Qdrant), prefetch over dense (1536-dim cosine) + sparse (BM25 IDF modifier via FastEmbed). Top-k = 5 by default. Both halves run server-side; do not push retrieval to the client.
4. **Chat completion** uses the `openai` SDK pointed at either OpenRouter (`LLM_PROVIDER=openrouter`, default) with `anthropic/claude-sonnet-4.6`, or OpenAI native (`LLM_PROVIDER=openai`) with a model of the operator's choice. The chat model is configurable via `CHAT_MODEL`; the provider is selected by `LLM_PROVIDER`. `EMBEDDING_PROVIDER` is independent.
5. **Tool-calling loop** (when `LLM_TOOLS_ENABLED=true`, the default): the model can call `search_documents`, `keyword_search_documents`, `semantic_search_documents`, and `get_document`. Per-turn calls are capped by `LLM_TOOLS_MAX_PER_TURN`. The `get_document` tool is whitelisted against the live document catalog.
6. **Streaming format:** Server-Sent Events with JSON-encoded tokens. Each token is framed as `data: <json-string>\n\n`. The `sources` event is emitted as `event: sources\ndata: <json-array>\n\n` **before** the `data: [DONE]\n\n` terminator. Do not change this format — the frontend parser depends on it exactly.
7. **Citation markers** in model output use the content-agnostic form `[c:<chunk_id>]`. The marker stripper in `rag/citations.py` removes them from the streamed text before the client sees it; `extract_cited_chunk_ids` then sets `is_cited=true` on the matching citations. Per-document collapse (`messages._collapse_by_document`) reduces multiple cited chunks from the same document to a single chip.
8. **Refusal detection** (`messages._is_refusal`) recognises the enforced phrase "the documentation library does not cover that topic" plus a curated list of paraphrases observed in evaluation. When a refusal is detected, citations are dropped before persistence so the conversation history stays clean.

---

## Environment Variables

All env var reads happen in `app/backend/config.py`. Add new variables there and import the constant elsewhere.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | **yes** | SQLite connection string (e.g. `sqlite+aiosqlite:///./data/app.db`). The app refuses to start without it |
| `QDRANT_URL` | **yes** | Qdrant Cloud cluster URL. The app refuses to start without it |
| `QDRANT_API_KEY` | **yes** | Qdrant Cloud API key |
| `QDRANT_COLLECTION` | No (default `firstspirit_docs`) | Qdrant collection name |
| `QDRANT_BM25_MODEL` | No (default `Qdrant/bm25`) | FastEmbed BM25 model id |
| `LLM_PROVIDER` | No (default `openrouter`) | `openrouter` \| `openai` |
| `EMBEDDING_PROVIDER` | No (default `openrouter`) | `openrouter` \| `openai` |
| `OPENROUTER_API_KEY` | conditional | Required when either provider is `openrouter` |
| `OPENAI_API_KEY` | conditional | Required when either provider is `openai` |
| `OPENAI_BASE_URL` | No (default `https://api.openai.com/v1`) | Override the OpenAI-native base URL |
| `CHAT_MODEL` | No | Chat model id. OpenRouter slug (default `anthropic/claude-sonnet-4.6`) or OpenAI model name (e.g. `gpt-4o`) |
| `EMBEDDING_MODEL` | No | Embedding model id (default `openai/text-embedding-3-small`; the `openai/` prefix is stripped when `EMBEDDING_PROVIDER=openai`) |
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
| `FASTEMBED_CACHE_PATH` | No | Where FastEmbed caches the BM25 ONNX model (compose sets `/app/data/fastembed-cache`) |
| `FEEDBACK_ENABLED` | No (default `false`) | Master toggle for the "Report this answer" flow. When `false` the route returns 503 and the UI hides the button |
| `FEEDBACK_GITHUB_REPO` | conditional | `owner/name` of the GitHub repo where feedback issues are filed. Required when feedback is enabled |
| `FEEDBACK_GITHUB_TOKEN` | conditional | GitHub PAT (classic or fine-grained) with `Issues: read & write` on `FEEDBACK_GITHUB_REPO`. An empty value disables the feature even when `FEEDBACK_ENABLED=true` (a startup warning is printed). See `.env.example` for token-scope guidance |
| `FEEDBACK_MAX_CORRECTION_CHARS` | No (default `5000`) | Server-side cap on `suggested_correction` length |
| `GITHUB_MAX_RETRIES` | No (default `4`) | Tenacity retry budget for 429 / 5xx / transport errors when calling the GitHub Issues API |

**Never commit `.env` files.** The root `.env`, every `.env.*`, and `deploy/.env` are all covered by the gitignore rules. `.env.example` is whitelisted via `!.env.example`.

---

## Deployment

The pivot ships as a Docker Compose stack (`deploy/docker-compose.yml`):

| Service | Image | Port | Purpose |
|---|---|---|---|
| `firstspirit-docs-rag-app` | Local `deploy/Dockerfile` build | `localhost:8000` | FastAPI + built frontend in one container; SQLite at `/app/data/app.db` on a named volume |
| `firstspirit-docs-rag-qdrant` | `qdrant/qdrant:v1.18.0` | `127.0.0.1:6333` (HTTP) / `127.0.0.1:6334` (gRPC) | Local Qdrant for dev/testing; storage on a named `qdrant_data` volume |

In production, override `QDRANT_URL` + `QDRANT_API_KEY` in `deploy/.env` to point at Qdrant Cloud. The bundled `qdrant` service can then be left running idle, or skipped at startup with `docker compose ... up -d --scale qdrant=0`. The app reaches the local container via the compose-network DNS name `qdrant`; from the host (manual dev) it's `localhost:6333`.

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

- Introduce a new vector database or replace the hybrid retrieval contract. Qdrant (server-side RRF over dense + BM25 sparse, k=60) is the contract.
- Add state-management libraries to the frontend (Redux, Zustand, Jotai…).
- Add an ORM to the backend at runtime. SQLAlchemy is used only by Alembic's migration runner.
- Write SQL outside `db/repository.py`, Qdrant calls outside `rag/vector_store.py`, or `fetch()` calls outside `src/lib/api.ts`.
- Construct `AsyncOpenAI` / `OpenAI` clients directly — use the `llm/providers.py` factories.
- Wire an auth layer onto a single route — the pivot is auth-free by design. If real auth is needed, that's a project-wide change, not a per-route patch.
- "Improve" code that wasn't part of the change you're making — scope discipline.
- Commit `.env`, `deploy/.env`, or any file containing real secrets.
