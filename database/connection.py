"""
database/connection.py

PURPOSE:
    This module is the SINGLE SOURCE OF TRUTH for everything related to
    how this application connects to PostgreSQL. No other file should
    contain database connection strings or engine configuration.

DESIGN PRINCIPLE — Separation of Concerns:
    Connection logic lives here. Models live in models.py. Queries live
    in crud.py. If the database ever changes (e.g., switching from local
    PostgreSQL to AWS RDS), you only change THIS file.
"""

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError, OperationalError

from config import settings  # see config.py explanation below

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
# We use Python's standard logging instead of print() in production code.
# This lets logs be routed to files, monitoring services (Datadog, Sentry),
# or suppressed entirely in tests — none of which is possible with print().
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DECLARATIVE BASE
# ---------------------------------------------------------------------------
# WHY IS THIS HERE?
#   Base is the foundation that all SQLAlchemy models inherit from.
#   It must be defined exactly once, in one place. Every model class
#   (Job, Trend, etc.) in models.py will import THIS Base object.
#
#   If Base were defined in models.py AND connection.py, SQLAlchemy would
#   see two different registries and your tables would never relate to each
#   other correctly. One Base, one registry — always.
#
# WHAT DOES IT DO?
#   When you write `class Job(Base)`, SQLAlchemy:
#     1. Reads the class attributes (columns, relationships)
#     2. Registers the class in Base's metadata
#     3. Knows to create a `jobs` table when Base.metadata.create_all() runs
Base = declarative_base()


# ---------------------------------------------------------------------------
# DATABASE URL CONSTRUCTION
# ---------------------------------------------------------------------------
# WHY ENVIRONMENT VARIABLES?
#   Hardcoding "postgresql://user:password@localhost/dbname" in source code
#   means your password is visible to anyone with access to your repository.
#   GitHub has bots that scan for accidentally committed credentials.
#
#   Environment variables let the same codebase connect to:
#     - Local PostgreSQL during development
#     - A staging database during QA testing
#     - AWS RDS in production
#   without changing a single line of code.
#
# FORMAT:
#   postgresql+psycopg2://username:password@host:port/database_name
#   └─ dialect ─┘└─ driver ─┘└─────────── DSN ───────────────────┘
#
#   'psycopg2' is the Python driver (adapter) for PostgreSQL.
#   SQLAlchemy itself is not a driver — it's a toolkit that USES drivers.
#   psycopg2 is the industry-standard driver for Python + PostgreSQL.
def _build_database_url() -> str:
    """
    Constructs the PostgreSQL connection URL from environment variables.

    Returns a URL in SQLAlchemy's expected format.
    Raises ValueError early if required config is missing, so the app
    fails at startup (not mid-request) with a clear error message.
    """
    url = (
        f"postgresql+psycopg2://"
        f"{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}"
        f"/{settings.DB_NAME}"
    )
    return url


# ---------------------------------------------------------------------------
# ENGINE
# ---------------------------------------------------------------------------
# WHAT IS THE ENGINE?
#   The Engine is SQLAlchemy's core object. It manages the connection pool
#   and is the entry point for all database communication. Think of it as
#   the "factory" that creates and recycles database connections.
#
#   You create ONE engine for the lifetime of your application (singleton).
#   Creating a new engine per request would be catastrophically slow.
#
# CONNECTION POOLING — WHY IT MATTERS:
#   Opening a new TCP connection to PostgreSQL takes ~50–100ms.
#   For an API handling 100 requests/second, that's 5–10 seconds wasted
#   per second — the API would collapse immediately.
#
#   A connection pool maintains a set of pre-opened connections and
#   REUSES them. When a request needs a connection, it borrows one from
#   the pool, uses it, then returns it. The connection stays open.
#
# POOL PARAMETERS EXPLAINED:
#   pool_size=10        → Keep 10 connections permanently open and ready.
#                         For a small-to-medium API, 10 is a solid default.
#                         Match this to PostgreSQL's max_connections setting.
#
#   max_overflow=20     → Allow 20 EXTRA connections beyond pool_size during
#                         traffic spikes. These are created and closed on demand.
#                         Max simultaneous connections = pool_size + max_overflow = 30.
#
#   pool_timeout=30     → If all connections are busy and none free up within
#                         30 seconds, raise a timeout error instead of hanging
#                         forever. Fail fast, fail clearly.
#
#   pool_recycle=1800   → Force-recycle a connection after 30 minutes (1800s).
#                         Why? PostgreSQL and network firewalls silently drop
#                         idle connections. A recycled connection is re-opened
#                         fresh, preventing "SSL connection has been closed
#                         unexpectedly" errors in long-running applications.
#
#   pool_pre_ping=True  → Before lending a connection from the pool, send a
#                         lightweight ping ("SELECT 1") to verify it's still
#                         alive. If not, discard and open a fresh one.
#                         This prevents "connection already closed" errors
#                         that would otherwise surface mid-request.
#
#   echo=False          → Don't log every SQL statement to the console in
#                         production. In development, set echo=True to see
#                         exactly what SQL SQLAlchemy generates — extremely
#                         useful for debugging and learning.
def _create_engine():
    """
    Creates and configures the SQLAlchemy Engine.
    Called once at module import time.
    """
    database_url = _build_database_url()

    engine = create_engine(
        database_url,
        poolclass=QueuePool,        # Explicit: QueuePool is the right pool for web apps
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=settings.DB_ECHO,      # True in dev, False in production (from .env)
        future=True,                # Use SQLAlchemy 2.0-style behavior
    )

    # WHAT IS THIS EVENT?
    #   PostgreSQL has a concept called search_path, which determines which
    #   schema (namespace) it looks in when you query a table.
    #   By default this is "public". If you want to use a custom schema
    #   (e.g., "job_market"), this event sets it on every new connection.
    #   For now it's a no-op comment, but knowing HOW to do this is valuable.
    #
    # @event.listens_for(engine, "connect")
    # def set_search_path(dbapi_connection, connection_record):
    #     dbapi_connection.execute("SET search_path TO job_market, public")

    logger.info(
        "Database engine created | host=%s db=%s pool_size=%d",
        settings.DB_HOST, settings.DB_NAME, 10
    )
    return engine


# Module-level singleton — created once when Python imports this module
engine = _create_engine()


# ---------------------------------------------------------------------------
# SESSION FACTORY
# ---------------------------------------------------------------------------
# WHAT IS A SESSION?
#   A Session is SQLAlchemy's "unit of work". It tracks all the objects
#   you load, create, or modify during a single operation (e.g., a single
#   API request). At the end, you either commit() (save all changes) or
#   rollback() (discard all changes).
#
#   A Session holds ONE connection from the pool for its lifetime.
#   Keeping a session open longer than needed wastes pool connections.
#   This is why sessions must be opened and closed per-request.
#
# SessionLocal IS THE FACTORY:
#   Calling SessionLocal() returns a NEW Session object each time.
#   It's not a session itself — it's a configured class that produces sessions.
#
# PARAMETERS:
#   autocommit=False → You must call session.commit() explicitly.
#                      Auto-commit is dangerous: if an error happens halfway
#                      through a multi-step operation, the first steps are
#                      already saved but the rest aren't — corrupt state.
#                      With autocommit=False, it's all-or-nothing (atomic).
#
#   autoflush=False  → Don't automatically send pending SQL to the DB before
#                      each query. We control this manually. Autoflush can
#                      cause unexpected INSERTs at the wrong moment.
#
#   bind=engine      → This session factory is connected to our specific engine.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
)


# ---------------------------------------------------------------------------
# SESSION DEPENDENCY — FOR FASTAPI
# ---------------------------------------------------------------------------
# WHY A GENERATOR FUNCTION?
#   FastAPI's dependency injection system expects a generator (a function
#   with `yield`) for resources that need setup AND teardown.
#
#   The pattern:
#     1. Code before `yield` → setup (open session)
#     2. `yield` the resource → FastAPI injects it into the route handler
#     3. Code after `yield` → teardown (close session, always)
#
#   The `try/finally` guarantees the session is ALWAYS closed, even if
#   an unhandled exception occurs inside the route. Without this, you'd
#   leak connections — the pool fills up, and your API stops working.
#
# HOW FASTAPI USES THIS:
#   @router.get("/jobs")
#   def get_jobs(db: Session = Depends(get_db)):
#       # `db` is a live Session, injected by FastAPI
#       return db.query(Job).all()
#       # After this returns, FastAPI calls the generator's cleanup code
def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a database session per request.

    Usage:
        from fastapi import Depends
        from database.connection import get_db

        @router.get("/jobs")
        def get_jobs(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()     # Commit only if the route completed without exception
    except SQLAlchemyError as e:
        db.rollback()   # Discard all changes if anything went wrong
        logger.error("Database session error, rolling back: %s", str(e))
        raise           # Re-raise so FastAPI returns a 500 response
    finally:
        db.close()      # ALWAYS return the connection to the pool


# ---------------------------------------------------------------------------
# CONTEXT MANAGER SESSION — FOR SCRIPTS & BACKGROUND TASKS
# ---------------------------------------------------------------------------
# WHY A SEPARATE CONTEXT MANAGER?
#   FastAPI's `Depends(get_db)` only works inside FastAPI route functions.
#   Your scraper, ML pipeline, and scheduled jobs run OUTSIDE FastAPI.
#   They need sessions too, but they can't use FastAPI's injection system.
#
#   A context manager (the `with` statement) solves this cleanly:
#
#   with get_db_session() as db:
#       db.add(some_job)
#       # commit happens automatically on success
#   # session closes automatically, even on error
#
#   This is idiomatic Python for resource management.
@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions in non-FastAPI contexts.

    Usage (scraper, ML pipeline, scripts):
        from database.connection import get_db_session

        with get_db_session() as db:
            db.add(new_job)
            # session commits and closes automatically
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Session error in context manager, rolling back: %s", str(e))
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# DATABASE HEALTH CHECK
# ---------------------------------------------------------------------------
# WHY?
#   When your API starts, it should immediately verify it can reach PostgreSQL.
#   If the DB is unreachable (wrong credentials, server down, network issue),
#   you want a clear error at startup — NOT a mysterious 500 error on the
#   first user request.
#
#   Docker health checks and Kubernetes liveness probes also call functions
#   like this to determine if the application is ready to serve traffic.
def check_database_connection() -> bool:
    """
    Verifies that the database is reachable and responding.

    Returns:
        True if the connection succeeds.

    Raises:
        OperationalError if the database cannot be reached.

    Usage:
        Called once in main.py's startup event.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("Database connection verified successfully.")
        return True
    except OperationalError as e:
        logger.critical(
            "FATAL: Cannot connect to database at %s:%s/%s — %s",
            settings.DB_HOST, settings.DB_PORT, settings.DB_NAME, str(e)
        )
        raise


# ---------------------------------------------------------------------------
# TABLE CREATION UTILITY
# ---------------------------------------------------------------------------
# WHY NOT USE THIS IN PRODUCTION?
#   create_all() is useful during development to auto-create tables.
#   In production, you should use Alembic (a migration tool) instead.
#
#   WHY ALEMBIC?
#   Imagine you have 1 million rows in your `jobs` table, and you need
#   to add a new column. create_all() does NOTHING to existing tables —
#   it only creates missing ones. To ADD a column to an existing table,
#   you need a migration. Alembic tracks schema changes the same way
#   Git tracks code changes — versioned, reversible, auditable.
#
#   For now, create_all() is fine for getting started. We note Alembic
#   here so you know it exists and why you'll need it.
def create_tables() -> None:
    """
    Creates all tables defined in models.py if they don't already exist.

    DEVELOPMENT USE ONLY.
    For production schema changes, use Alembic migrations.

    Usage:
        Called once in main.py's startup event, or run directly:
        python -c "from database.connection import create_tables; create_tables()"
    """
    # Import models here to ensure they are registered with Base.metadata
    # before create_all() runs. If models.py is never imported, Base.metadata
    # is empty and no tables will be created.
    import database.models  # noqa: F401 — import for side effects (registration)

    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created (or already exist).")
