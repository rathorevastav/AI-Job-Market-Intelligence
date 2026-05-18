"""
ml/salary_analyzer.py

Salary aggregation and analysis pipeline.

Answers questions like:
    - What is the median salary for Python engineers globally?
    - How do salaries compare between remote and on-site roles?
    - Which country pays the most for Kubernetes skills?
    - What is the 25th/75th percentile for Data Engineers?

DESIGN DECISIONS:
    - All computations happen in Python on data pulled from PostgreSQL.
      For the current data volume (thousands of jobs), this is appropriate.
      At millions of rows, move aggregations to PostgreSQL window functions.
    - Currency normalization converts all values to USD using static rates.
      In production, replace with a live exchange rate API.
    - Results are returned as plain Python dicts — the scheduler or API
      can decide what to do with them (persist, cache, return directly).
    - No direct database writes in this module (unlike trend_analyzer which
      writes to skill_trends). Salary data is embedded in skill_trends rows
      and also available on-demand from the raw jobs table.

HOW TO RUN:
    python -m ml.salary_analyzer
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from ml.constants import (
    SALARY_MAX_ANNUAL_USD,
    SALARY_MIN_ANNUAL_USD,
    SUPPORTED_CURRENCIES,
    USD_CONVERSION_RATES,
)
from ml.utils import log_pipeline_summary, safe_float, safe_int, timed, utcnow

logger = logging.getLogger(__name__)


# ============================================================================
# DATA LOADING
# ============================================================================

def _load_salary_data(
    db,
    country: Optional[str] = None,
    posted_after: Optional[object] = None,
) -> list[dict]:
    """
    Loads jobs with disclosed salary information.

    Only loads the fields needed for salary analysis — no description blobs.
    Filters out jobs with salary_min=0 or salary_max=0 (undisclosed).
    """
    from sqlalchemy import text

    conditions = [
        "salary_min IS NOT NULL",
        "salary_max IS NOT NULL",
        "salary_min > 0",
        "salary_max > 0",
        "salary_currency IS NOT NULL",
        "is_skills_extracted = true",
    ]
    params: dict = {}

    if country:
        conditions.append("country = :country")
        params["country"] = country

    if posted_after:
        conditions.append("posted_at >= :posted_after")
        params["posted_after"] = posted_after

    where = " AND ".join(conditions)

    sql = text(f"""
        SELECT
            id,
            title,
            company_name,
            skills,
            salary_min,
            salary_max,
            salary_currency,
            is_remote,
            country,
            experience_level,
            posted_at
        FROM jobs
        WHERE {where}
        ORDER BY posted_at DESC
    """)

    rows = db.execute(sql, params).fetchall()
    return [dict(row._mapping) for row in rows]


# ============================================================================
# NORMALIZATION
# ============================================================================

def _to_usd(amount: int, currency: str) -> Optional[float]:
    """
    Converts a salary amount to USD using static conversion rates.

    Returns None if the currency is unsupported or the rate is unknown.
    """
    currency = (currency or "USD").upper()
    if currency not in SUPPORTED_CURRENCIES:
        return None
    rate = USD_CONVERSION_RATES.get(currency, 0)
    if rate == 0:
        return None
    return amount * rate


def _is_valid_salary(salary_usd: float) -> bool:
    """Returns True if the salary is within our reasonable annual range."""
    return SALARY_MIN_ANNUAL_USD <= salary_usd <= SALARY_MAX_ANNUAL_USD


# ============================================================================
# STATISTICAL FUNCTIONS
# ============================================================================

def _percentile(values: list[float], pct: float) -> Optional[float]:
    """
    Computes the p-th percentile of a sorted list.

    Args:
        values: Sorted list of numeric values
        pct:    Percentile as 0–100 (e.g. 50 for median)

    Returns None if the list is empty.
    """
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    rank = (pct / 100) * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    fraction = rank - lower
    return round(sorted_vals[lower] * (1 - fraction) + sorted_vals[upper] * fraction, 0)


def _basic_stats(values: list[float]) -> dict:
    """
    Returns count, mean, median, p25, p75, min, max for a list of values.
    All monetary values are in USD.
    """
    if not values:
        return {
            "count": 0, "mean": None, "median": None,
            "p25": None, "p75": None, "min": None, "max": None,
        }
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mean = round(sum(sorted_vals) / n, 0)
    return {
        "count":  n,
        "mean":   mean,
        "median": _percentile(sorted_vals, 50),
        "p25":    _percentile(sorted_vals, 25),
        "p75":    _percentile(sorted_vals, 75),
        "min":    sorted_vals[0],
        "max":    sorted_vals[-1],
    }


# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================

def compute_salary_by_skill(
    jobs: list[dict],
    min_sample_size: int = 5,
) -> list[dict]:
    """
    Computes salary statistics for each skill.

    Returns:
        List of dicts sorted by median salary (USD) descending:
        [{skill, count, mean_usd, median_usd, p25_usd, p75_usd}, ...]

    Only skills with at least `min_sample_size` salary disclosures are included.
    Below that threshold, sample variance is too high to be meaningful.
    """
    skill_salaries: dict[str, list[float]] = {}

    for job in jobs:
        currency = (job.get("salary_currency") or "USD").upper()
        mid_salary_raw = (
            safe_int(job.get("salary_min"), 0) +
            safe_int(job.get("salary_max"), 0)
        ) / 2

        if mid_salary_raw <= 0:
            continue

        mid_usd = _to_usd(int(mid_salary_raw), currency)
        if mid_usd is None or not _is_valid_salary(mid_usd):
            continue

        for skill in (job.get("skills") or []):
            if skill not in skill_salaries:
                skill_salaries[skill] = []
            skill_salaries[skill].append(mid_usd)

    results = []
    for skill, salaries in skill_salaries.items():
        if len(salaries) < min_sample_size:
            continue
        stats = _basic_stats(salaries)
        results.append({
            "skill":       skill,
            "sample_size": stats["count"],
            "mean_usd":    stats["mean"],
            "median_usd":  stats["median"],
            "p25_usd":     stats["p25"],
            "p75_usd":     stats["p75"],
            "min_usd":     stats["min"],
            "max_usd":     stats["max"],
        })

    return sorted(results, key=lambda x: (x["median_usd"] or 0), reverse=True)


def compute_salary_by_country(
    jobs: list[dict],
    min_sample_size: int = 5,
) -> list[dict]:
    """
    Computes median salary statistics per country (all skills combined).

    Useful for dashboard card: "Average remote salary by country".
    """
    country_salaries: dict[str, list[float]] = {}

    for job in jobs:
        country = job.get("country") or "UNKNOWN"
        currency = (job.get("salary_currency") or "USD").upper()

        mid_raw = (
            safe_int(job.get("salary_min"), 0) +
            safe_int(job.get("salary_max"), 0)
        ) / 2
        if mid_raw <= 0:
            continue

        mid_usd = _to_usd(int(mid_raw), currency)
        if mid_usd is None or not _is_valid_salary(mid_usd):
            continue

        if country not in country_salaries:
            country_salaries[country] = []
        country_salaries[country].append(mid_usd)

    results = []
    for country, salaries in country_salaries.items():
        if len(salaries) < min_sample_size:
            continue
        stats = _basic_stats(salaries)
        results.append({
            "country":     country,
            "sample_size": stats["count"],
            "median_usd":  stats["median"],
            "mean_usd":    stats["mean"],
            "p25_usd":     stats["p25"],
            "p75_usd":     stats["p75"],
        })

    return sorted(results, key=lambda x: (x["median_usd"] or 0), reverse=True)


def compute_remote_vs_onsite_salary(jobs: list[dict]) -> dict:
    """
    Compares median salaries between remote and on-site roles.

    Returns:
        {
            "remote":  {count, median_usd, mean_usd, p25_usd, p75_usd},
            "on_site": {count, median_usd, ...},
            "premium_usd":  difference in median (remote - on_site),
            "premium_pct":  percentage premium for remote
        }
    """
    remote_salaries: list[float] = []
    onsite_salaries: list[float] = []

    for job in jobs:
        currency = (job.get("salary_currency") or "USD").upper()
        mid_raw = (
            safe_int(job.get("salary_min"), 0) +
            safe_int(job.get("salary_max"), 0)
        ) / 2
        if mid_raw <= 0:
            continue

        mid_usd = _to_usd(int(mid_raw), currency)
        if mid_usd is None or not _is_valid_salary(mid_usd):
            continue

        is_remote = job.get("is_remote") or False
        if is_remote:
            remote_salaries.append(mid_usd)
        else:
            onsite_salaries.append(mid_usd)

    remote_stats = _basic_stats(remote_salaries)
    onsite_stats = _basic_stats(onsite_salaries)

    remote_median = remote_stats["median"] or 0
    onsite_median = onsite_stats["median"] or 0

    premium_usd = round(remote_median - onsite_median, 0) if onsite_median else None
    premium_pct = (
        round((remote_median - onsite_median) / onsite_median * 100, 1)
        if onsite_median else None
    )

    return {
        "remote":       remote_stats,
        "on_site":      onsite_stats,
        "premium_usd":  premium_usd,
        "premium_pct":  premium_pct,
    }


def compute_salary_by_experience(jobs: list[dict]) -> list[dict]:
    """
    Computes median salary per experience level.

    Useful for chart: "How salary grows from entry to senior".
    """
    from collections import defaultdict
    exp_salaries: dict[str, list[float]] = defaultdict(list)

    for job in jobs:
        level = job.get("experience_level")
        if not level:
            continue

        currency = (job.get("salary_currency") or "USD").upper()
        mid_raw = (
            safe_int(job.get("salary_min"), 0) +
            safe_int(job.get("salary_max"), 0)
        ) / 2
        if mid_raw <= 0:
            continue

        mid_usd = _to_usd(int(mid_raw), currency)
        if mid_usd is None or not _is_valid_salary(mid_usd):
            continue

        exp_salaries[level].append(mid_usd)

    # Canonical order for chart display
    level_order = ["internship", "entry", "mid", "senior", "lead", "principal", "executive"]

    results = []
    for level in level_order:
        salaries = exp_salaries.get(level, [])
        if not salaries:
            continue
        stats = _basic_stats(salaries)
        results.append({
            "experience_level": level,
            "sample_size": stats["count"],
            "median_usd":  stats["median"],
            "mean_usd":    stats["mean"],
            "p25_usd":     stats["p25"],
            "p75_usd":     stats["p75"],
        })

    return results


# ============================================================================
# ORCHESTRATION
# ============================================================================

@timed
def run_salary_analysis(
    country: Optional[str] = None,
    posted_after=None,
    min_sample_size: int = 5,
) -> dict:
    """
    Runs all salary analyses and returns results in a single dict.

    This function does NOT persist to the database — it returns
    a structured report. The scheduler can:
        - Log the summary
        - Cache the result in Redis (future phase)
        - Embed key figures into SkillTrend rows (already handled by trend_analyzer)

    Returns:
        {
            "generated_at": datetime,
            "scope": {"country": ..., "posted_after": ...},
            "by_skill": [...],
            "by_country": [...],
            "remote_vs_onsite": {...},
            "by_experience": [...],
        }
    """
    from database.connection import get_db_session

    with get_db_session() as db:
        jobs = _load_salary_data(db, country=country, posted_after=posted_after)

    logger.info("Salary analysis: %d jobs with disclosed salary", len(jobs))

    if not jobs:
        logger.warning("No salary data available — check that jobs have salary_min/max populated")
        return {"generated_at": utcnow(), "scope": {}, "by_skill": [], "by_country": [], "remote_vs_onsite": {}, "by_experience": []}

    by_skill     = compute_salary_by_skill(jobs, min_sample_size=min_sample_size)
    by_country   = compute_salary_by_country(jobs, min_sample_size=min_sample_size)
    remote_comp  = compute_remote_vs_onsite_salary(jobs)
    by_experience = compute_salary_by_experience(jobs)

    report = {
        "generated_at":    utcnow(),
        "scope":           {"country": country, "posted_after": str(posted_after) if posted_after else None},
        "total_jobs_with_salary": len(jobs),
        "by_skill":        by_skill[:20],    # top 20 by median salary
        "by_country":      by_country,
        "remote_vs_onsite": remote_comp,
        "by_experience":   by_experience,
    }

    stats = {
        "jobs_analysed":    len(jobs),
        "skills_ranked":    len(by_skill),
        "countries_ranked": len(by_country),
    }
    log_pipeline_summary("salary_analysis", stats)
    return report


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        stream=sys.stdout,
    )

    import json
    report = run_salary_analysis()
    print("\n── Salary Analysis Report ──")
    print(f"  Jobs with salary: {report['total_jobs_with_salary']}")
    print(f"\n  Top 5 highest-paying skills (median USD):")
    for row in report["by_skill"][:5]:
        print(f"    {row['skill']:<25} ${row['median_usd']:>8,.0f}  (n={row['sample_size']})")
    print(f"\n  Remote vs On-Site:")
    rv = report["remote_vs_onsite"]
    if rv.get("remote") and rv["remote"].get("median_usd"):
        print(f"    Remote median:  ${rv['remote']['median_usd']:>8,.0f}")
    if rv.get("on_site") and rv["on_site"].get("median_usd"):
        print(f"    On-site median: ${rv['on_site']['median_usd']:>8,.0f}")
    if rv.get("premium_pct"):
        print(f"    Remote premium: {rv['premium_pct']:+.1f}%")
