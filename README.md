# FirstSpirit Docs RAG

A self-hosted RAG chat app over the FirstSpirit / Crownpeak product documentation and your own Obsidian notes. Ask a question, get a streamed answer grounded in the docs with clickable citations that link back to the exact source URL or note.

Single-tenant, single-team. No login, no per-user accounts — the whole stack is a single anonymous identity behind the scenes. Designed to run on your laptop or a small VPS with one `docker compose up`.

---

## What you get

- **Hybrid retrieval** — Qdrant's server-side RRF over a dense vector half (1536-dim cosine, OpenAI `text-embedding-3-small`) and a sparse BM25 vector half (Qdrant/bm25 via FastEmbed). Single round-trip via the Query API. Top-k = 5 by default; RRF `k=60`.
- **Two ingestion pipelines:**
  - **URL list** — a markdown file with one URL per line. The crawler fetches each page (HTML → markdown via `trafilatura`, PDFs via `pymupdf4llm`), chunks with Docling's `HybridChunker` (cl100k_base, 512-token max), embeds via the configured provider, then dual-writes metadata to SQLite and vectors+payload to Qdrant.
  - **Vault** — recursively reads an Obsidian-style directory of markdown files, honours YAML frontmatter (`title`, `description`, `lang`, `source`), and ingests the body the same way.
- **Tool-calling LLM** — the chat model can issue `search_documents`, `keyword_search_documents`, `semantic_search_documents`, and `get_document` calls during a turn (capped at 6 per turn by default).
- **Citations** that group multiple chunks from the same document into a single chip and deep-link to the source URL.
- **Streamed responses** over SSE with content-agnostic `[c:<chunk_id>]` markers stripped before they reach the client.
- **Pluggable LLM + embedding provider** — pick OpenRouter (default) or OpenAI native independently for chat and embeddings via `LLM_PROVIDER` / `EMBEDDING_PROVIDER`.
- **SQLite for chat + metadata, Qdrant for vectors.** A local Qdrant container ships with the compose stack for dev/testing; production swaps in Qdrant Cloud via two env vars. No Postgres container to maintain; SQLite schema is managed by Alembic and `alembic upgrade head` runs automatically on startup.

---

## Quick start (Docker — recommended)

Prerequisites: Docker Desktop / Docker Engine + Compose v2.

```bash
git clone <this-repo>
cd firstspirit-docs-rag

cp deploy/.env.example deploy/.env
# Edit deploy/.env — at minimum set one of:
#   OPENROUTER_API_KEY=<your OpenRouter key>   # if LLM/EMBEDDING_PROVIDER=openrouter (default)
#   OPENAI_API_KEY=<your OpenAI key>            # if LLM/EMBEDDING_PROVIDER=openai
#
# Qdrant defaults to the bundled `qdrant` container — no edit needed for
# local dev. To use Qdrant Cloud instead, see the "Switching to Qdrant
# Cloud" section below.

docker compose -f deploy/docker-compose.yml up -d --build
```

Open <http://localhost:8000>. The Qdrant dashboard is on <http://localhost:6333/dashboard>.

### Switching to Qdrant Cloud (production)

The bundled `qdrant` service is for dev. In production, point `QDRANT_URL` + `QDRANT_API_KEY` in `deploy/.env` at your managed cluster and the app will skip the local container:

```ini
QDRANT_URL=https://<your-cluster>.cloud.qdrant.io
QDRANT_API_KEY=<your Qdrant Cloud key>
```

If you don't want the unused local Qdrant container running at all, bring the stack up with `--scale qdrant=0`:

```bash
docker compose -f deploy/docker-compose.yml up -d --build --scale qdrant=0
```

### Adding sources

Drop URLs into `deploy/sources/URL List.md` (one per line, `#` lines and blanks are ignored, markdown link syntax `[label](url)` is parsed). Then trigger ingestion:

```bash
curl -X POST http://localhost:8000/api/sources/sync \
  -H 'Content-Type: application/json' \
  -d '{"kind":"url_list"}'
```

For an Obsidian vault, set in `deploy/.env`:

```ini
# Windows path — use forward slashes inside docker env values
VAULT_HOST_PATH=C:/Users/you/OneDrive/Obsidian/Docs
SOURCE_VAULT_PATH_IN_CONTAINER=/app/vault
```

Restart the stack and trigger:

```bash
docker compose -f deploy/docker-compose.yml up -d
curl -X POST http://localhost:8000/api/sources/sync \
  -H 'Content-Type: application/json' \
  -d '{"kind":"vault"}'
```

### Health and history

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/sources/sync-runs
curl http://localhost:8000/api/sources/documents
```

---

## Manual dev (no Docker)

Useful when you want hot reload on the backend or frontend separately. SQLite is local (the data directory is created on first start). For Qdrant, the easiest option is to bring up just the bundled container:

```bash
docker compose -f deploy/docker-compose.yml up -d qdrant
```

This exposes Qdrant on `127.0.0.1:6333`, which the root `.env.example` already points at. For Qdrant Cloud instead, override `QDRANT_URL` + `QDRANT_API_KEY`.

Copy the root template and fill it in:

```bash
cp .env.example .env
# Set DATABASE_URL=sqlite+aiosqlite:///./data/app.db
# QDRANT_URL defaults to http://localhost:6333 (bundled container) — override
#   to https://<your-cluster>.cloud.qdrant.io for Qdrant Cloud
# Set QDRANT_API_KEY=<your Qdrant Cloud key> (leave blank for local Qdrant)
# Set OPENROUTER_API_KEY=... (or OPENAI_API_KEY=... when using OpenAI native)
```

### Choosing a provider

Both halves of the LLM stack are independently configurable:

| Env var | Values | Default |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` \| `openai` | `openrouter` |
| `EMBEDDING_PROVIDER` | `openrouter` \| `openai` | `openrouter` |

`LLM_PROVIDER=openrouter` keeps Anthropic prompt-cache support (`cache_control: {"type": "ephemeral"}` blocks) on the system prompt; `LLM_PROVIDER=openai` strips those blocks (OpenAI native rejects extra keys with HTTP 400) and relies on OpenAI's automatic prompt cache instead.

### Migrating from a Postgres install

`scripts/migrate_pg_to_qdrant.py` is a one-shot, idempotent CLI for users who already have data in the legacy Postgres+pgvector setup. It reads documents and chunks from a Postgres DSN, inserts the metadata into SQLite, and upserts dense vectors + payloads to Qdrant. The BM25 sparse vectors are re-derived locally via FastEmbed.

```bash
uv run --with asyncpg python scripts/migrate_pg_to_qdrant.py \
    --pg-dsn postgresql://docs_rag:pw@localhost:5433/docs_rag \
    --sqlite-path ./data/app.db \
    --qdrant-url https://<your-cluster>.cloud.qdrant.io \
    --qdrant-api-key <key>
```

**Backend** (from the repo root):

```bash
cd app/backend
uv sync --all-extras
cd ..
uv --project backend run uvicorn backend.main:app --reload --port 8000
```

`uvicorn` must be launched from `app/`, not `app/backend/` — `backend.main:app` is a module path that needs `app/` on `sys.path`.

**Frontend** (separate terminal):

```bash
cd app/frontend
bun install
bun run dev
```

Visit <http://localhost:5173>. The Vite proxy forwards `/api/*` to the backend on port 8000.

---

## Architecture

```
┌──────────────────────┐      ┌────────────────────────────────────────┐
│      Frontend        │      │              Backend                   │
│  React + Vite + TS   │ ◀──▶ │              FastAPI                   │
│  Tailwind CSS        │      │                                        │
│                      │      │  routes/  ──▶  ingest/  ──▶  rag/      │
└──────────────────────┘      │     │                       chunker    │
                              │     │                       embeddings │
                              │     │                       vector_store
                              │     │                       (Qdrant)   │
                              │     ▼                       tools      │
                              │   db/repository.py   ──▶  llm/chat     │
                              │   (aiosqlite)              (OpenRouter │
                              │     │                       OR OpenAI) │
                              │     ▼                                  │
                              └────────────────────────────────────────┘
                                       │                  │
                            ┌──────────▼───────┐ ┌────────▼─────────┐
                            │     SQLite       │ │   Qdrant Cloud   │
                            │  documents,      │ │   dense + BM25   │
                            │  chunks,         │ │   vectors,       │
                            │  conversations,  │ │   payload =      │
                            │  messages,       │ │   chunk metadata │
                            │  sync runs       │ │                  │
                            └──────────────────┘ └──────────────────┘
```

- **Ingest** — `POST /api/sources/sync` reads either the URL list or the vault, extracts markdown, chunks via Docling, embeds via the configured provider, then writes chunk metadata to SQLite and vectors+payload to Qdrant in lockstep.
- **Retrieve** — At chat time, the user's query is embedded once and reused across tool calls. The hybrid retriever delegates to Qdrant's Query API with `prefetch=[dense, sparse]` + `fusion=RRF`, returning a single ranked list in one round-trip.
- **Generate** — `POST /api/conversations/{id}/messages` opens an SSE stream, runs the tool-calling loop, strips citation markers, attaches the `sources` event, and persists the final assistant message with its citations.

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness — returns document + chunk counts |
| `GET` | `/api/version` | Backend package version |
| `POST` | `/api/sources/sync` | `{"kind":"url_list"\|"vault"}` — runs the ingester synchronously |
| `GET` | `/api/sources/sync-runs` | Last 10 ingest runs across both pipelines |
| `GET` | `/api/sources/documents` | Document catalog with chunk counts (admin view) |
| `GET` | `/api/documents` | Public document catalog |
| `GET` | `/api/conversations` | List conversations |
| `POST` | `/api/conversations` | Create a conversation |
| `GET` | `/api/conversations/search?q=...` | Title-contains search |
| `GET` | `/api/conversations/{id}` | Conversation + messages |
| `PATCH` | `/api/conversations/{id}` | Rename |
| `DELETE` | `/api/conversations/{id}` | Delete |
| `POST` | `/api/conversations/{id}/messages` | Send a message; streams the assistant reply over SSE |

---

## Working on the code

See [`CLAUDE.md`](CLAUDE.md) for the full set of code conventions, repo layout rules, and the RAG pipeline invariants that must not regress.

Run the test suite:

```bash
cd app/backend && uv run pytest tests -xvs
cd app/frontend && bun run test
```

Run the linters:

```bash
cd app/backend
uv run ruff check . && uv run ruff format --check . && uv run mypy .

cd app/frontend
bun x biome check src && bun run tsc --noEmit
```

---

## License

Internal project. Adapt as needed; do not redistribute the indexed corpus without permission from the source owners.
