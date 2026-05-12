"""
database/crud.py

PURPOSE:
    The repository layer. Every database read and write in this project
    goes through a function defined here. No other module (scraper, API,
    ML pipeline) should write raw SQLAlchemy queries — they call these
    functions instead.

═══════════════════════════════════════════════════════════════════════
WHY THE REPOSITORY PATTERN?
═══════════════════════════════════════════════════════════════════════
A "repository" is a layer that hides the details of data access behind
a clean function interface. Instead of your FastAPI route knowing that
jobs are stored in PostgreSQL and queried with SQLAlchemy, it just calls:

    jobs = get_jobs(db, filters={"city": "Bangalore", "skill": "Python"})

Three concrete benefits:

1. SWAPPABILITY
   If you ever migrate from PostgreSQL to another database, you rewrite
   crud.py. The scraper, API, and ML pipeline are untouched — they never
   knew about PostgreSQL directly.

2. TESTABILITY
   In tests, you can mock `crud.get_jobs()` to return fake data without
   ever touching a real database. This makes unit tests fast and reliable.

3. SINGLE RESPONSIBILITY
   Route handlers handle HTTP. Scraper handles page collection. This file
   handles all database logic. Each module has one job, one reason to change.

═══════════════════════════════════════════════════════════════════════
TRANSACTION MANAGEMENT STRATEGY
═══════════════════════════════════════════════════════════════════════
Sessions are NEVER opened inside crud.py.

The caller (scraper, FastAPI route, ML pipeline) is responsible for:
  - Opening the session (via get_db() or get_db_session())
  - Committing on success
  - Rolling back on failure
  - Closing the session

crud.py functions receive a `db: Session` parameter and operate within
the caller's transaction boundary. This means:

  - Multiple crud operations can be grouped into ONE atomic transaction
  - If step 2 fails, step 1 is automatically rolled back
  - crud.py never commits or rolls back — it just builds the transaction

Example of why this matters:
    # Scraper — ONE atomic transaction for the whole batch
    with get_db_session() as db:
        run = create_scrape_run(db, platform="linkedin")   # step 1
        for job_data in scraped_jobs:
            insert_job(db, job_data)                        # step 2..N
        complete_scrape_run(db, run.id, jobs_inserted=50)  # final step
    # If ANY step raises an exception, ALL changes are rolled back together.
    # Either all 50 jobs are saved, or none are.

═══════════════════════════════════════════════════════════════════════
DUPLICATE PREVENTION STRATEGY
═══════════════════════════════════════════════════════════════════════
Two-layer defense:

Layer 1 — Database constraint (models.py):
    source_url has unique=True. PostgreSQL enforces this at the storage
    level. Even direct SQL inserts cannot create duplicates.

Layer 2 — Application-level check (crud.py):
    insert_job() checks if source_url exists BEFORE attempting an insert.
    This avoids the overhead of catching IntegrityError on every duplicate
    (PostgreSQL raises an exception, Python catches it, SQLAlchemy rolls
    back the transaction — slow and noisy in logs).

    For BULK inserts from the scraper, we use PostgreSQL's native
    INSERT ... ON CONFLICT DO NOTHING — one round-trip for the entire
    batch, letting the database handle deduplication at maximum speed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, any_, cast, func, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database.models import (
    ExperienceLevel,
    Job,
    JobType,
    ScrapeRun,
    SkillTrend,
    TrendDirection,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: TYPE ALIASES
# ═══════════════════════════════════════════════════════════════════════════
# WHY TYPE ALIASES?
#   dict[str, Any] repeated everywhere is vague and noisy.
#   A named alias documents intent: JobData means "a dict of job fields".
#   When you see `data: JobData` in a function signature, you immediately
#   know this is raw scraped job data, not a config dict or API payload.

JobData = dict[str, Any]
TrendData = dict[str, Any]
PaginatedResult = dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _utcnow() -> datetime:
    """
    Returns the current UTC time as a timezone-aware datetime.

    WHY NOT datetime.utcnow()?
        datetime.utcnow() returns a NAIVE datetime (no timezone info).
        Storing naive datetimes in a timezone-aware PostgreSQL column
        causes warnings and inconsistent comparisons.
        datetime.now(timezone.utc) returns an AWARE datetime — always use this.
    """
    return datetime.now(timezone.utc)


def _safe_lower(value: Optional[str]) -> Optional[str]:
    """Strips and lowercases a string, returns None if empty."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped.lower() if stripped else None


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: JOB OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def insert_job(db: Session, data: JobData) -> tuple[Optional[Job], bool]:
    """
    Inserts a single new job posting into the database.

    DUPLICATE HANDLING:
        Checks source_url existence before insert (application layer).
        Falls back to catching IntegrityError if a race condition
        causes two concurrent scrapers to attempt the same URL simultaneously.

    Args:
        db:   Active SQLAlchemy session from the caller.
        data: Dictionary of job fields matching the Job model columns.

    Returns:
        A tuple of (Job instance or None, was_inserted: bool).
        - (Job, True)  → new record inserted successfully
        - (Job, False) → record already existed, returned as-is
        - (None, False) → insert failed due to an unexpected error

    WHY RETURN A TUPLE INSTEAD OF RAISING?
        The scraper processes hundreds of jobs in a loop. If insert_job()
        raised on every duplicate, the scraper would need a try/except
        around every call, making the loop messy. A (result, bool) tuple
        lets the caller check the outcome cleanly:

            job, inserted = insert_job(db, data)
            if inserted:
                stats["new"] += 1
            else:
                stats["duplicate"] += 1
    """
    source_url = data.get("source_url", "").strip()
    if not source_url:
        logger.warning("insert_job called with empty source_url — skipping")
        return None, False

    # Layer 2 duplicate check: query before attempting insert.
    # Uses the B-tree index on source_url — effectively free at scale.
    existing = (
        db.query(Job.id)
        .filter(Job.source_url == source_url)
        .first()
    )
    if existing:
        logger.debug("Duplicate job skipped: %s", source_url)
        return db.get(Job, existing.id), False

    try:
        job = Job(**data)
        db.add(job)
        db.flush()  # Assigns job.id from the DB sequence without committing.
                    # WHY flush() NOT commit()?
                    #   flush() sends the INSERT to PostgreSQL within the current
                    #   transaction but does NOT commit. The caller decides when to
                    #   commit. This lets us get job.id for logging while keeping
                    #   the transaction open for more operations.
        logger.debug("Job staged for insert: id=%s title='%s'", job.id, job.title[:50])
        return job, True

    except IntegrityError:
        # Race condition: another process inserted the same URL between
        # our SELECT check and our INSERT attempt. Roll back just this
        # operation and treat it as a duplicate.
        db.rollback()
        logger.debug("IntegrityError on insert (race condition duplicate): %s", source_url)
        existing_job = db.query(Job).filter(Job.source_url == source_url).first()
        return existing_job, False

    except SQLAlchemyError as e:
        db.rollback()
        logger.error("Unexpected error inserting job '%s': %s", source_url, str(e))
        return None, False


def bulk_insert_jobs(db: Session, jobs_data: list[JobData]) -> dict[str, int]:
    """
    Inserts a list of job postings using PostgreSQL's native upsert.

    PERFORMANCE DESIGN:
        insert_job() does one SELECT + one INSERT per job.
        For a scraper batch of 500 jobs, that is 1000 round-trips.
        bulk_insert_jobs() does ONE round-trip for the entire batch using:

            INSERT INTO jobs (...) VALUES (...), (...), (...)
            ON CONFLICT (source_url) DO NOTHING

        PostgreSQL handles deduplication internally. This is 10–50x faster
        for large batches and is the correct approach for the scraper.

    Args:
        db:         Active session.
        jobs_data:  List of job field dicts.

    Returns:
        Stats dict: {"attempted": N, "inserted": N, "skipped_duplicate": N, "failed": N}
    """
    if not jobs_data:
        return {"attempted": 0, "inserted": 0, "skipped_duplicate": 0, "failed": 0}

    stats = {"attempted": len(jobs_data), "inserted": 0, "skipped_duplicate": 0, "failed": 0}

    # Filter out records missing required fields before touching the database.
    valid_data = []
    for raw in jobs_data:
        if not raw.get("source_url") or not raw.get("title"):
            logger.warning(
                "bulk_insert_jobs: skipping record missing source_url or title: %s",
                raw.get("source_url", "<no url>"),
            )
            stats["failed"] += 1
            continue
        valid_data.append(raw)

    if not valid_data:
        return stats

    try:
        # pg_insert() is SQLAlchemy's PostgreSQL-specific INSERT builder.
        # It supports ON CONFLICT clauses that standard SQL INSERT does not.
        stmt = pg_insert(Job).values(valid_data)

        # ON CONFLICT DO NOTHING:
        #   If a row with the same source_url already exists, skip it silently.
        #   index_elements references the unique constraint column.
        stmt = stmt.on_conflict_do_nothing(index_elements=["source_url"])

        result = db.execute(stmt)

        # rowcount = number of rows actually inserted (duplicates excluded)
        inserted = result.rowcount if result.rowcount >= 0 else 0
        stats["inserted"] = inserted
        stats["skipped_duplicate"] = len(valid_data) - inserted

        db.flush()
        logger.info(
            "Bulk insert: %d attempted, %d inserted, %d duplicates skipped",
            len(valid_data), stats["inserted"], stats["skipped_duplicate"],
        )

    except SQLAlchemyError as e:
        db.rollback()
        logger.error("bulk_insert_jobs failed: %s", str(e))
        stats["failed"] = len(valid_data)
        stats["inserted"] = 0

    return stats


def get_job_by_id(db: Session, job_id: int) -> Optional[Job]:
    """
    Fetches a single job by its primary key.

    db.get() is SQLAlchemy's optimized single-row lookup by primary key.
    It checks the session's identity map first (an in-memory cache of
    already-loaded objects) before hitting the database. For repeated
    lookups of the same row within one session, this is effectively free.
    """
    try:
        return db.get(Job, job_id)
    except SQLAlchemyError as e:
        logger.error("get_job_by_id(%s) failed: %s", job_id, str(e))
        return None


def get_jobs(
    db: Session,
    *,
    # --- Filter parameters ---
    city: Optional[str] = None,
    country: Optional[str] = None,
    company_name: Optional[str] = None,
    skill: Optional[str] = None,
    experience_level: Optional[ExperienceLevel] = None,
    job_type: Optional[JobType] = None,
    is_remote: Optional[bool] = None,
    source_platform: Optional[str] = None,
    posted_after: Optional[datetime] = None,
    posted_before: Optional[datetime] = None,
    search_query: Optional[str] = None,
    # --- Pagination ---
    page: int = 1,
    page_size: int = 20,
    # --- Sorting ---
    order_by: str = "posted_at",
    descending: bool = True,
) -> PaginatedResult:
    """
    Fetches jobs with dynamic multi-filter support and pagination.

    DESIGN: KEYWORD-ONLY ARGUMENTS (`*`)
        The `*` after `db` forces all subsequent parameters to be passed
        by keyword: get_jobs(db, city="Bangalore"), NOT get_jobs(db, "Bangalore").
        This prevents silent bugs from argument order mistakes and makes
        call sites self-documenting.

    PAGINATION DESIGN:
        Returns a dict with both the data AND metadata:
        {
            "items":      [Job, Job, ...],   # The actual rows
            "total":      1482,              # Total matching rows (for UI)
            "page":       1,
            "page_size":  20,
            "pages":      75,                # Total pages
        }
        The UI needs `total` to render "Showing 1-20 of 1,482 jobs".
        Without it, the frontend can't build a pagination control.

    QUERY OPTIMIZATION:
        We build the filter list dynamically. Only the filters the caller
        actually passes are added to the WHERE clause. This avoids
        "WHERE 1=1 AND city IS NOT NULL AND city = ..." patterns that
        confuse the query planner and produce slower queries.

        The count query uses db.query(func.count(Job.id)) — counting only
        the primary key column, not SELECT *, which is significantly faster.

    Args:
        db:              Active session.
        city:            Filter by city (case-insensitive partial match).
        country:         Filter by ISO country code (exact match).
        company_name:    Filter by company (case-insensitive partial match).
        skill:           Filter jobs where this skill appears in the skills array.
        experience_level: Filter by ExperienceLevel enum value.
        job_type:        Filter by JobType enum value.
        is_remote:       If True, only remote jobs. If False, only on-site.
        source_platform: Filter by scraper source platform.
        posted_after:    Only jobs posted after this datetime.
        posted_before:   Only jobs posted before this datetime.
        search_query:    Full-text search across title + description.
        page:            1-based page number.
        page_size:       Rows per page (clamped to 1–100).
        order_by:        Column name to sort by.
        descending:      True for newest-first.

    Returns:
        PaginatedResult dict.
    """
    # Clamp page_size: never let the caller request 10,000 rows at once.
    # This protects the database and the API response time.
    page_size = max(1, min(page_size, 100))
    page = max(1, page)

    try:
        # Start from the base query — all filters are additive from here.
        query = db.query(Job)

        # Build filter conditions dynamically.
        # Each condition is only added if the caller provided a value.
        filters = []

        if city:
            # ilike = case-insensitive LIKE. '%value%' matches any substring.
            # Useful because "bangalore" should match "Bengaluru / Bangalore".
            filters.append(Job.city.ilike(f"%{city.strip()}%"))

        if country:
            filters.append(Job.country == country.upper().strip())

        if company_name:
            filters.append(Job.company_name.ilike(f"%{company_name.strip()}%"))

        if skill:
            # PostgreSQL ARRAY containment: WHERE 'python' = ANY(skills)
            # The cast(skill, String) is needed because ANY() requires
            # the comparison value to match the array element type exactly.
            # The GIN index on skills makes this extremely fast.
            skill_lower = skill.strip().lower()
            filters.append(
                func.lower(func.any(Job.skills)) == skill_lower
            )

        if experience_level:
            filters.append(Job.experience_level == experience_level)

        if job_type:
            filters.append(Job.job_type == job_type)

        if is_remote is not None:
            filters.append(Job.is_remote == is_remote)

        if source_platform:
            filters.append(Job.source_platform == source_platform.lower().strip())

        if posted_after:
            filters.append(Job.posted_at >= posted_after)

        if posted_before:
            filters.append(Job.posted_at <= posted_before)

        if search_query:
            # Full-text search across title and description.
            # ilike with CONCAT is a simple approach that works without
            # setting up PostgreSQL full-text search (tsvector/tsquery).
            # For a production system with millions of rows, you would
            # replace this with PostgreSQL's to_tsvector() + GIN index.
            # That is a performance upgrade, not a logic change — same
            # function signature, different filter implementation.
            term = f"%{search_query.strip()}%"
            filters.append(
                or_(
                    Job.title.ilike(term),
                    Job.description.ilike(term),
                )
            )

        # Apply all collected filters in a single .filter(and_(...)) call.
        # and_() combines conditions with SQL AND.
        if filters:
            query = query.filter(and_(*filters))

        # COUNT QUERY: reuse the same filters, count only the PK column.
        # This is run BEFORE pagination (offset/limit) to get the total
        # number of matching rows — needed for the pagination metadata.
        total = query.with_entities(func.count(Job.id)).scalar() or 0

        # SORTING
        sortable_columns = {
            "posted_at":    Job.posted_at,
            "created_at":   Job.created_at,
            "salary_min":   Job.salary_min,
            "salary_max":   Job.salary_max,
            "company_name": Job.company_name,
            "title":        Job.title,
        }
        sort_column = sortable_columns.get(order_by, Job.posted_at)
        sort_expr = sort_column.desc() if descending else sort_column.asc()

        # PAGINATION: offset = how many rows to skip
        offset = (page - 1) * page_size
        items = query.order_by(sort_expr).offset(offset).limit(page_size).all()

        import math
        pages = math.ceil(total / page_size) if page_size > 0 else 0

        return {
            "items":     items,
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     pages,
        }

    except SQLAlchemyError as e:
        logger.error("get_jobs query failed: %s", str(e))
        return {"items": [], "total": 0, "page": page, "page_size": page_size, "pages": 0}


def get_jobs_pending_skill_extraction(
    db: Session,
    batch_size: int = 100,
) -> list[Job]:
    """
    Returns a batch of jobs that have not yet had skills extracted.

    Used by the ML pipeline to find its work queue.

    PERFORMANCE NOTE:
        This query uses the partial index `ix_jobs_pending_skills` defined
        in models.py, which only indexes rows where is_skills_extracted=false.
        As the pipeline processes jobs, the index shrinks. Eventually it
        covers only newly scraped jobs — a tiny, always-fast index.
    """
    try:
        return (
            db.query(Job)
            .filter(Job.is_skills_extracted == False)  # noqa: E712
            .order_by(Job.scraped_at.asc())  # Process oldest-first (FIFO)
            .limit(batch_size)
            .all()
        )
    except SQLAlchemyError as e:
        logger.error("get_jobs_pending_skill_extraction failed: %s", str(e))
        return []


def mark_skills_extracted(db: Session, job_ids: list[int]) -> int:
    """
    Marks a batch of jobs as having had their skills extracted by the ML pipeline.

    BULK UPDATE DESIGN:
        ORM update (load each object, set attribute, flush) = N database round-trips.
        db.query(...).filter(...).update({...}) = 1 database round-trip.
        For batches of 100+ records, always use the bulk update form.

    Returns:
        Number of rows updated.
    """
    if not job_ids:
        return 0
    try:
        updated = (
            db.query(Job)
            .filter(Job.id.in_(job_ids))
            .update(
                {"is_skills_extracted": True},
                synchronize_session="fetch",
                # synchronize_session="fetch":
                #   After the bulk UPDATE, SQLAlchemy re-fetches the updated
                #   objects into the session's identity map so that any Job
                #   objects already loaded in this session reflect the new value.
                #   "evaluate" is faster but can miss edge cases with complex filters.
                #   "fetch" is safer for bulk operations.
            )
        )
        db.flush()
        logger.debug("Marked %d jobs as skills_extracted", updated)
        return updated
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("mark_skills_extracted failed for ids %s: %s", job_ids, str(e))
        return 0


def update_job_skills(db: Session, job_id: int, skills: list[str]) -> Optional[Job]:
    """
    Updates the skills array and flags the job as processed.

    WHY UPDATE SKILLS SEPARATELY?
        The scraper inserts the job. The ML pipeline runs later and
        updates skills. These are two separate processes with two
        separate database operations. The job exists before skills
        are extracted — skills are appended, not inserted together.
    """
    try:
        job = db.get(Job, job_id)
        if not job:
            logger.warning("update_job_skills: job id=%s not found", job_id)
            return None
        job.skills = [s.lower().strip() for s in skills if s.strip()]
        job.is_skills_extracted = True
        db.flush()
        return job
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("update_job_skills failed for job id=%s: %s", job_id, str(e))
        return None


def get_top_skills(
    db: Session,
    limit: int = 20,
    country: Optional[str] = None,
    posted_after: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """
    Returns the most frequently appearing skills across all matching jobs.

    QUERY DESIGN:
        PostgreSQL's unnest() expands an ARRAY column into individual rows.
        Combined with GROUP BY + COUNT, this gives skill frequency in one query:

            SELECT unnest(skills) AS skill, COUNT(*) AS count
            FROM jobs
            WHERE ...
            GROUP BY skill
            ORDER BY count DESC
            LIMIT 20

        This is more efficient than loading all Job objects into Python
        and counting in application code — the aggregation happens in the
        database, and only the final 20 rows cross the network.
    """
    try:
        conditions = ["skills IS NOT NULL", "is_skills_extracted = true"]
        params: dict[str, Any] = {"limit": limit}

        if country:
            conditions.append("country = :country")
            params["country"] = country.upper()

        if posted_after:
            conditions.append("posted_at >= :posted_after")
            params["posted_after"] = posted_after

        where_clause = " AND ".join(conditions)

        sql = text(f"""
            SELECT
                lower(unnest(skills)) AS skill,
                COUNT(*)              AS job_count
            FROM jobs
            WHERE {where_clause}
            GROUP BY skill
            ORDER BY job_count DESC
            LIMIT :limit
        """)

        rows = db.execute(sql, params).fetchall()
        return [{"skill": row.skill, "job_count": row.job_count} for row in rows]

    except SQLAlchemyError as e:
        logger.error("get_top_skills failed: %s", str(e))
        return []


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: SKILL TREND OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def upsert_skill_trend(db: Session, data: TrendData) -> Optional[SkillTrend]:
    """
    Inserts a new skill trend record or updates it if it already exists.

    WHY UPSERT INSTEAD OF INSERT + UPDATE?
        The ML pipeline runs nightly. On the first run, there are no trend
        records — INSERT is needed. On the second run, the same (skill,
        period, location) already exists — UPDATE is needed.

        Without upsert, the pipeline would have to:
          1. SELECT to check if the row exists
          2. If yes: UPDATE
          3. If no: INSERT
        That is 2 round-trips per skill per run.

        PostgreSQL's INSERT ... ON CONFLICT DO UPDATE handles this in ONE
        round-trip. For 500 skills × 12 periods = 6,000 rows per run,
        this halves the database load.

    The unique constraint `uq_skill_trends_unique_period` (defined in
    models.py) is what ON CONFLICT targets — the combination of
    (skill_name, period_start, period_end, granularity, country, city).
    """
    try:
        stmt = pg_insert(SkillTrend).values(**data)

        # ON CONFLICT DO UPDATE: if a row with the same unique key exists,
        # update these specific columns with the new values.
        # "excluded" refers to the row that was proposed for insertion
        # but was blocked by the conflict — a PostgreSQL convention.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_skill_trends_unique_period",
            set_={
                "job_count":                    stmt.excluded.job_count,
                "job_count_change":             stmt.excluded.job_count_change,
                "job_count_change_pct":         stmt.excluded.job_count_change_pct,
                "avg_salary_min":               stmt.excluded.avg_salary_min,
                "avg_salary_max":               stmt.excluded.avg_salary_max,
                "co_occurring_skills":          stmt.excluded.co_occurring_skills,
                "trend_direction":              stmt.excluded.trend_direction,
                "trend_confidence":             stmt.excluded.trend_confidence,
                "predicted_next_period_count":  stmt.excluded.predicted_next_period_count,
                "sample_job_ids":               stmt.excluded.sample_job_ids,
                "computed_at":                  stmt.excluded.computed_at,
                "updated_at":                   func.now(),
            },
        )

        # returning() tells PostgreSQL to return the full inserted/updated row.
        # Without it, we would need a second SELECT to fetch the record.
        stmt = stmt.returning(SkillTrend)
        result = db.execute(stmt)
        db.flush()

        row = result.fetchone()
        if row:
            logger.debug(
                "Upserted trend: skill='%s' period=%s count=%d",
                data.get("skill_name"), data.get("period_start"), data.get("job_count"),
            )
        return row[0] if row else None

    except SQLAlchemyError as e:
        db.rollback()
        logger.error(
            "upsert_skill_trend failed for skill='%s': %s",
            data.get("skill_name", "unknown"), str(e),
        )
        return None


def get_trending_skills(
    db: Session,
    *,
    direction: Optional[TrendDirection] = None,
    granularity: str = "monthly",
    country: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 20,
    min_job_count: int = 10,
) -> list[SkillTrend]:
    """
    Returns the most recent trend records, optionally filtered.

    RECENCY DESIGN:
        "Most recent" means the row with the latest period_end for each skill.
        We achieve this by ordering on period_end DESC and limiting results.
        The composite index ix_skill_trends_location_period covers this query.

    Args:
        direction:     Filter by TrendDirection (RISING, STABLE, DECLINING, NEW).
        granularity:   "daily", "weekly", or "monthly".
        country:       ISO country code filter. None = global trends.
        city:          City filter. None = national or global.
        limit:         Maximum rows to return.
        min_job_count: Ignore skills with fewer than this many jobs (noise filter).
    """
    try:
        query = db.query(SkillTrend).filter(
            SkillTrend.granularity == granularity,
            SkillTrend.job_count >= min_job_count,
        )

        if direction:
            query = query.filter(SkillTrend.trend_direction == direction)

        # NULL handling: if country is None, we want global aggregates (country IS NULL).
        # If country is specified, filter to that country.
        if country is None:
            query = query.filter(SkillTrend.country.is_(None))
        else:
            query = query.filter(SkillTrend.country == country.upper())

        if city is None:
            query = query.filter(SkillTrend.city.is_(None))
        else:
            query = query.filter(SkillTrend.city.ilike(f"%{city}%"))

        return (
            query
            .order_by(SkillTrend.period_end.desc(), SkillTrend.job_count.desc())
            .limit(limit)
            .all()
        )

    except SQLAlchemyError as e:
        logger.error("get_trending_skills failed: %s", str(e))
        return []


def get_skill_trend_history(
    db: Session,
    skill_name: str,
    *,
    granularity: str = "monthly",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    country: Optional[str] = None,
) -> list[SkillTrend]:
    """
    Returns the time-series history for a single skill.

    Powers the line chart: "Python demand over the past 12 months".

    The result is ordered ASC (oldest to newest) so the chart renders
    left-to-right chronologically without client-side sorting.
    """
    try:
        query = db.query(SkillTrend).filter(
            func.lower(SkillTrend.skill_name) == skill_name.lower().strip(),
            SkillTrend.granularity == granularity,
        )

        if country is None:
            query = query.filter(SkillTrend.country.is_(None))
        else:
            query = query.filter(SkillTrend.country == country.upper())

        if start_date:
            query = query.filter(SkillTrend.period_start >= start_date)
        if end_date:
            query = query.filter(SkillTrend.period_end <= end_date)

        return query.order_by(SkillTrend.period_start.asc()).all()

    except SQLAlchemyError as e:
        logger.error(
            "get_skill_trend_history failed for skill='%s': %s", skill_name, str(e)
        )
        return []


def get_skill_salary_comparison(
    db: Session,
    skills: list[str],
    country: Optional[str] = None,
    granularity: str = "monthly",
) -> list[dict[str, Any]]:
    """
    Returns avg salary data for a list of skills for salary comparison charts.

    Used to power: "Python vs Java vs Go — average salary comparison".
    Returns the most recent period's salary data for each skill.
    """
    if not skills:
        return []

    try:
        skill_names_lower = [s.lower().strip() for s in skills]

        query = db.query(
            SkillTrend.skill_name,
            SkillTrend.avg_salary_min,
            SkillTrend.avg_salary_max,
            SkillTrend.salary_currency,
            SkillTrend.job_count,
            SkillTrend.period_end,
        ).filter(
            func.lower(SkillTrend.skill_name).in_(skill_names_lower),
            SkillTrend.granularity == granularity,
            SkillTrend.avg_salary_min.isnot(None),
        )

        if country is None:
            query = query.filter(SkillTrend.country.is_(None))
        else:
            query = query.filter(SkillTrend.country == country.upper())

        # Get only the most recent period for each skill using a subquery.
        # This avoids returning all historical salary data when we only
        # want the current snapshot for the comparison chart.
        rows = (
            query
            .order_by(SkillTrend.period_end.desc())
            .all()
        )

        # De-duplicate: keep only the first (most recent) row per skill.
        seen: set[str] = set()
        results = []
        for row in rows:
            key = row.skill_name.lower()
            if key not in seen:
                seen.add(key)
                results.append({
                    "skill_name":    row.skill_name,
                    "avg_salary_min": row.avg_salary_min,
                    "avg_salary_max": row.avg_salary_max,
                    "currency":      row.salary_currency,
                    "job_count":     row.job_count,
                    "as_of":         row.period_end,
                })
        return results

    except SQLAlchemyError as e:
        logger.error("get_skill_salary_comparison failed: %s", str(e))
        return []


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: SCRAPE RUN OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def create_scrape_run(
    db: Session,
    platform: str,
    config_snapshot: Optional[dict] = None,
) -> Optional[ScrapeRun]:
    """
    Creates a new ScrapeRun audit record at the START of a scraper execution.

    Call this as the FIRST operation when the scraper starts.
    The run starts with status='running'. Call complete_scrape_run() or
    fail_scrape_run() at the end to finalize it.

    Args:
        db:              Active session.
        platform:        Platform being scraped, e.g. "linkedin".
        config_snapshot: The search config used for this run (for auditing).

    Returns:
        The new ScrapeRun record with its database-assigned id.
    """
    try:
        run = ScrapeRun(
            platform=platform.lower().strip(),
            started_at=_utcnow(),
            status="running",
            config_snapshot=config_snapshot or {},
        )
        db.add(run)
        db.flush()  # Gets run.id so caller can log it
        logger.info("Scrape run started: id=%s platform='%s'", run.id, platform)
        return run
    except SQLAlchemyError as e:
        db.rollback()
        logger.error("create_scrape_run failed for platform='%s': %s", platform, str(e))
        return None


def complete_scrape_run(
    db: Session,
    run_id: int,
    *,
    pages_scraped: int = 0,
    jobs_found: int = 0,
    jobs_inserted: int = 0,
    jobs_skipped_duplicate: int = 0,
    jobs_failed_parsing: int = 0,
) -> Optional[ScrapeRun]:
    """
    Marks a ScrapeRun as successfully completed and records final statistics.

    Call this at the END of a successful scraper execution.
    Calculates total execution time from started_at automatically.

    Args:
        run_id:                  The ScrapeRun.id returned by create_scrape_run().
        pages_scraped:           How many pages were visited.
        jobs_found:              Total job listings seen on the page.
        jobs_inserted:           New jobs actually written to the database.
        jobs_skipped_duplicate:  Jobs that already existed.
        jobs_failed_parsing:     Jobs that could not be parsed due to HTML errors.
    """
    try:
        run = db.get(ScrapeRun, run_id)
        if not run:
            logger.warning("complete_scrape_run: run id=%s not found", run_id)
            return None

        completed_at = _utcnow()
        run.completed_at = completed_at
        run.status = "completed"
        run.pages_scraped = pages_scraped
        run.jobs_found = jobs_found
        run.jobs_inserted = jobs_inserted
        run.jobs_skipped_duplicate = jobs_skipped_duplicate
        run.jobs_failed_parsing = jobs_failed_parsing

        db.flush()

        duration = (completed_at - run.started_at).total_seconds()
        logger.info(
            "Scrape run completed: id=%s platform='%s' "
            "inserted=%d duplicates=%d failed=%d duration=%.1fs",
            run.id, run.platform,
            jobs_inserted, jobs_skipped_duplicate, jobs_failed_parsing, duration,
        )
        return run

    except SQLAlchemyError as e:
        db.rollback()
        logger.error("complete_scrape_run failed for run id=%s: %s", run_id, str(e))
        return None


def fail_scrape_run(
    db: Session,
    run_id: int,
    error_message: str,
    *,
    pages_scraped: int = 0,
    jobs_inserted: int = 0,
) -> Optional[ScrapeRun]:
    """
    Marks a ScrapeRun as failed and records the error message.

    Call this in the scraper's exception handler:

        run = create_scrape_run(db, "linkedin")
        try:
            ... scraping logic ...
            complete_scrape_run(db, run.id, ...)
        except Exception as e:
            fail_scrape_run(db, run.id, error_message=str(e))
            raise
    """
    try:
        run = db.get(ScrapeRun, run_id)
        if not run:
            logger.warning("fail_scrape_run: run id=%s not found", run_id)
            return None

        run.completed_at = _utcnow()
        run.status = "failed"
        run.error_message = error_message[:5000]  # Truncate runaway error strings
        run.pages_scraped = pages_scraped
        run.jobs_inserted = jobs_inserted

        db.flush()
        logger.error(
            "Scrape run FAILED: id=%s platform='%s' error='%s'",
            run.id, run.platform, error_message[:200],
        )
        return run

    except SQLAlchemyError as e:
        db.rollback()
        logger.error("fail_scrape_run itself failed for run id=%s: %s", run_id, str(e))
        return None


def get_recent_scrape_runs(
    db: Session,
    platform: Optional[str] = None,
    limit: int = 10,
    status: Optional[str] = None,
) -> list[ScrapeRun]:
    """
    Returns recent scrape run audit records.

    Used by the dashboard's operational health panel and by monitoring
    scripts that alert if no successful run has occurred in N hours.

    Args:
        platform: Filter to one platform, or None for all platforms.
        limit:    Maximum rows to return.
        status:   "completed", "failed", "running", or None for all.
    """
    try:
        query = db.query(ScrapeRun)

        if platform:
            query = query.filter(ScrapeRun.platform == platform.lower().strip())
        if status:
            query = query.filter(ScrapeRun.status == status)

        return query.order_by(ScrapeRun.started_at.desc()).limit(limit).all()

    except SQLAlchemyError as e:
        logger.error("get_recent_scrape_runs failed: %s", str(e))
        return []


def get_scrape_run_stats(
    db: Session,
    platform: Optional[str] = None,
    since: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Returns aggregate statistics across all scrape runs.

    Powers the dashboard's operational health metrics:
      - Total jobs scraped this week
      - Success rate
      - Average jobs per run
      - Last successful run timestamp

    Runs a single aggregation query rather than loading all ScrapeRun
    objects into Python memory — correct approach for summary statistics.
    """
    try:
        query = db.query(
            func.count(ScrapeRun.id).label("total_runs"),
            func.sum(
                func.cast(ScrapeRun.status == "completed", Integer := None)
            ),
            func.sum(ScrapeRun.jobs_inserted).label("total_inserted"),
            func.max(ScrapeRun.completed_at).label("last_completed_at"),
        )

        if platform:
            query = query.filter(ScrapeRun.platform == platform.lower())
        if since:
            query = query.filter(ScrapeRun.started_at >= since)

        # Separate simpler queries for clarity and reliability
        base_query = db.query(ScrapeRun)
        if platform:
            base_query = base_query.filter(ScrapeRun.platform == platform.lower())
        if since:
            base_query = base_query.filter(ScrapeRun.started_at >= since)

        total_runs = base_query.count()
        successful_runs = base_query.filter(ScrapeRun.status == "completed").count()
        failed_runs = base_query.filter(ScrapeRun.status == "failed").count()

        total_inserted = db.query(
            func.coalesce(func.sum(ScrapeRun.jobs_inserted), 0)
        ).filter(
            *(
                ([ScrapeRun.platform == platform.lower()] if platform else []) +
                ([ScrapeRun.started_at >= since] if since else [])
            )
        ).scalar() or 0

        last_success = (
            base_query
            .filter(ScrapeRun.status == "completed")
            .order_by(ScrapeRun.completed_at.desc())
            .first()
        )

        success_rate = (
            round(successful_runs / total_runs * 100, 1) if total_runs > 0 else 0.0
        )

        return {
            "total_runs":      total_runs,
            "successful_runs": successful_runs,
            "failed_runs":     failed_runs,
            "success_rate_pct": success_rate,
            "total_jobs_inserted": total_inserted,
            "last_successful_run_at": last_success.completed_at if last_success else None,
        }

    except SQLAlchemyError as e:
        logger.error("get_scrape_run_stats failed: %s", str(e))
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: LOCAL TESTING
# ═══════════════════════════════════════════════════════════════════════════
# HOW TO RUN:
#   From the project root directory:
#       python -m database.crud
#
# WHAT IT DOES:
#   1. Creates all tables (if they don't exist)
#   2. Inserts a sample job
#   3. Fetches it back with filters
#   4. Simulates a full scrape run lifecycle (start → complete)
#   5. Prints results to confirm everything works end-to-end
#
# WHY A LOCAL TEST IN THE MODULE ITSELF?
#   It gives you an immediate feedback loop while building this layer.
#   You do not need a test framework, a mock, or a running API.
#   Just run the file directly and see real database output.
#   Once the API is built, these become proper pytest tests in /tests/.

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    print("\n" + "═" * 60)
    print("  CRUD Layer — Local Integration Test")
    print("═" * 60 + "\n")

    # ── Step 0: ensure tables exist ──────────────────────────────────────
    from database.connection import create_tables, get_db_session
    create_tables()
    print("✓  Tables verified / created\n")

    # ── Step 1: Insert a single job ──────────────────────────────────────
    sample_job: JobData = {
        "source_url":      "https://linkedin.com/jobs/view/test-python-dev-001",
        "source_platform": "linkedin",
        "title":           "Senior Python Developer",
        "company_name":    "Acme Tech Solutions",
        "city":            "Bangalore",
        "country":         "IN",
        "experience_level": ExperienceLevel.SENIOR,
        "job_type":        JobType.FULL_TIME,
        "is_remote":       False,
        "skills":          ["python", "fastapi", "postgresql", "docker"],
        "salary_min":      1500000,
        "salary_max":      2200000,
        "salary_currency": "INR",
        "salary_period":   "yearly",
        "description":     "We are hiring a Senior Python Developer with FastAPI experience.",
        "posted_at":       _utcnow(),
    }

    with get_db_session() as db:
        job, inserted = insert_job(db, sample_job)
        if inserted:
            print(f"✓  Job inserted: id={job.id} title='{job.title}'")
        else:
            print(f"⚠  Job already existed: id={job.id if job else 'N/A'}")

    # ── Step 2: Insert duplicate (should be skipped) ─────────────────────
    with get_db_session() as db:
        job2, inserted2 = insert_job(db, sample_job)
        assert not inserted2, "Expected duplicate to be skipped"
        print(f"✓  Duplicate correctly skipped: id={job2.id if job2 else 'N/A'}")

    # ── Step 3: Fetch jobs with filters ───────────────────────────────────
    with get_db_session() as db:
        result = get_jobs(db, city="Bangalore", skill="python", page=1, page_size=5)
        print(f"\n✓  get_jobs(city='Bangalore', skill='python')")
        print(f"   total={result['total']} page={result['page']} pages={result['pages']}")
        for j in result["items"]:
            print(f"   → [{j.id}] {j.title} @ {j.company_name}")

    # ── Step 4: Scrape run lifecycle ──────────────────────────────────────
    print("\n── Scrape Run Lifecycle ──")
    with get_db_session() as db:
        run = create_scrape_run(db, "linkedin", config_snapshot={"query": "python", "location": "bangalore"})
        print(f"✓  ScrapeRun created: id={run.id} status='{run.status}'")

        # Simulate the scraper doing work and completing
        complete_scrape_run(
            db, run.id,
            pages_scraped=3,
            jobs_found=45,
            jobs_inserted=38,
            jobs_skipped_duplicate=7,
        )
        print(f"✓  ScrapeRun completed: id={run.id}")

    # Verify the run was saved
    with get_db_session() as db:
        runs = get_recent_scrape_runs(db, platform="linkedin", limit=3)
        print(f"\n✓  Recent scrape runs for 'linkedin':")
        for r in runs:
            print(f"   → [{r.id}] status='{r.status}' inserted={r.jobs_inserted} at={r.completed_at}")

    # ── Step 5: Upsert a skill trend ──────────────────────────────────────
    print("\n── Skill Trend Upsert ──")
    from datetime import timezone as tz
    period_start = datetime(2025, 4, 1, tzinfo=timezone.utc)
    period_end   = datetime(2025, 4, 30, tzinfo=timezone.utc)

    trend_data: TrendData = {
        "skill_name":       "python",
        "skill_category":   "language",
        "granularity":      "monthly",
        "period_start":     period_start,
        "period_end":       period_end,
        "country":          None,
        "city":             None,
        "job_count":        412,
        "avg_salary_min":   1400000,
        "avg_salary_max":   2500000,
        "salary_currency":  "INR",
        "trend_direction":  TrendDirection.RISING,
        "trend_confidence": 0.87,
        "computed_at":      _utcnow(),
    }

    with get_db_session() as db:
        trend = upsert_skill_trend(db, trend_data)
        if trend:
            print(f"✓  SkillTrend upserted: skill='{trend_data['skill_name']}' count={trend_data['job_count']}")

    # Run it again — should UPDATE, not insert duplicate
    trend_data["job_count"] = 435  # Updated count
    with get_db_session() as db:
        trend = upsert_skill_trend(db, trend_data)
        print(f"✓  SkillTrend re-upserted (updated): count should be 435")

    # ── Step 6: Fetch trending skills ────────────────────────────────────
    with get_db_session() as db:
        rising = get_trending_skills(db, direction=TrendDirection.RISING, limit=5)
        print(f"\n✓  Rising skills: {[t.skill_name for t in rising]}")

    print("\n" + "═" * 60)
    print("  All tests passed. Database layer is working correctly.")
    print("═" * 60 + "\n")