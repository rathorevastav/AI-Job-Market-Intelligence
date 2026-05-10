"""
config.py  — Project Root Level

PURPOSE:
    Single source of truth for ALL application configuration.
    Every module that needs a setting imports from here.
    No module should ever call os.environ directly.

WHY THIS MATTERS:
    Scattered os.environ.get() calls across files means:
      - No central validation (missing vars crash at runtime, deep in code)
      - No type conversion (everything is a string from env)
      - No defaults in one place
      - No documentation of what the app needs to run

    This file solves all of that.

INSTALL DEPENDENCIES:
    pip install pydantic-settings python-dotenv

USAGE:
    from config import settings

    print(settings.DATABASE_URL)
    print(settings.DB_HOST)
"""

import logging
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ENVIRONMENT TYPE
# ---------------------------------------------------------------------------
# Literal["a", "b"] restricts APP_ENV to exactly these three strings.
# A typo like "prod" or "dev" raises a clear ValidationError at startup,
# not a subtle bug discovered later at runtime.
EnvironmentType = Literal["development", "staging", "production"]


# ---------------------------------------------------------------------------
# SETTINGS CLASS
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """
    Application-wide configuration loaded from environment variables and .env file.

    HOW PYDANTIC-SETTINGS WORKS:
        1. Reads the .env file(s) specified in model_config
        2. Also reads actual OS environment variables
        3. OS variables OVERRIDE .env values (correct production priority)
        4. Each field is type-validated and coerced automatically
        5. Missing required fields raise a clear ValidationError at startup
    """

    # -----------------------------------------------------------------------
    # PYDANTIC-SETTINGS CONFIGURATION
    # -----------------------------------------------------------------------
    model_config = SettingsConfigDict(
        # Read from .env first, fall back to .env.example if .env doesn't exist.
        # This prevents crashes on fresh clones before the developer runs setup.
        env_file=(".env", ".env.example"),
        env_file_encoding="utf-8",

        # True = DB_PASSWORD and db_password are different keys.
        # Enforces Unix convention: env vars are uppercase.
        case_sensitive=True,

        # If .env contains extra keys this class doesn't define,
        # ignore them silently instead of raising a validation error.
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # APPLICATION
    # -----------------------------------------------------------------------

    APP_NAME: str = Field(
        default="AI Job Market Intelligence Platform",
        description="Human-readable name used in API docs and log output",
    )
    APP_VERSION: str = Field(
        default="0.1.0",
        description="Semantic version string displayed in API metadata",
    )
    APP_ENV: EnvironmentType = Field(
        default="development",
        description="Runtime environment. Controls logging verbosity and feature flags.",
    )
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description=(
            "Python logging level. DEBUG locally to see all SQL. "
            "INFO or WARNING in production to reduce log volume."
        ),
    )
    DEBUG: bool = Field(
        default=False,
        description=(
            "Enables FastAPI debug mode (detailed error pages). "
            "MUST be False in production — debug mode leaks internal stack traces."
        ),
    )

    # -----------------------------------------------------------------------
    # DATABASE — INDIVIDUAL COMPONENTS
    # -----------------------------------------------------------------------
    # WHY INDIVIDUAL COMPONENTS AND A FULL URL?
    #   Some code needs the full URL (SQLAlchemy engine).
    #   Some code needs individual parts (logging without exposing the password,
    #   health-check scripts that only need the host, etc.)
    #
    #   We store the components and COMPUTE the full DATABASE_URL in a
    #   model_validator below. Both are available after initialization.
    #
    # WHY NO DEFAULT FOR DB_PASSWORD?
    #   If DB_PASSWORD had a default (even ""), the app would start
    #   "successfully" with wrong credentials and fail mysteriously on the
    #   first DB call. With no default, failure is immediate and clear:
    #   "ValidationError: DB_PASSWORD field required"

    DB_USER: str = Field(default="postgres")
    DB_PASSWORD: str = Field(description="PostgreSQL password. Required. No default.")
    DB_HOST: str = Field(
        default="localhost",
        description=(
            "'localhost' for local dev. "
            "'db' inside Docker Compose (the service name). "
            "RDS endpoint string in production."
        ),
    )
    DB_PORT: int = Field(default=5432, ge=1, le=65535)
    DB_NAME: str = Field(default="job_market_db")
    DB_ECHO: bool = Field(
        default=False,
        description=(
            "SQLAlchemy logs every SQL statement when True. "
            "Use in development only. Always False in production."
        ),
    )

    # -----------------------------------------------------------------------
    # DATABASE — FULL URL (computed or overridden)
    # -----------------------------------------------------------------------
    # Optional[str] with default=None means "not provided yet; will be built."
    #
    # WHY SUPPORT A DIRECT DATABASE_URL OVERRIDE?
    #   Heroku, Railway, Render, and most PaaS providers inject a single
    #   DATABASE_URL environment variable. Supporting it directly means
    #   zero config changes when deploying to those platforms.
    #
    # Priority:
    #   1. DATABASE_URL from OS environment (PaaS / Docker secret)
    #   2. Constructed from DB_USER + DB_PASSWORD + DB_HOST + DB_PORT + DB_NAME

    DATABASE_URL: Optional[str] = Field(
        default=None,
        description=(
            "Full PostgreSQL DSN. If set in .env, used directly. "
            "If absent, auto-constructed from DB_* fields."
        ),
    )

    # -----------------------------------------------------------------------
    # CONNECTION POOL
    # -----------------------------------------------------------------------
    # Exposed as config so they can be tuned per environment without code changes.

    DB_POOL_SIZE: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Permanent connections held in the SQLAlchemy pool",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Extra connections allowed beyond pool_size during traffic spikes",
    )
    DB_POOL_TIMEOUT: int = Field(
        default=30,
        ge=5,
        description="Seconds to wait for a free connection before raising TimeoutError",
    )
    DB_POOL_RECYCLE: int = Field(
        default=1800,
        description=(
            "Seconds before a connection is force-recycled. "
            "Prevents stale-connection errors from firewall idle timeouts. "
            "1800 = 30 minutes."
        ),
    )

    # -----------------------------------------------------------------------
    # SCRAPER
    # -----------------------------------------------------------------------

    SCRAPER_DELAY_MIN: float = Field(
        default=1.0,
        description="Minimum seconds to wait between page requests",
    )
    SCRAPER_DELAY_MAX: float = Field(
        default=4.0,
        description="Maximum seconds to wait between page requests",
    )
    SCRAPER_MAX_PAGES: int = Field(
        default=50,
        description="Maximum pages to scrape per platform per run",
    )
    SCRAPER_HEADLESS: bool = Field(
        default=True,
        description=(
            "Run Playwright in headless mode (no visible browser window). "
            "Set False locally when debugging scraper behaviour."
        ),
    )

    # -----------------------------------------------------------------------
    # ML PIPELINE
    # -----------------------------------------------------------------------

    ML_MODEL_DIR: str = Field(
        default="ml/models",
        description="Directory where trained .joblib model files are stored",
    )
    ML_TREND_LOOKBACK_DAYS: int = Field(
        default=90,
        description="Days of job history used to train the trend model",
    )
    ML_MIN_JOB_COUNT_FOR_TREND: int = Field(
        default=10,
        description=(
            "Minimum job count before a skill gets a trend computed. "
            "Prevents noisy trends from rarely-seen skills."
        ),
    )

    # -----------------------------------------------------------------------
    # API
    # -----------------------------------------------------------------------

    API_HOST: str = Field(default="0.0.0.0")
    API_PORT: int = Field(default=8000)
    API_WORKERS: int = Field(
        default=1,
        description=(
            "Uvicorn worker processes. "
            "1 in development. (2 × CPU cores) + 1 in production."
        ),
    )
    API_PREFIX: str = Field(
        default="/api/v1",
        description="URL prefix for all routes, e.g. /api/v1/jobs",
    )
    API_CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:8501"],
        description=(
            "Origins allowed by CORS. "
            "localhost:8501 = Streamlit default. "
            "Add your deployed dashboard URL in production."
        ),
    )

    # -----------------------------------------------------------------------
    # VALIDATORS
    # -----------------------------------------------------------------------

    @field_validator("DB_PASSWORD")
    @classmethod
    def password_must_not_be_empty(cls, v: str) -> str:
        """
        Rejects an empty or whitespace-only password.

        WHY:
            An .env file can have DB_PASSWORD= (key present, value empty).
            Pydantic accepts this as a valid string "". This validator catches
            that silent misconfiguration and raises a clear error immediately.
        """
        if not v or not v.strip():
            raise ValueError(
                "DB_PASSWORD cannot be empty. "
                "Set it in your .env file: DB_PASSWORD=your_secure_password"
            )
        return v

    @field_validator("APP_ENV")
    @classmethod
    def warn_if_production(cls, v: str) -> str:
        """Logs a confirmation message when APP_ENV=production is detected."""
        if v == "production":
            logger.warning(
                "Running in PRODUCTION environment. "
                "Verify: DB_ECHO=false, DEBUG=false, all secrets set."
            )
        return v

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        """
        Constructs DATABASE_URL from individual DB_* fields if not already set.

        WHY model_validator AND NOT field_validator?
            field_validator works on ONE field at a time.
            We need to READ five fields (user, password, host, port, name)
            and WRITE one field (DATABASE_URL).
            model_validator runs after ALL fields are validated, giving us
            access to the complete, coerced model instance via `self`.

        WHY mode="after"?
            Ensures all DB_* fields are already validated and coerced before
            this runs. Safe to access self.DB_HOST, self.DB_PORT (as int), etc.
        """
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+psycopg2://"
                f"{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}"
                f"/{self.DB_NAME}"
            )

        # Normalize legacy "postgres://" URLs (Heroku, some older providers).
        # SQLAlchemy 1.4+ dropped support for the "postgres://" prefix.
        # Replace it transparently so deployment doesn't require manual URL edits.
        if self.DATABASE_URL.startswith("postgres://"):
            self.DATABASE_URL = self.DATABASE_URL.replace(
                "postgres://",
                "postgresql+psycopg2://",
                1,
            )

        return self

    @model_validator(mode="after")
    def validate_pool_ceiling(self) -> "Settings":
        """
        Warns if total max connections might exceed PostgreSQL's limit.

        WHY:
            PostgreSQL's default max_connections=100.
            If pool_size + max_overflow > 90, other services sharing the DB
            (pgAdmin, other app instances) may be starved.
            We warn rather than error so the app still starts — the operator
            can decide whether their PostgreSQL is configured for higher limits.
        """
        ceiling = self.DB_POOL_SIZE + self.DB_MAX_OVERFLOW
        if ceiling > 90:
            logger.warning(
                "Connection pool ceiling=%d (pool_size=%d + max_overflow=%d). "
                "PostgreSQL default max_connections=100. "
                "Verify pg config can support this.",
                ceiling, self.DB_POOL_SIZE, self.DB_MAX_OVERFLOW,
            )
        return self

    # -----------------------------------------------------------------------
    # COMPUTED PROPERTIES
    # -----------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """True when running with APP_ENV=production."""
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        """True when running with APP_ENV=development."""
        return self.APP_ENV == "development"

    @property
    def safe_database_url(self) -> str:
        """
        DATABASE_URL with the password masked as ***.

        ALWAYS use this for logging. Never log the real DATABASE_URL.

        Example:
            postgresql+psycopg2://postgres:***@localhost:5432/job_market_db
        """
        if self.DATABASE_URL and self.DB_PASSWORD:
            return self.DATABASE_URL.replace(self.DB_PASSWORD, "***")
        return "not configured"

    def log_startup_config(self) -> None:
        """
        Logs a readable summary of all active settings at application startup.

        Call this once from main.py's lifespan startup handler.
        It confirms the right configuration loaded — critical for debugging
        environment issues in staging and production.
        """
        logger.info("=" * 60)
        logger.info("  %s  v%s", self.APP_NAME, self.APP_VERSION)
        logger.info("  Environment  : %s", self.APP_ENV)
        logger.info("  Log Level    : %s", self.LOG_LEVEL)
        logger.info("  Debug Mode   : %s", self.DEBUG)
        logger.info("  Database     : %s", self.safe_database_url)
        logger.info(
            "  Pool         : size=%d overflow=%d timeout=%ds recycle=%ds",
            self.DB_POOL_SIZE, self.DB_MAX_OVERFLOW,
            self.DB_POOL_TIMEOUT, self.DB_POOL_RECYCLE,
        )
        logger.info(
            "  API          : http://%s:%d%s",
            self.API_HOST, self.API_PORT, self.API_PREFIX,
        )
        logger.info("=" * 60)


# ---------------------------------------------------------------------------
# SINGLETON
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the cached Settings singleton.

    @lru_cache(maxsize=1) ensures Settings() is instantiated EXACTLY ONCE
    for the lifetime of the process. Configuration is read at startup and
    never re-read — consistent, predictable, and fast.

    TWO WAYS TO USE THIS:

    1. Direct import (recommended for most modules):
           from config import settings
           print(settings.DATABASE_URL)

    2. FastAPI Dependency Injection (for routes that need config):
           from fastapi import Depends
           from config import get_settings, Settings

           @router.get("/info")
           def info(cfg: Settings = Depends(get_settings)):
               return {"version": cfg.APP_VERSION, "env": cfg.APP_ENV}

       FastAPI calls get_settings() on every request, but @lru_cache
       returns the same instance each time — zero overhead.
    """
    return Settings()


# Module-level singleton — imported directly by most non-FastAPI modules.
# connection.py, crud.py, scraper, ML pipeline all use: from config import settings
settings: Settings = get_settings()
