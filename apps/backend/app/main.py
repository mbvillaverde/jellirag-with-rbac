"""JellieRAG FastAPI backend entrypoint.

Owns RAG orchestration, auth/RBAC, sync, session lifecycle. All AI operations
go through OpenAI-compatible HTTP clients; no external dependencies beyond
Jellyfin (reached over Tailscale).

A single shared `httpx.AsyncClient` (async I/O throughout) is created in the
lifespan and reused by the AI provider + Jellyfin clients.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config.settings import get_settings
from .services.jellyfin_client import JellyfinClient
from .services.ai_provider import LLMClient, EmbeddingsClient
from .services.db import Database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Shared async client for ALL outbound HTTP (async I/O throughout).
    transport = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    app.state.http = transport
    app.state.jellyfin = JellyfinClient(
        transport, settings.jellyfin_tailscale_url, settings.jellyfin_api_key
    )
    
    # AI provider clients
    app.state.llm = LLMClient(
        transport, settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
    )
    app.state.embed = EmbeddingsClient(
        transport, settings.embed_base_url, settings.embed_api_key, settings.embed_model, settings.sync_embed_concurrency
    )
    
    # Database with vector support
    app.state.db = Database(settings.sqlite_path, settings.embed_dim)
    await app.state.db.initialize()
    
    # Warmup LLM
    try:
        await app.state.llm.warmup()
    except Exception as e:
        import logging
        logging.warning("LLM warmup failed (non-blocking): %s", e)
    
    # Bootstrap admin user
    from .services.bootstrap import ensure_bootstrap_admin

    try:
        await ensure_bootstrap_admin(app.state.db, settings)
    except Exception:  # never block startup on bootstrap
        pass

    scheduler = None
    try:
        from .services.scheduler import start_scheduler

        scheduler = start_scheduler(app, settings)
    except Exception:  # never block startup on scheduler
        pass

    try:
        yield
    finally:
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
        await transport.aclose()
        await app.state.db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="JellieRAG backend", lifespan=lifespan)

    # Strict CORS: only the configured frontend origin(s) (edge-security spec).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or [],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Routers registered by their owning modules (see _register_routers).
    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    # Imported lazily so config errors surface only when the app builds, and to
    # avoid import cycles during module-level collection.
    from .routers.auth import router as auth_router
    from .routers.admin import router as admin_router
    from .routers.chat import router as chat_router
    from .routers.history import router as history_router
    from .routers.jellyfin import router as jellyfin_router
    from .routers.sync import router as sync_router

    app.include_router(auth_router, prefix="/api")
    app.include_router(admin_router, prefix="/api/admin")
    app.include_router(chat_router, prefix="/api")
    app.include_router(history_router, prefix="/api")
    app.include_router(jellyfin_router, prefix="/api")
    app.include_router(sync_router, prefix="/api")


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
