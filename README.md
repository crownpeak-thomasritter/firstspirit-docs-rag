# FirstSpirit Docs RAG

A self-hosted RAG chat app over the FirstSpirit / Crownpeak product documentation and your own Obsidian notes. Ask a question, get a streamed answer grounded in the docs with clickable citations that link back to the exact source URL or note.

Single-tenant, single-team. No login, no per-user accounts — the whole stack is a single anonymous identity behind the scenes. Designed to run on your laptop or a small VPS with one `docker compose up`.

---

## What you get

- **Hybrid retrieval** — Reciprocal Rank Fusion over Postgres `tsvector` (keyword) and `pgvector` cosine similarity (semantic). Top-k = 5 by default.
- **Two ingestion pipelines:**
  - **URL list** — a markdown file with one URL per line. The crawler fetches each page (HTML → markdown via `trafilatura`, PDFs via `pymupdf4llm`), chunks with Docling's `HybridChunker` (cl100k_base, 512-token max), and embeds via OpenRouter.
  - **Vault** — recursively reads an Obsidian-style directory of markdown files, honours YAML frontmatter (`title`, `description`, `lang`, `source`), and ingests the body the same way.
- **Tool-calling LLM** — the chat model can issue `search_documents`, `keyword_search_documents`, `semantic_search_documents`, and `get_document` calls during a turn (capped at 6 per turn by default).
- **Citations** that group multiple chunks from the same document into a single chip and deep-link to the source URL.
- **Streamed responses** over SSE with content-agnostic `[c:<chunk_id>]` markers stripped before they reach the client.
- **Postgres-only persistence**, schema managed by Alembic — `alembic upgrade head` runs automatically on startup.

---

## Quick start (Docker — recommended)

Prerequisites: Docker Desktop / Docker Engine + Compose v2.

```bash
git clone <this-repo>
cd firstspirit-docs-rag

cp deploy/.env.example deploy/.env
# Edit deploy/.env — at minimum set:
#   POSTGRES_PASSWORD=<choose anything>
#   OPENROUTER_API_KEY=<your OpenRouter key>

docker compose -f deploy/docker-compose.yml up -d --build
```

Open <http://localhost:8000>.

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

Useful when you want hot reload on the backend or frontend separately. Postgres still has to be running — easiest is to bring up just that service:

```bash
docker compose -f deploy/docker-compose.yml up -d postgres
```

Then copy the root template and fill it in:

```bash
cp .env.example .env
# Set DATABASE_URL=postgresql://docs_rag:<password>@127.0.0.1:5433/docs_rag
# Set OPENROUTER_API_KEY=...
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
                              │     │                       retriever  │
                              │     ▼                       (RRF over  │
                              │   db/repository.py           tsvector  │
                              │     │                        + pgvector)│
                              │     ▼                       tools      │
                              │   asyncpg pool   ──▶  llm/openrouter   │
                              │                       (Claude Sonnet)  │
                              └────────────────────────────────────────┘
                                              │
                                              ▼
                                  ┌────────────────────────┐
                                  │   Postgres + pgvector  │
                                  │  documents, chunks,    │
                                  │  conversations,        │
                                  │  messages, sync runs   │
                                  └────────────────────────┘
```

- **Ingest** — `POST /api/sources/sync` reads either the URL list or the vault, extracts markdown, chunks via Docling, embeds via OpenRouter, and writes to `documents` + `chunks` (with content-hash idempotency on re-runs).
- **Retrieve** — At chat time, the user's query is embedded once and reused across tool calls (in-process cache). The hybrid retriever runs keyword + semantic searches in parallel and fuses them via RRF.
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
