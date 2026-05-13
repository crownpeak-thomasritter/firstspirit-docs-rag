"""FastAPI application entry point — FirstSpirit Docs RAG.

Lifespan responsibilities:

1. Run Alembic migrations to ``head`` (creates documents / chunks / sync /
   conversation tables on first start; no-ops thereafter).
2. Initialise the Postgres connection pool used by every repository call.

The pivot drops the donor's auth stack — no signup, no login, no
``Depends(get_current_user)`` wiring. Three routers are mounted:

* :mod:`backend.routes.sources` — admin ingest + sync history + document catalog.
* :mod:`backend.routes.conversations` — conversation CRUD and public document list.
* :mod:`backend.routes.messages` — streaming RAG chat with citations.
"""

from __future__ import annotations

import logging
import os
import subprocess
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as get_version
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.config import CORS_ORIGINS, DATABASE_URL, FRONTEND_DIST
from backend.db.postgres import close_pg_pool, init_pg_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run Alembic migrations, then initialise the Postgres pool."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set — the app refuses to start without it.")

    logger.info("Running Alembic migrations…")
    backend_dir = Path(__file__).resolve().parent
    alembic_cfg = backend_dir / "alembic.ini"
    alembic_cwd = backend_dir.parent
    result = subprocess.run(
        [
            "uv",
            "run",
            "alembic",
            "--config",
            str(alembic_cfg),
            "upgrade",
            "head",
        ],
        capture_output=True,
        text=True,
        cwd=str(alembic_cwd),
        check=False,
    )
    if result.returncode != 0:
        logger.error("Alembic migration failed. stdout=%s stderr=%s", result.stdout, result.stderr)
        raise RuntimeError(
            f"Alembic upgrade head failed: stdout={result.stdout} stderr={result.stderr}"
        )
    logger.info("Alembic migrations applied.")

    await init_pg_pool()
    logger.info("Postgres pool initialised.")

    yield
    logger.info("Shutting down.")
    await close_pg_pool()


app = FastAPI(title="FirstSpirit Docs RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
from backend.routes import conversations, messages, sources  # noqa: E402

app.include_router(sources.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(messages.router, prefix="/api")


# ---------------------------------------------------------------------------
# Health + version
# ---------------------------------------------------------------------------
from backend.db import repository  # noqa: E402


@app.get("/api/health")
async def health() -> dict[str, object]:
    document_count = await repository.count_documents()
    chunk_count = await repository.count_chunks()
    return {
        "status": "ok",
        "document_count": document_count,
        "chunk_count": chunk_count,
    }


@app.get("/api/version")
async def version() -> dict[str, str]:
    try:
        return {"version": get_version("firstspirit-docs-rag-backend")}
    except PackageNotFoundError as exc:
        raise HTTPException(status_code=503, detail="Package metadata unavailable") from exc


# ---------------------------------------------------------------------------
# Frontend static assets / SPA catch-all
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def serve_root() -> FileResponse:
    index_path = Path(FRONTEND_DIST) / "index.html" if FRONTEND_DIST else Path("index.html")
    if not index_path.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                f"index.html not found. FRONTEND_DIST={FRONTEND_DIST!r}, cwd={os.getcwd()}. "
                "Set FRONTEND_DIST or run a frontend build."
            ),
        )
    return FileResponse(str(index_path))


@app.get("/{path:path}", include_in_schema=False)
async def serve_spa_or_static(path: str) -> FileResponse:
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404)

    if FRONTEND_DIST:
        try:
            dist_dir = Path(FRONTEND_DIST).resolve()
            requested_path = (dist_dir / path).resolve()
            if not requested_path.is_relative_to(dist_dir):
                raise HTTPException(status_code=404)
            if requested_path.is_file():
                return FileResponse(str(requested_path))
        except OSError as exc:
            logger.error(
                "Static file error for path=%s frontend_dist=%s: %s", path, FRONTEND_DIST, exc
            )
            raise HTTPException(status_code=500, detail="Static file error") from exc

    index_path = Path(FRONTEND_DIST) / "index.html" if FRONTEND_DIST else Path("index.html")
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(index_path))
