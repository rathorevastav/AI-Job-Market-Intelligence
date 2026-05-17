"""
api/routes/stats.py

Analytics and operational health endpoints.

These routes aggregate data for the Streamlit dashboard and for
operational monitoring (uptime bots, alerting systems, Docker health checks).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

import database.crud as crud
from api.schemas import (
    HealthResponse,
    ScrapeRunResponse,
    ScrapeRunsResponse,
    TopSkillsResponse,
    SkillCount,
)
from database.connection import get_db
from database.models import Job

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/stats",
    tags=["Stats"],
)


# ============================================================================
# GET /stats/top-skills
# ============================================================================

@router.get(
    "/top-skills",
    response_model=TopSkillsResponse,
    summary="Top skills by job count",
    description=(
        "Returns the most frequently appearing skills across all jobs. "
        "Optionally filtered by country and date range. "
        "Uses PostgreSQL unnest() aggregation — runs in the database, not Python."
    ),
)
def top_skills(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of skills to return",
    ),
    country: Optional[str] = Query(
        default=None,
        description="ISO 3166-1 alpha-2 country code to filter by. Example: 'US', 'IN'",
        max_length=2,
    ),
    posted_after: Optional[datetime] = Query(
        default=None,
        description="Only count jobs posted after this datetime. Example: '2026-01-01T00:00:00Z'",
    ),
    db: Session = Depends(get_db),
) -> TopSkillsResponse:
    """
    IMPORTANT: crud.get_top_skills() only counts jobs where is_skills_extracted=True.
    Immediately after scraping, jobs have is_skills_extracted=False (skills were set
    from the API tags, but the ML pipeline hasn't run yet). The count may be 0 until
    the ML pipeline marks jobs as processed.

    For now, if the count is 0, the response returns an empty skills list.
    The ML pipeline phase will populate is_skills_extracted=True.
    """
    logger.info("GET /stats/top-skills | limit=%d country=%s", limit, country)

    raw_skills = crud.get_top_skills(
        db,
        limit=limit,
        country=country,
        posted_after=posted_after,
    )

    skills = [SkillCount(skill=row["skill"], job_count=row["job_count"]) for row in raw_skills]

    filters_applied = {}
    if country:
        filters_applied["country"] = country
    if posted_after:
        filters_applied["posted_after"] = posted_after.isoformat()

    return TopSkillsResponse(
        skills=skills,
        total_skills=len(skills),
        filters_applied=filters_applied,
    )


# ============================================================================
# GET /stats/scrape-runs
# ============================================================================

@router.get(
    "/scrape-runs",
    response_model=ScrapeRunsResponse,
    summary="Recent scraper execution logs",
    description=(
        "Returns recent ScrapeRun audit records. Each record captures "
        "how many jobs were found, inserted, and whether the run succeeded or failed."
    ),
)
def scrape_runs(
    platform: Optional[str] = Query(
        default=None,
        description="Filter by platform. Example: 'remoteok', 'linkedin'",
        max_length=100,
    ),
    status: Optional[str] = Query(
        default=None,
        description="Filter by run status. One of: completed, failed, running",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of runs to return",
    ),
    db: Session = Depends(get_db),
) -> ScrapeRunsResponse:
    """
    Useful for:
        - Dashboard operational health panel ("last scrape ran 2 hours ago")
        - Monitoring alerts ("no successful run in 24 hours — trigger alert")
        - Debugging ("why did the last run insert 0 jobs?")
    """
    logger.info("GET /stats/scrape-runs | platform=%s status=%s limit=%d", platform, status, limit)

    runs = crud.get_recent_scrape_runs(
        db,
        platform=platform,
        limit=limit,
        status=status,
    )

    run_schemas = [ScrapeRunResponse.model_validate(run) for run in runs]

    return ScrapeRunsResponse(
        runs=run_schemas,
        total=len(run_schemas),
    )


# ============================================================================
# GET /stats/health
# ============================================================================

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="API and database health check",
    description=(
        "Returns API status, database connectivity, total job count, "
        "and the most recent scrape run. Used by monitoring systems."
    ),
)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    """
    DESIGN PRINCIPLES for health endpoints:
        1. Must respond even if parts of the system are degraded
           — catch exceptions and report them instead of crashing
        2. Must be fast — no complex queries, just lightweight checks
        3. total_jobs uses COUNT(Job.id) — the single fastest aggregate
           PostgreSQL can answer (uses the primary key index)

    STATUS CODES:
        200 even when database_connected=False — the API itself is up.
        Monitoring systems check the response body, not just the status code.
        503 would prevent the response body from being read by some monitors.
    """
    logger.info("GET /stats/health")

    database_connected = False
    total_jobs = 0
    latest_run = None

    try:
        # Lightweight COUNT — PostgreSQL answers this from the PK index
        total_jobs = db.query(func.count(Job.id)).scalar() or 0
        database_connected = True
    except Exception as exc:
        logger.error("Health check: database query failed: %s", exc)

    try:
        recent_runs = crud.get_recent_scrape_runs(db, limit=1)
        if recent_runs:
            latest_run = ScrapeRunResponse.model_validate(recent_runs[0])
    except Exception as exc:
        logger.error("Health check: scrape run query failed: %s", exc)

    return HealthResponse(
        api_status="ok",
        database_connected=database_connected,
        total_jobs=total_jobs,
        latest_scrape_run=latest_run,
        timestamp=datetime.now(timezone.utc),
    )
