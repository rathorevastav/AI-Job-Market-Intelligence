"""
ml/trend_analyzer.py

Computes skill demand trends and writes pre-aggregated results to skill_trends.

COMPUTATION FLOW:
    For each month in the analysis window:
      1. Load all jobs posted in that month (from PostgreSQL via raw SQL agg)
      2. Count job occurrences per skill
      3. Compare to the previous month → compute change and change_pct
      4. Classify trend direction (RISING / STABLE / DECLINING / NEW)
      5. Compute co-occurring skills (which skills appear together)
      6. Aggregate salary data per skill
      7. Upsert results into skill_trends via crud.upsert_skill_trend()

    Runs in two scopes:
        - Global (country=None, city=None)
        - Per top-5 country (country=XX, city=None)

HOW TO RUN:
    python -m ml.trend_analyzer

    # Analyse specific number of months back:
    python -m ml.trend_analyzer --months-back 6
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

import database.crud as crud
from database.connection import get_db_session
from database.models import TrendDirection
from ml.constants import (
    MIN_JOBS_FOR_TREND,
    TREND_DECLINING_THRESHOLD,
    TREND_RISING_THRESHOLD,
    SALARY_MIN_ANNUAL_USD,
    SALARY_MAX_ANNUAL_USD,
    USD_CONVERSION_RATES,
)
from ml.utils import (
    log_pipeline_summary,
    month_boundaries,
    months_back,
    pct_change,
    safe_float,
    safe_int,
    timed,
    utcnow,
)

logger = logging.getLogger(__name__)


# ============================================================================
# DATA LOADING
# ============================================================================

def _load_jobs_for_period(
    db,
    period_start: datetime,
    period_end: datetime,
    country: Optional[str] = None,
) -> list[dict]:
    """
    Loads a lightweight projection of jobs for a given time period.

    We use raw SQL here for two reasons:
        1. We need unnest(skills) which has no SQLAlchemy ORM equivalent
        2. Loading full ORM Job objects for salary/skill aggregation
           would pull in description blobs — wasteful for pure analytics

    Returns a list of minimal dicts: {id, skills, salary_min, salary_max,
    salary_currency, is_remote, country}. No description text.

    This raw SQL is justified by aggregation performance requirements
    (as acknowledged in the architecture requirements). All other database
    operations still go through crud.py.
    """
    from sqlalchemy import text

    conditions = [
        "posted_at >= :period_start",
        "posted_at < :period_end",
        "is_skills_extracted = true",
        "skills IS NOT NULL",
        "array_length(skills, 1) > 0",
    ]
    params: dict = {
        "period_start": period_start,
        "period_end":   period_end,
    }

    if country:
        conditions.append("country = :country")
        params["country"] = country

    where = " AND ".join(conditions)

    sql = text(f"""
        SELECT
            id,
            skills,
            salary_min,
            salary_max,
            salary_currency,
            is_remote,
            country
        FROM jobs
        WHERE {where}
    """)

    rows = db.execute(sql, params).fetchall()
    return [dict(row._mapping) for row in rows]


# ============================================================================
# AGGREGATION — PURE COMPUTATION (no DB side-effects)
# ============================================================================

def _count_skills(jobs: list[dict]) -> Counter:
    """
    Counts how many jobs mention each skill.
    One job mentioning a skill = count of 1, regardless of how many times
    the skill appears within that job's skills array.
    """
    counter: Counter = Counter()
    for job in jobs:
        skills = job.get("skills") or []
        for skill in set(skills):  # set() = deduplicate within one job
            counter[skill] += 1
    return counter


def _compute_cooccurrence(
    jobs: list[dict],
    target_skill: str,
    min_cooccurrence: int = 2,
) -> dict[str, float]:
    """
    Computes co-occurrence coefficients for skills that appear
    alongside `target_skill`.

    METHOD: Jaccard-like coefficient
        coeff = jobs_with_both / jobs_with_target
        Ranges 0.0–1.0. 1.0 = always appears together. 0.0 = never.

    Args:
        jobs:             All jobs in the period
        target_skill:     The skill we are computing co-occurrence for
        min_cooccurrence: Minimum times a pair must appear to be included
                          (filters noise from rare co-occurrences)

    Returns:
        Dict of {other_skill: coefficient}, sorted by coefficient desc,
        top 10 only (JSONB column size management).
    """
    target_jobs = [j for j in jobs if target_skill in (j.get("skills") or [])]
    if not target_jobs:
        return {}

    co_counter: Counter = Counter()
    for job in target_jobs:
        for skill in (job.get("skills") or []):
            if skill != target_skill:
                co_counter[skill] += 1

    total_target = len(target_jobs)
    result = {
        skill: round(count / total_target, 3)
        for skill, count in co_counter.items()
        if count >= min_cooccurrence
    }

    # Top 10 by coefficient, then alphabetical for determinism
    return dict(
        sorted(result.items(), key=lambda x: (-x[1], x[0]))[:10]
    )


def _aggregate_salary(
    jobs: list[dict],
    target_skill: str,
) -> tuple[Optional[int], Optional[int], str]:
    """
    Computes average salary_min and salary_max for jobs with a given skill.

    Handles:
        - Missing salary (NULL values) → excluded from aggregation
        - Zero salary (undisclosed from API) → excluded
        - Outlier filtering (below SALARY_MIN or above SALARY_MAX)
        - Multi-currency: converts to USD for aggregation, stores in USD

    Returns:
        (avg_salary_min_usd, avg_salary_max_usd, "USD")
    """
    mins, maxes = [], []

    for job in jobs:
        if target_skill not in (job.get("skills") or []):
            continue

        currency = (job.get("salary_currency") or "USD").upper()
        rate = USD_CONVERSION_RATES.get(currency, 0)
        if rate == 0:
            continue  # Unknown currency — skip

        raw_min = safe_int(job.get("salary_min"), 0)
        raw_max = safe_int(job.get("salary_max"), 0)

        if raw_min > 0:
            usd_min = int(raw_min * rate)
            if SALARY_MIN_ANNUAL_USD <= usd_min <= SALARY_MAX_ANNUAL_USD:
                mins.append(usd_min)

        if raw_max > 0:
            usd_max = int(raw_max * rate)
            if SALARY_MIN_ANNUAL_USD <= usd_max <= SALARY_MAX_ANNUAL_USD:
                maxes.append(usd_max)

    avg_min = int(sum(mins) / len(mins)) if mins else None
    avg_max = int(sum(maxes) / len(maxes)) if maxes else None

    return avg_min, avg_max, "USD"


def _classify_trend_direction(
    change_pct: Optional[float],
    job_count: int,
    prev_job_count: int,
) -> tuple[TrendDirection, float]:
    """
    Determines trend direction and confidence score.

    CLASSIFICATION RULES:
        - No previous period data    → NEW     (confidence = 0.5)
        - change_pct ≥ +10%          → RISING  (confidence scales with magnitude)
        - change_pct ≤ -10%          → DECLINING
        - -10% < change_pct < +10%   → STABLE

    CONFIDENCE SCORING:
        Confidence reflects how reliable the direction signal is.
        A skill going from 2 to 3 jobs (+50%) is very noisy.
        A skill going from 200 to 300 (+50%) is a strong signal.
        We scale confidence by log(job_count) / log(max_meaningful_count).
    """
    import math

    if prev_job_count == 0 or change_pct is None:
        return TrendDirection.NEW, 0.5

    # Volume-based confidence: more jobs = more confidence in direction
    # log10(10) = 1.0, log10(100) = 2.0, log10(1000) = 3.0
    # We cap at log10(500) = 2.7 as the "maximum confidence" baseline
    volume_confidence = min(1.0, math.log10(max(job_count, 1)) / math.log10(500))

    if change_pct >= TREND_RISING_THRESHOLD:
        # Higher magnitude rise = higher confidence (up to 0.95)
        magnitude_bonus = min(0.3, change_pct / 200)
        confidence = round(min(0.95, volume_confidence + magnitude_bonus), 3)
        return TrendDirection.RISING, confidence

    if change_pct <= TREND_DECLINING_THRESHOLD:
        magnitude_bonus = min(0.3, abs(change_pct) / 200)
        confidence = round(min(0.95, volume_confidence + magnitude_bonus), 3)
        return TrendDirection.DECLINING, confidence

    # STABLE — confidence is higher the closer to 0% change
    stability_bonus = max(0, 0.2 - abs(change_pct) / 100)
    confidence = round(min(0.90, volume_confidence + stability_bonus), 3)
    return TrendDirection.STABLE, confidence


def _compute_period_trends(
    current_jobs:  list[dict],
    previous_jobs: list[dict],
    period_start:  datetime,
    period_end:    datetime,
    granularity:   str,
    country:       Optional[str],
    min_job_count: int = MIN_JOBS_FOR_TREND,
) -> list[dict]:
    """
    Produces a list of TrendData dicts for all qualifying skills in a period.

    ISOLATION PRINCIPLE:
        This function performs ONLY computation — no database access.
        The caller (run_trend_analysis) handles DB sessions and persistence.
        This makes the computation testable without a database.

    Args:
        current_jobs:  Jobs posted in the period being analysed
        previous_jobs: Jobs posted in the equivalent prior period
        period_start:  Inclusive start of current period
        period_end:    Exclusive end of current period
        granularity:   "monthly", "weekly", "daily"
        country:       ISO 2 country code, or None for global
        min_job_count: Skills with fewer jobs are excluded

    Returns:
        List of dicts matching the SkillTrend column set expected by
        crud.upsert_skill_trend().
    """
    current_counts  = _count_skills(current_jobs)
    previous_counts = _count_skills(previous_jobs)
    now = utcnow()

    results: list[dict] = []

    for skill, count in current_counts.items():
        if count < min_job_count:
            continue

        prev_count  = previous_counts.get(skill, 0)
        change      = count - prev_count
        change_pct  = pct_change(count, prev_count)

        direction, confidence = _classify_trend_direction(
            change_pct, count, prev_count
        )

        co_occurring = _compute_cooccurrence(current_jobs, skill)
        avg_min, avg_max, currency = _aggregate_salary(current_jobs, skill)

        # Collect a sample of up to 10 job IDs for audit trail
        sample_ids = [
            j["id"] for j in current_jobs
            if skill in (j.get("skills") or [])
        ][:10]

        # Look up skill category from constants
        from ml.constants import SKILL_CATEGORIES
        skill_category = SKILL_CATEGORIES.get(skill)

        results.append({
            "skill_name":                  skill,
            "skill_category":              skill_category,
            "granularity":                 granularity,
            "period_start":                period_start,
            "period_end":                  period_end,
            "country":                     country,
            "city":                        None,       # City-level is a future phase
            "job_count":                   count,
            "job_count_change":            change,
            "job_count_change_pct":        change_pct,
            "avg_salary_min":              avg_min,
            "avg_salary_max":              avg_max,
            "salary_currency":             currency,
            "co_occurring_skills":         co_occurring,
            "trend_direction":             direction,
            "trend_confidence":            confidence,
            "predicted_next_period_count": None,  # Forecasting is a future phase
            "sample_job_ids":              sample_ids,
            "computed_at":                 now,
        })

    logger.debug(
        "Period %s → %s | country=%s | skills computed: %d",
        period_start.date(), period_end.date(), country or "GLOBAL", len(results),
    )
    return results


# ============================================================================
# ORCHESTRATION
# ============================================================================

@timed
def run_trend_analysis(
    months_back_count: int = 3,
    granularity: str = "monthly",
    top_countries: Optional[list[str]] = None,
    min_job_count: int = MIN_JOBS_FOR_TREND,
) -> dict[str, int]:
    """
    Runs the full trend computation and persists results.

    INCREMENTAL DESIGN:
        Each month is independent — if computation for month N fails,
        months 1 through N-1 are already committed to the database.
        Re-running is idempotent: upsert_skill_trend uses ON CONFLICT DO UPDATE.

    Args:
        months_back_count: How many months of history to analyse
        granularity:       Aggregation level ("monthly")
        top_countries:     Country codes to run per-country analysis for.
                           None = global only. Pass ["US", "IN", "GB"] for country breakdown.
        min_job_count:     Minimum jobs per skill to produce a trend row

    Returns:
        Stats dict for scheduler reporting.
    """
    stats = {
        "months_computed": 0,
        "trend_rows_upserted": 0,
        "trend_rows_failed": 0,
    }

    period_list = months_back(months_back_count)
    scopes: list[Optional[str]] = [None]  # None = global
    if top_countries:
        scopes.extend(top_countries)

    logger.info(
        "Trend analysis: %d months × %d scopes (global + %d countries)",
        len(period_list), len(scopes), len(top_countries or []),
    )

    for scope_country in scopes:
        scope_label = scope_country or "GLOBAL"

        for i, (year, month) in enumerate(period_list):
            curr_start, curr_end = month_boundaries(year, month)

            # Previous period boundaries
            if month == 1:
                prev_year, prev_month = year - 1, 12
            else:
                prev_year, prev_month = year, month - 1
            prev_start, prev_end = month_boundaries(prev_year, prev_month)

            logger.info(
                "Computing: %d-%02d | scope=%s",
                year, month, scope_label,
            )

            try:
                with get_db_session() as db:
                    current_jobs  = _load_jobs_for_period(db, curr_start, curr_end, scope_country)
                    previous_jobs = _load_jobs_for_period(db, prev_start, prev_end, scope_country)

                    if not current_jobs:
                        logger.info("No jobs for %d-%02d scope=%s — skipping", year, month, scope_label)
                        continue

                    trend_rows = _compute_period_trends(
                        current_jobs=current_jobs,
                        previous_jobs=previous_jobs,
                        period_start=curr_start,
                        period_end=curr_end,
                        granularity=granularity,
                        country=scope_country,
                        min_job_count=min_job_count,
                    )

                    for trend_data in trend_rows:
                        result = crud.upsert_skill_trend(db, trend_data)
                        if result:
                            stats["trend_rows_upserted"] += 1
                        else:
                            stats["trend_rows_failed"] += 1

                stats["months_computed"] += 1

            except Exception as exc:
                logger.error(
                    "Trend computation failed for %d-%02d scope=%s: %s",
                    year, month, scope_label, exc,
                )
                # Continue to next period — do not abort the whole run

    log_pipeline_summary("trend_analysis", stats)
    return stats


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="Skill trend analysis pipeline")
    parser.add_argument("--months-back",  type=int, default=3)
    parser.add_argument("--countries",    nargs="*", default=None,
                        help="Country codes for per-country analysis. e.g. US IN GB")
    parser.add_argument("--min-job-count", type=int, default=MIN_JOBS_FOR_TREND)
    args = parser.parse_args()

    result = run_trend_analysis(
        months_back_count=args.months_back,
        top_countries=args.countries,
        min_job_count=args.min_job_count,
    )
    print("\n── Trend Analysis Results ──")
    for k, v in result.items():
        print(f"  {k}: {v}")
