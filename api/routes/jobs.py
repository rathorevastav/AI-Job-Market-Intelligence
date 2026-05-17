"""
api/routes/jobs.py

Handles all job-related HTTP endpoints.

DESIGN RULE: No raw SQLAlchemy queries in this file.
    Every database operation goes through crud.py functions.
    Routes handle HTTP concerns only: parameter parsing, status codes,
    response shaping, and error translation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

import database.crud as crud
from api.schemas import (
    JobResponse,
    JobSummary,
    PaginatedJobsResponse,
)
from database.connection import get_db
from database.models import ExperienceLevel, JobType

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"],
)


# ============================================================================
# GET /jobs
# ============================================================================

@router.get(
    "",
    response_model=PaginatedJobsResponse,
    summary="List jobs with filters and pagination",
    description=(
        "Returns a paginated list of job postings. All filter parameters are "
        "optional and combinable. Skills filter uses PostgreSQL array containment."
    ),
)
def list_jobs(
    # --- Filter query parameters ---
    city: Optional[str] = Query(
        default=None,
        description="Partial city name match (case-insensitive). Example: 'bangalore'",
        max_length=200,
    ),
    country: Optional[str] = Query(
        default=None,
        description="ISO 3166-1 alpha-2 country code. Example: 'IN', 'US'",
        max_length=2,
    ),
    company_name: Optional[str] = Query(
        default=None,
        description="Partial company name match (case-insensitive). Example: 'google'",
        max_length=200,
    ),
    skill: Optional[str] = Query(
        default=None,
        description="Single skill to filter on (exact match within skills array). Example: 'python'",
        max_length=100,
    ),
    experience_level: Optional[ExperienceLevel] = Query(
        default=None,
        description="Experience level. One of: internship, entry, mid, senior, lead, principal, executive",
    ),
    job_type: Optional[JobType] = Query(
        default=None,
        description="Job type. One of: full_time, part_time, contract, freelance, internship",
    ),
    is_remote: Optional[bool] = Query(
        default=None,
        description="Filter by remote eligibility. true=remote only, false=on-site only",
    ),
    source_platform: Optional[str] = Query(
        default=None,
        description="Scraper source platform. Example: 'remoteok', 'linkedin'",
        max_length=100,
    ),
    posted_after: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 datetime. Only jobs posted after this date. Example: '2026-01-01T00:00:00Z'",
    ),
    posted_before: Optional[datetime] = Query(
        default=None,
        description="ISO 8601 datetime. Only jobs posted before this date.",
    ),
    search_query: Optional[str] = Query(
        default=None,
        description="Full-text search across title and description (case-insensitive).",
        max_length=200,
    ),
    # --- Pagination ---
    page: int = Query(
        default=1,
        ge=1,
        description="Page number (1-based)",
    ),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Results per page (max 100)",
    ),
    # --- Sorting ---
    order_by: str = Query(
        default="posted_at",
        description="Sort column. One of: posted_at, created_at, salary_min, salary_max, company_name, title",
    ),
    descending: bool = Query(
        default=True,
        description="Sort direction. true=newest/highest first",
    ),
    # --- Injected dependencies ---
    db: Session = Depends(get_db),
) -> PaginatedJobsResponse:
    """
    DEPENDENCY INJECTION FLOW:
        FastAPI sees `db: Session = Depends(get_db)`.
        Before calling this function, FastAPI calls get_db() which:
            1. Opens a SQLAlchemy session from the connection pool
            2. Yields it as `db`
            3. After the function returns, commits and closes the session

        The route handler never opens or closes the session directly.
        This is the correct pattern for resource lifecycle management.

    CRUD CALL:
        crud.get_jobs() accepts keyword-only arguments (the `*` in its signature).
        We pass every filter parameter explicitly by name.
        If a parameter is None, crud.get_jobs() skips that filter — no "WHERE NULL"
        conditions are added to the query.

    RESPONSE SHAPING:
        crud.get_jobs() returns a PaginatedResult dict:
            {"items": [Job, Job, ...], "total": N, "page": N, ...}

        The `items` are SQLAlchemy Job ORM objects.
        Pydantic's JobSummary(from_attributes=True) reads their attributes directly.
        FastAPI serialises the validated Pydantic objects to JSON automatically.
    """
    logger.info(
        "GET /jobs | city=%s country=%s skill=%s level=%s page=%d",
        city, country, skill, experience_level, page,
    )

    # Validate sort column against the whitelist crud.py accepts
    valid_order_columns = {"posted_at", "created_at", "salary_min", "salary_max", "company_name", "title"}
    if order_by not in valid_order_columns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid order_by '{order_by}'. Must be one of: {sorted(valid_order_columns)}",
        )

    result = crud.get_jobs(
        db,
        city=city,
        country=country,
        company_name=company_name,
        skill=skill,
        experience_level=experience_level,
        job_type=job_type,
        is_remote=is_remote,
        source_platform=source_platform,
        posted_after=posted_after,
        posted_before=posted_before,
        search_query=search_query,
        page=page,
        page_size=page_size,
        order_by=order_by,
        descending=descending,
    )

    # Convert ORM objects to Pydantic models
    # JobSummary(from_attributes=True) reads attributes directly from ORM objects
    items = [JobSummary.model_validate(job) for job in result["items"]]

    return PaginatedJobsResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        pages=result["pages"],
    )


# ============================================================================
# GET /jobs/{job_id}
# ============================================================================

@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get a single job by ID",
    responses={
        404: {"description": "Job not found"},
    },
)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
) -> JobResponse:
    """
    Returns the full job record including description.

    PATH PARAMETER:
        FastAPI automatically extracts `job_id` from the URL path
        and validates that it is a valid integer. If a non-integer
        is provided (e.g. /jobs/abc), FastAPI returns a 422 before
        this function is ever called.

    404 HANDLING:
        crud.get_job_by_id() returns None when the record doesn't exist.
        We translate None → HTTP 404 here. The CRUD layer doesn't know
        about HTTP — that's correctly the route layer's responsibility.
    """
    logger.info("GET /jobs/%d", job_id)

    job = crud.get_job_by_id(db, job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with id={job_id} not found",
        )

    return JobResponse.model_validate(job)
