"""
database/models.py

PURPOSE:
    Defines the complete data schema for the AI Job Market Intelligence Platform.
    Every table, column, index, constraint, and relationship lives here.

DESIGN PHILOSOPHY:
    Models are the contract between your application and your database.
    They should be designed with two questions in mind:
      1. What data do we collect? (completeness)
      2. How will we query it? (performance via indexes)

    A model designed well at the start saves enormous pain later.
    Adding indexes to a 10-million-row table after the fact is painful
    and requires locking the table — sometimes for hours.
"""

from __future__ import annotations  # Allows forward references in type hints

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship, Mapped

from database.connection import Base


# ---------------------------------------------------------------------------
# PYTHON ENUMS FOR CONTROLLED VOCABULARIES
# ---------------------------------------------------------------------------
# WHY ENUMS IN THE DATABASE?
#   Storing experience level as a free-text string (VARCHAR) means you'll
#   inevitably get: "senior", "Senior", "SENIOR", "Sr.", "Senior Level" —
#   five different values that mean the same thing, making aggregation
#   impossible without a messy cleanup step.
#
#   PostgreSQL native ENUM types enforce that only valid values are stored.
#   SQLAlchemy's Enum() type maps Python's enum.Enum to PostgreSQL's ENUM.
#
# WHY INHERIT FROM str AND enum.Enum?
#   Inheriting from `str` means the enum's value IS the string.
#   This allows JSON serialization to work transparently and lets you
#   compare: ExperienceLevel.SENIOR == "senior" → True.
#   Without `str`, FastAPI's JSON serializer would fail on enum values.

class ExperienceLevel(str, enum.Enum):
    INTERNSHIP  = "internship"
    ENTRY       = "entry"       # 0–2 years
    MID         = "mid"         # 2–5 years
    SENIOR      = "senior"      # 5–10 years
    LEAD        = "lead"        # Team lead / Staff
    PRINCIPAL   = "principal"   # Principal / Architect
    EXECUTIVE   = "executive"   # CTO, VP Engineering

    @classmethod
    def _missing_(cls, value):
        """Gracefully handle unknown values from scraped data."""
        return cls.MID  # Sensible fallback rather than a crash


class JobType(str, enum.Enum):
    FULL_TIME   = "full_time"
    PART_TIME   = "part_time"
    CONTRACT    = "contract"
    FREELANCE   = "freelance"
    INTERNSHIP  = "internship"


class TrendDirection(str, enum.Enum):
    RISING      = "rising"      # Demand increasing
    STABLE      = "stable"      # Demand holding steady
    DECLINING   = "declining"   # Demand decreasing
    NEW         = "new"         # Newly observed skill (insufficient history)


# ---------------------------------------------------------------------------
# MIXIN: TIMESTAMPS
# ---------------------------------------------------------------------------
# WHY A MIXIN?
#   Every table needs created_at and updated_at timestamps for auditing,
#   debugging, and trend analysis. Instead of repeating these 4 lines in
#   every model, we define them once in a mixin and inherit it.
#
#   This is the DRY principle (Don't Repeat Yourself) applied to models.
#
# HOW server_default WORKS:
#   server_default="now()" tells PostgreSQL to set the value using its
#   own NOW() function at insert time. This is more reliable than
#   Python-side defaults because it uses the database server's clock
#   (consistent across all app instances) and works even for direct
#   SQL inserts (e.g., from a DBA running a manual query).
#
# onupdate=func.now():
#   SQLAlchemy automatically sets this column to the current timestamp
#   whenever the row is modified through the ORM. Invaluable for
#   tracking when records were last changed.

class TimestampMixin:
    """Adds created_at and updated_at audit columns to any model."""

    created_at: Mapped[datetime] = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="UTC timestamp when this record was first created",
    )
    updated_at: Mapped[datetime] = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="UTC timestamp when this record was last modified",
    )


# ---------------------------------------------------------------------------
# MODEL: Job
# ---------------------------------------------------------------------------
# REPRESENTS: One job posting scraped from a source (LinkedIn, Indeed, etc.)
#
# TABLE NAME CONVENTION:
#   SQLAlchemy uses __tablename__ to map the class to a database table.
#   Convention: lowercase, plural, snake_case. "jobs" not "Job" or "Jobs".
#
# PRIMARY KEY DESIGN — Why BigInteger?
#   Integer (32-bit) supports ~2.1 billion rows. That sounds like a lot,
#   but if you scrape 100,000 jobs/day, you exhaust Integer in ~58 years.
#   However, if you ever reset sequences or do bulk inserts, you can
#   exhaust it faster. BigInteger (64-bit) supports 9.2 quintillion rows.
#   The storage cost difference (4 bytes vs 8 bytes) is negligible.
#   Always use BigInteger for primary keys in new projects.
#
# POSTGRESQL-SPECIFIC TYPES:
#   ARRAY(String): PostgreSQL native array. Stores a list of strings
#     (extracted skills) as a single column value. This avoids a separate
#     `job_skills` junction table for a simple many-to-many that we never
#     need to query inversely in complex ways.
#
#   JSONB: Binary JSON. Unlike TEXT, JSONB is indexed, queryable, and
#     validated. We use it for `raw_metadata` to store extra scraped
#     fields (benefits, team size, tech stack bullets) without designing
#     explicit columns for every possible field. Schema-flexible storage
#     alongside structured columns — the best of both worlds.

class Job(TimestampMixin, Base):
    """
    Core entity: a single job posting scraped from any source.

    One Job belongs to zero or one Company (optional relationship).
    One Job has many Trend records (through the SkillTrend aggregate).
    """

    __tablename__ = "jobs"

    # --- Primary Key ---
    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Internal surrogate primary key",
    )

    # --- Source Tracking ---
    # WHY source_url UNIQUE?
    #   The scraper may run multiple times per day. Without a unique
    #   constraint on source_url, each run would duplicate every job.
    #   The unique constraint enforces deduplication at the database level
    #   (the safest layer) — even if scraper-side dedup logic has a bug.
    source_url = Column(
        String(2048),
        nullable=False,
        unique=True,
        index=True,     # B-tree index for fast lookups during dedup check
        comment="The canonical URL of the original job posting",
    )
    source_platform = Column(
        String(100),
        nullable=False,
        index=True,
        comment="e.g. linkedin, indeed, naukri, glassdoor",
    )
    external_id = Column(
        String(256),
        nullable=True,
        comment="Platform's own ID for the job (if available in HTML/API)",
    )

    # --- Core Job Information ---
    title = Column(
        String(512),
        nullable=False,
        comment="Raw job title as scraped, e.g. 'Senior Python Developer'",
    )
    title_normalized = Column(
        String(256),
        nullable=True,
        comment="Cleaned/standardized title for grouping, e.g. 'software_engineer'",
    )
    description = Column(
        Text,
        nullable=True,
        comment="Full job description text, used by NLP skill extractor",
    )
    description_summary = Column(
        Text,
        nullable=True,
        comment="AI-generated 3–5 sentence summary of the job description",
    )

    # --- Classification ---
    experience_level = Column(
        Enum(ExperienceLevel),
        nullable=True,
        index=True,
        comment="Seniority inferred from title and description by ML pipeline",
    )
    job_type = Column(
        Enum(JobType),
        nullable=True,
        index=True,
        comment="Employment type inferred from listing",
    )
    is_remote = Column(
        Boolean,
        nullable=True,
        default=False,
        comment="True if the job explicitly allows full remote work",
    )
    is_hybrid = Column(
        Boolean,
        nullable=True,
        default=False,
    )

    # --- Company ---
    # We store company name directly on Job for simplicity during Phase 1.
    # In a later phase, this normalizes into a separate Company table with
    # a ForeignKey. The comment documents this as a known future refactor.
    company_name = Column(
        String(512),
        nullable=True,
        index=True,
        comment="Raw company name as scraped. Will be FK to companies table in v2.",
    )
    company_size = Column(
        String(64),
        nullable=True,
        comment="e.g. '51-200 employees', '1001-5000 employees'",
    )
    company_industry = Column(
        String(256),
        nullable=True,
        comment="e.g. 'Information Technology', 'FinTech'",
    )

    # --- Location ---
    location_raw = Column(
        String(512),
        nullable=True,
        comment="Location exactly as scraped, e.g. 'Bengaluru, Karnataka, India'",
    )
    city = Column(
        String(256),
        nullable=True,
        index=True,
        comment="Parsed city name, normalized",
    )
    state = Column(
        String(256),
        nullable=True,
        comment="Parsed state/province",
    )
    country = Column(
        String(100),
        nullable=True,
        index=True,
        comment="ISO 3166-1 alpha-2 country code, e.g. 'IN', 'US'",
    )
    latitude = Column(Float, nullable=True, comment="For map visualizations")
    longitude = Column(Float, nullable=True)

    # --- Compensation ---
    # WHY SEPARATE MIN/MAX INSTEAD OF A RANGE STRING?
    #   Storing "₹15-20 LPA" as text is useless for analysis.
    #   We parse it during cleaning into two numeric fields.
    #   Null means salary was not disclosed (extremely common).
    salary_min = Column(
        BigInteger,
        nullable=True,
        comment="Minimum salary in smallest currency unit (paise or cents)",
    )
    salary_max = Column(
        BigInteger,
        nullable=True,
        comment="Maximum salary in smallest currency unit",
    )
    salary_currency = Column(
        String(10),
        nullable=True,
        default="INR",
        comment="ISO 4217 currency code, e.g. 'INR', 'USD'",
    )
    salary_period = Column(
        String(20),
        nullable=True,
        comment="e.g. 'yearly', 'monthly', 'hourly'",
    )
    salary_raw = Column(
        String(256),
        nullable=True,
        comment="Original salary text before parsing, for auditing",
    )

    # --- Skills (NLP Output) ---
    # WHY POSTGRESQL ARRAY?
    #   Storing ["Python", "FastAPI", "Docker"] as a PostgreSQL ARRAY lets
    #   you query: WHERE 'Python' = ANY(skills) — a single-table query
    #   with no joins. For our use case (read-heavy analytics), this is
    #   significantly simpler than a junction table approach.
    #
    #   Trade-off: If you need complex skill-to-skill queries ("find all
    #   jobs that require Python AND Docker but NOT Java"), a normalized
    #   skills junction table would perform better. For trend counting,
    #   ARRAY is sufficient and far simpler.
    skills = Column(
        ARRAY(String(100)),
        nullable=True,
        default=list,
        comment="List of tech skills extracted by spaCy NLP pipeline",
    )

    # --- Dates ---
    posted_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When the job was posted on the source platform",
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Application deadline, if shown",
    )
    scraped_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When our scraper collected this record",
    )

    # --- ML Pipeline Flags ---
    # WHY PROCESSING STATUS FLAGS?
    #   After scraping, several async pipeline stages process each job:
    #   skill extraction, salary normalization, geocoding, trend aggregation.
    #   These flags let us efficiently query "which jobs need ML processing?"
    #   and resume after failures without reprocessing everything.
    is_skills_extracted = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="True when spaCy has processed this job's description",
    )
    is_salary_normalized = Column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_geocoded = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    # --- Flexible Extra Data ---
    raw_metadata = Column(
        JSONB,
        nullable=True,
        comment=(
            "Schema-flexible store for extra scraped fields: benefits, perks, "
            "number of applicants, required certifications, etc. Queryable via "
            "JSONB operators: raw_metadata->>'benefits'"
        ),
    )

    # --- Data Quality ---
    quality_score = Column(
        Float,
        nullable=True,
        comment=(
            "0.0–1.0 score reflecting completeness of the record. "
            "Computed by the cleaner pipeline. Used to filter low-quality data "
            "from ML training sets."
        ),
    )

    # -------------------------------------------------------------------
    # TABLE CONSTRAINTS & INDEXES
    # -------------------------------------------------------------------
    # WHY DEFINE INDEXES HERE INSTEAD OF ON COLUMNS?
    #   Some indexes span MULTIPLE columns (composite indexes). These
    #   cannot be defined inline on a single column — they go in
    #   __table_args__ as Index() objects.
    #
    # COMPOSITE INDEX STRATEGY:
    #   The most common query in the dashboard will be:
    #     "Show me Python jobs in Bangalore posted in the last 30 days"
    #   → Filters: skills (ARRAY contains), city, posted_at
    #
    #   Index: (city, posted_at) covers the range scan.
    #   The ARRAY GIN index on skills covers the ANY() lookup.
    #
    # GIN INDEX (Generalized Inverted Index):
    #   Standard B-tree indexes don't work on ARRAY or JSONB columns.
    #   GIN indexes are designed for these "multi-valued" types.
    #   They let you efficiently run: WHERE 'Python' = ANY(skills)
    #   Without GIN: PostgreSQL scans every row (full table scan).
    #   With GIN: millisecond lookup even on millions of rows.
    __table_args__ = (
        # Composite index: city + country + posted_at
        # Covers: "Python jobs in Bangalore, India, last 30 days"
        Index("ix_jobs_location_date", "city", "country", "posted_at"),

        # Composite index: experience_level + posted_at
        # Covers: "Senior jobs posted this month"
        Index("ix_jobs_level_date", "experience_level", "posted_at"),

        # Composite index: company_name + posted_at
        # Covers: "Jobs from Google this quarter"
        Index("ix_jobs_company_date", "company_name", "posted_at"),

        # GIN index on the skills ARRAY for ANY() queries
        # Covers: WHERE 'Python' = ANY(skills)
        Index(
            "ix_jobs_skills_gin",
            "skills",
            postgresql_using="gin",
        ),

        # GIN index on JSONB metadata for flexible field queries
        Index(
            "ix_jobs_metadata_gin",
            "raw_metadata",
            postgresql_using="gin",
        ),

        # Partial index: only unprocessed jobs — much smaller than a full index
        # Covers: WHERE is_skills_extracted = false (the ML queue query)
        # A partial index covering just unprocessed rows is tiny and fast.
        Index(
            "ix_jobs_pending_skills",
            "scraped_at",
            postgresql_where="is_skills_extracted = false",
        ),

        # Explicit table-level comment for pgAdmin visibility
        {"comment": "Core table: one row per unique job posting scraped from any platform"},
    )

    def __repr__(self) -> str:
        return (
            f"<Job id={self.id} title='{self.title[:40]}' "
            f"company='{self.company_name}' platform='{self.source_platform}'>"
        )


# ---------------------------------------------------------------------------
# MODEL: SkillTrend
# ---------------------------------------------------------------------------
# REPRESENTS: Aggregated demand statistics for a single skill, per time period.
#
# DESIGN DECISION — Aggregation Table vs. Live Computation:
#   Option A (Live): Every time the dashboard requests trend data,
#     compute it on the fly: SELECT skill, COUNT(*) FROM jobs
#     WHERE posted_at > NOW() - INTERVAL '30 days' GROUP BY skill
#
#     Problem: With 1M+ job rows, this GROUP BY takes seconds. The dashboard
#     becomes unusably slow for users.
#
#   Option B (Pre-aggregated, chosen here):
#     The ML pipeline runs nightly and writes aggregated results into
#     this table. Dashboard queries hit pre-computed rows.
#
#     SELECT * FROM skill_trends WHERE period_end > NOW() - INTERVAL '1 day'
#     This is a tiny, indexed query — milliseconds regardless of job volume.
#
#   Trade-off: Data is up to 24 hours stale. For job market trends,
#   that's perfectly acceptable. Real-time trend computation would be
#   over-engineering for this use case.
#
# GRANULARITY DESIGN:
#   We store ONE row per (skill, period_start, period_end) combination.
#   This lets the dashboard show: daily, weekly, or monthly trends
#   without changing the table structure — just filter on date range.

class SkillTrend(TimestampMixin, Base):
    """
    Pre-aggregated skill demand statistics per time period.

    Populated nightly by the ML pipeline (trend_analyzer.py).
    Read by FastAPI trend endpoints to power dashboard charts.
    """

    __tablename__ = "skill_trends"

    # --- Primary Key ---
    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # --- The Skill Being Measured ---
    skill_name = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Normalized skill name, e.g. 'python', 'react', 'machine_learning'",
    )
    skill_category = Column(
        String(100),
        nullable=True,
        comment=(
            "Logical grouping, e.g. 'language', 'framework', 'cloud', "
            "'database', 'devops', 'soft_skill'"
        ),
    )

    # --- Time Period ---
    # WHY period_start + period_end INSTEAD OF a period_type STRING?
    #   Storing the actual date boundaries is more flexible than storing
    #   "2024-01" or "week-42". You can query any arbitrary date range:
    #   WHERE period_start >= '2024-01-01' AND period_end <= '2024-03-31'
    #   This works for daily, weekly, monthly, or quarterly rollups
    #   all in the same table, with the same query pattern.
    period_start = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="Start of the aggregation window (inclusive)",
    )
    period_end = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="End of the aggregation window (exclusive)",
    )
    granularity = Column(
        String(20),
        nullable=False,
        default="monthly",
        comment="Aggregation level: 'daily', 'weekly', 'monthly'",
    )

    # --- Geographic Scope ---
    # NULL means "all locations globally" — a global aggregate row.
    # When set, it's a regional aggregate. This lets one table serve
    # both "Global Python demand" and "Bangalore Python demand" queries.
    country = Column(
        String(100),
        nullable=True,
        index=True,
        comment="ISO country code, NULL = global aggregate",
    )
    city = Column(
        String(256),
        nullable=True,
        comment="City name, NULL = national or global aggregate",
    )

    # --- Demand Metrics ---
    job_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of job postings mentioning this skill in this period",
    )
    job_count_change = Column(
        Integer,
        nullable=True,
        comment="Difference from the previous equivalent period (can be negative)",
    )
    job_count_change_pct = Column(
        Float,
        nullable=True,
        comment="Percentage change from previous period, e.g. 23.5 for +23.5%",
    )

    # --- Salary Metrics for This Skill ---
    # WHY STORE SALARY ON TRENDS?
    #   "What's the average salary for Python engineers this year vs. last year?"
    #   This query needs both trend data and salary data in the same time bucket.
    #   Pre-aggregating salary here makes this query a simple row lookup.
    avg_salary_min = Column(BigInteger, nullable=True)
    avg_salary_max = Column(BigInteger, nullable=True)
    salary_currency = Column(String(10), nullable=True, default="INR")

    # --- Co-occurrence: Skills That Appear Together ---
    # JSONB field: {"docker": 0.73, "kubernetes": 0.61, "aws": 0.58}
    # Meaning: 73% of Python jobs also require Docker.
    # This powers the "related skills" feature in the dashboard.
    # JSONB is the right type here — the keys are dynamic skill names.
    co_occurring_skills = Column(
        JSONB,
        nullable=True,
        comment=(
            "Map of {skill_name: correlation_coefficient} for skills that "
            "frequently appear alongside this one in the same period"
        ),
    )

    # --- ML Prediction ---
    trend_direction = Column(
        Enum(TrendDirection),
        nullable=True,
        index=True,
        comment="ML-predicted demand direction for the NEXT period",
    )
    trend_confidence = Column(
        Float,
        nullable=True,
        comment="Model confidence in trend_direction, 0.0–1.0",
    )
    predicted_next_period_count = Column(
        Integer,
        nullable=True,
        comment="Model's forecast for job_count in the following period",
    )

    # --- Source Data Quality ---
    sample_job_ids = Column(
        ARRAY(BigInteger),
        nullable=True,
        comment=(
            "Sample of Job.id values that were counted in this aggregate. "
            "Useful for debugging and auditing trend calculations."
        ),
    )
    computed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the ML pipeline computed this trend record",
    )

    # -------------------------------------------------------------------
    # CONSTRAINTS & INDEXES
    # -------------------------------------------------------------------
    __table_args__ = (
        # UNIQUE CONSTRAINT: Prevents duplicate trend records for the same
        # (skill, period, location) combination. If the ML pipeline runs twice,
        # it should UPDATE the existing record, not INSERT a duplicate.
        # In crud.py, we'll use INSERT ... ON CONFLICT DO UPDATE (upsert).
        UniqueConstraint(
            "skill_name",
            "period_start",
            "period_end",
            "granularity",
            "country",
            "city",
            name="uq_skill_trends_unique_period",
        ),

        # Most common dashboard query: trending skills this month, globally
        # This composite index covers: skill_name + period range + country
        Index("ix_skill_trends_skill_period", "skill_name", "period_start", "period_end"),

        # Query: "What's trending in Bangalore right now?"
        Index("ix_skill_trends_location_period", "country", "city", "period_end"),

        # Query: "Show me all Rising skills"
        Index("ix_skill_trends_direction", "trend_direction", "period_end"),

        # GIN index on co_occurring_skills JSONB for skill correlation queries
        Index(
            "ix_skill_trends_co_occurring_gin",
            "co_occurring_skills",
            postgresql_using="gin",
        ),

        {"comment": "Pre-aggregated skill demand metrics, populated by the nightly ML pipeline"},
    )

    def __repr__(self) -> str:
        return (
            f"<SkillTrend skill='{self.skill_name}' "
            f"period={self.period_start.date()}→{self.period_end.date()} "
            f"count={self.job_count} direction={self.trend_direction}>"
        )


# ---------------------------------------------------------------------------
# MODEL: ScrapeRun
# ---------------------------------------------------------------------------
# PURPOSE:
#   An audit log. Every time the scraper runs, it creates one ScrapeRun record.
#   This answers critical operational questions:
#     - When did we last successfully scrape LinkedIn?
#     - How many jobs did we collect in each run?
#     - Did any scrape runs fail? Why?
#     - Is our scraper getting blocked more often recently?
#
# WHY IS THIS IMPORTANT FOR PRODUCTION?
#   Without this, if your scraper silently stops working (site changed HTML,
#   IP banned, network issue), you won't know for days — your database stops
#   getting new data, your trends go stale, and users lose trust.
#   With this table, you can set an alert: "If no successful ScrapeRun in 12h
#   for LinkedIn, send a Slack notification."

class ScrapeRun(TimestampMixin, Base):
    """
    Audit record for every scraper execution.

    One ScrapeRun represents one execution of the scraper for one platform.
    """

    __tablename__ = "scrape_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    platform = Column(
        String(100),
        nullable=False,
        index=True,
        comment="e.g. 'linkedin', 'indeed', 'naukri'",
    )
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        String(50),
        nullable=False,
        default="running",
        comment="'running', 'completed', 'failed', 'partial'",
    )
    pages_scraped = Column(Integer, nullable=True, default=0)
    jobs_found = Column(Integer, nullable=True, default=0)
    jobs_inserted = Column(Integer, nullable=True, default=0)
    jobs_skipped_duplicate = Column(Integer, nullable=True, default=0)
    jobs_failed_parsing = Column(Integer, nullable=True, default=0)
    error_message = Column(Text, nullable=True)
    config_snapshot = Column(
        JSONB,
        nullable=True,
        comment="The search parameters used: {query, location, date_filter}",
    )

    __table_args__ = (
        Index("ix_scrape_runs_platform_started", "platform", "started_at"),
        {"comment": "Audit log of every scraper execution for operational monitoring"},
    )

    def __repr__(self) -> str:
        return (
            f"<ScrapeRun id={self.id} platform='{self.platform}' "
            f"status='{self.status}' jobs={self.jobs_inserted}>"
        )
