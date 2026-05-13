# syntax=docker/dockerfile:1.7
#
# FirstSpirit Docs RAG single-container image.
#   Stage 1: build the frontend with Bun  → /src/frontend/dist
#   Stage 2: install backend with uv and serve FastAPI + the built frontend.
#
# Build context is the repo root (see deploy/docker-compose.yml → build.context: ..).

# ----------------------------------------------------------------------------
# Stage 1 — frontend build
# ----------------------------------------------------------------------------
FROM oven/bun:1.2-alpine AS frontend-build

WORKDIR /src/frontend

# Install deps first for better layer caching. The lockfile is committed; we
# refuse to start the build if it's missing so we never quietly ship a
# different dep tree than dev sees.
COPY app/frontend/package.json app/frontend/bun.lock ./
RUN bun install --frozen-lockfile

# Now bring in the source and build.
COPY app/frontend/ ./
RUN bun run build
# Output lives at /src/frontend/dist

# ----------------------------------------------------------------------------
# Stage 2 — backend runtime
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Pull in a pinned uv binary. uv is the only package manager the backend
# understands; pip/poetry are not in the image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Minimal runtime dependencies. `curl` is optional but handy for in-container
# debugging; the healthcheck uses Python's stdlib so we avoid any external
# tool dependency for the critical path.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. Create /app ownership BEFORE any volume mounts so a named
# volume inherits correct ownership on first creation.
RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home /app app

WORKDIR /app

# Install Python deps first (separate layer for cache hits on source-only changes).
COPY --chown=app:app app/backend/pyproject.toml app/backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev

# Copy backend source.
COPY --chown=app:app app/backend/ ./backend/

# Pull the built frontend from stage 1 into /app/frontend/dist.
COPY --from=frontend-build --chown=app:app /src/frontend/dist /app/frontend/dist

# Provide a writable data dir. SQLite (chat + metadata) lives at
# /app/data/app.db; the FastEmbed BM25 ONNX model is cached at
# /app/data/fastembed-cache. The compose file mounts the `sqlite_data`
# named volume at /app/data so both survive container restarts.
RUN mkdir -p /app/data /app/data/fastembed-cache && chown -R app:app /app

USER app

ENV FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Healthcheck hits /api/health, which itself counts rows in SQLite — so a
# green status proves the app is genuinely ready, not just that the process
# is listening.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)" || exit 1

# Run from /app so the `backend.main:app` module path resolves. We don't
# enable --proxy-headers here because the pivot's compose publishes 8000
# directly to the host (no reverse proxy in front) — trusting forwarded
# headers would let any caller spoof their client IP. If you put this image
# behind a real reverse proxy later, add `--proxy-headers --forwarded-allow-ips="<proxy-ip>"`.
CMD ["uv", "--project", "backend", "run", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
