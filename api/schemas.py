"""
api/schemas.py

PURPOSE:
    Pydantic models defining the exact shape of every API response.
    These are completely separate from SQLAlchemy ORM models in database/models.py.

WHY TWO SEPARATE MODEL LAYERS?
    SQLAlchemy models (database/models.py):
        - Represent database tables with ORM internals, lazy-loading state,
          and PostgreSQL-specific types that JSON cannot serialize directly.

    Pydantic schemas (this file):
        - Define the data CONTRACT exposed to API consumers.
        - Validate and serialize ORM objects into clean JSON.
        - Control exactly which fields are public vs internal.

    The route handler is the bridge:
        crud result (ORM objects) → Pydantic schema → JSON response

    This means you can refactor the database schema without breaking the
    API contract, and add API fields without touching the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from database.models import ExperienceLevel, JobType


# ============================================================================
# BASE CONFIGURATION
# ============================================================================

class _Base(BaseModel):
    """
    Shared config inherited by all ORM-backed response schemas.

    from_attributes=True (Pydantic v2):
        Lets Pydantic read values from SQLAlchemy ORM object attributes
        (obj.title) instead of requiring a plain dict.
        This is required for all schemas that receive ORM objects directly.
        Equivalent to Pydantic v1's `class Config: orm_mode = True`.
    """
    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# JOB SCHEMAS
# ============================================================================

class JobSummary(_Base):
    """
    Compact job shape for paginated list responses.

    Deliberately excludes `description` (can be thousands of characters)
    to keep list responses fast and small. The full description is available
    via GET /jobs/{id} which returns JobResponse.
    """
    id:               int
    title:            str
    company_name:     Optional[str]            = None
    city:             Optional[str]            = None
    country:          Optional[str]            = None
    is_remote:        Optional[bool]           = None
    experience_level: Optional[ExperienceLevel] = None
    job_type:         Optional[JobType]         = None
    skills:           Optional[list[str]]       = None
    salary_min:       Optional[int]             = None
    salary_max:       Optional[int]             = None
    salary_currency:  Optional[str]             = None
    source_platform:  str
    source_url:       str
    posted_at:        Optional[datetime]        = None


class JobResponse(_Base):
    """
    Full job record returned by GET /jobs/{job_id}.

    Includes all user-facing fields. Deliberately excludes:
        - raw_metadata    (internal JSONB scraper blob)
        - is_skills_extracted, is_salary_normalized, is_geocoded
          (internal ML pipeline processing flags)
        - quality_score   (internal data quality metric)
    """
    id:               int
    source_url:       str
    source_platform:  str
    external_id:      Optional[str]             = None
    title:            str
    company_name:     Optional[str]             = None
    description:      Optional[str]             = None
    location_raw:     Optional[str]             = None
    city:             Optional[str]             = None
    country:          Optional[str]             = None
    is_remote:        Optional[bool]            = None
    is_hybrid:        Optional[bool]            = None
    experience_level: Optional[ExperienceLevel] = None
    job_type:         Optional[JobType]         = None
    skills:           Optional[list[str]]       = None
    salary_min:       Optional[int]             = None
    salary_max:       Optional[int]             = None
    salary_currency:  Optional[str]             = None
    salary_period:    Optional[str]             = None
    company_size:     Optional[str]             = None
    company_industry: Optional[str]             = None
    posted_at:        Optional[datetime]        = None
    scraped_at:       Optional[datetime]        = None
    created_at:       Optional[datetime]        = None


class PaginatedJobsResponse(BaseModel):
    """
    Paginated job list with metadata.

    Does NOT inherit _Base — it is assembled from a crud dict, not an ORM object.
    The `items` field contains JobSummary objects validated from ORM rows.
    """
    items:     list[JobSummary]
    total:     int = Field(description="Total matching records across all pages")
    page:      int = Field(description="Current 1-based page number")
    page_size: int = Field(description="Records per page (max 100)")
    pages:     int = Field(description="Total number of pages")


# ============================================================================
# STATS SCHEMAS
# ============================================================================

class SkillCount(BaseModel):
    """One row from get_top_skills() — a skill and its job count."""
    skill:     str
    job_count: int


class TopSkillsResponse(BaseModel):
    """Response for GET /stats/top-skills."""
    skills:          list[SkillCount]
    total_skills:    int = Field(description="Number of distinct skills returned")
    filters_applied: dict[str, Any] = Field(
        default_factory=dict,
        description="Echo of the query filters used, for display in the dashboard",
    )


class ScrapeRunResponse(_Base):
    """One scrape run audit record."""
    id:                     int
    platform:               str
    status:                 str
    started_at:             Optional[datetime] = None
    completed_at:           Optional[datetime] = None
    pages_scraped:          Optional[int]      = None
    jobs_found:             Optional[int]      = None
    jobs_inserted:          Optional[int]      = None
    jobs_skipped_duplicate: Optional[int]      = None
    jobs_failed_parsing:    Optional[int]      = None
    error_message:          Optional[str]      = None
    config_snapshot:        Optional[dict]     = None


class ScrapeRunsResponse(BaseModel):
    """Response for GET /stats/scrape-runs."""
    runs:  list[ScrapeRunResponse]
    total: int


class HealthResponse(BaseModel):
    """
    Response for GET /stats/health.

    Consumed by monitoring uptime bots, Docker HEALTHCHECK,
    and load-balancer probes. Must always respond quickly.
    """
    api_status:         str     = Field(description="'ok' if the API is running")
    database_connected: bool
    total_jobs:         int
    latest_scrape_run:  Optional[ScrapeRunResponse] = None
    timestamp:          datetime


# ============================================================================
# ERROR SCHEMA
# ============================================================================

class ErrorResponse(BaseModel):
    """
    Uniform error payload for all 4xx / 5xx responses.

    Using one consistent error shape means API consumers
    never need to handle multiple error formats.
    """
    error:       str
    detail:      Optional[str] = None
    status_code: int
