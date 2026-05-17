"""
api/main.py

FastAPI application entry point.

This file has one job: assemble the application.
    - Create the FastAPI instance
    - Configure startup/shutdown lifecycle
    - Register all routers
    - Add middleware

It contains NO business logic, NO database queries, NO route handlers.
All of those live in their respective modules.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import jobs, stats
from config import settings
from database.connection import check_database_connection, create_tables

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# Configure once here, at the application entry point.
# All other modules use `logging.getLogger(__name__)` and inherit this config.
# In production, replace StreamHandler with a structured JSON handler
# (e.g. python-json-logger) that feeds into your log aggregator.

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ============================================================================
# LIFESPAN — startup and shutdown logic
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager (replaces deprecated @app.on_event).

    Code before `yield` runs at STARTUP.
    Code after `yield` runs at SHUTDOWN.

    WHY LIFESPAN INSTEAD OF @app.on_event("startup")?
        @app.on_event is deprecated in FastAPI 0.93+. The lifespan pattern
        is the correct modern approach. It also works better with pytest's
        async test client.

    STARTUP:
        1. Log the active configuration (confirms correct .env was loaded)
        2. Verify database connectivity — fail fast if the DB is unreachable
           rather than discovering it on the first user request
        3. Create tables if they don't exist (development convenience)
           In production, Alembic migrations replace this call.
    """
    # ── Startup ──────────────────────────────────────────────────────────
    logger.info("API starting up...")
    settings.log_startup_config()

    try:
        check_database_connection()
    except Exception as exc:
        logger.critical("Database unreachable at startup: %s", exc)
        # Re-raise to prevent the server from starting with a broken DB connection.
        # Uvicorn will exit with a non-zero code, which Docker/Kubernetes will detect
        # and restart the container (or alert the operator).
        raise

    create_tables()
    logger.info("API startup complete. Ready to serve requests.")

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info("API shutting down.")


# ============================================================================
# APPLICATION INSTANCE
# ============================================================================

app = FastAPI(
    title="AI Job Market Intelligence Platform",
    description=(
        "REST API for querying job market data collected from RemoteOK and other platforms. "
        "Provides filtered job listings, skill demand analytics, and scraper health metrics."
    ),
    version="0.1.0",
    lifespan=lifespan,

    # Swagger UI is served at /docs
    # ReDoc is served at /redoc
    # Both are auto-generated from the OpenAPI schema FastAPI builds from
    # your route decorators and Pydantic schemas — zero extra configuration needed.
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ============================================================================
# MIDDLEWARE
# ============================================================================

# CORS — Cross-Origin Resource Sharing
# Required so the Streamlit dashboard (running on localhost:8501) can make
# HTTP requests to this API (running on localhost:8000).
# Without CORS, the browser blocks cross-origin requests as a security measure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.API_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],          # This is a read-only API — no POST/PUT/DELETE needed
    allow_headers=["*"],
)


# ============================================================================
# GLOBAL EXCEPTION HANDLERS
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catches any unhandled exception and returns a consistent 500 response.

    Without this, unhandled exceptions return FastAPI's default error format
    which may expose internal stack traces in production.

    This handler:
        1. Logs the full traceback (visible in your log aggregator)
        2. Returns a safe, consistent JSON error to the client
    """
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "detail": "An unexpected error occurred. Check server logs.",
            "status_code": 500,
        },
    )


# ============================================================================
# ROUTER REGISTRATION
# ============================================================================
# WHY A PREFIX ON EACH ROUTER?
#   The prefix "/api/v1" is set in config.py (API_PREFIX).
#   Versioning the API from the start costs nothing and allows breaking
#   changes later without disrupting existing consumers:
#       /api/v1/jobs   — current consumers stay here
#       /api/v2/jobs   — new version with breaking changes, opt-in

app.include_router(jobs.router,  prefix=settings.API_PREFIX)
app.include_router(stats.router, prefix=settings.API_PREFIX)


# ============================================================================
# ROOT ENDPOINT
# ============================================================================

@app.get("/", include_in_schema=False)
async def root() -> dict:
    """
    Root endpoint. Not included in the OpenAPI schema (include_in_schema=False).
    Redirects browsers away from the bare URL with a useful message.
    """
    return {
        "message": "AI Job Market Intelligence Platform API",
        "version": "0.1.0",
        "docs":    "/docs",
        "health":  f"{settings.API_PREFIX}/stats/health",
    }
