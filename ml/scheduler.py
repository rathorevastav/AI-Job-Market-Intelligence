"""
ml/scheduler.py

Pipeline orchestrator for the ML analytics layer.

PIPELINE ORDER:
    1. Scraper          (optional — can run independently)
    2. Skill Extraction — processes all pending jobs
    3. Trend Analysis   — computes monthly skill demand trends
    4. Salary Analysis  — aggregates salary statistics

FAILURE ISOLATION:
    Each stage runs independently. A failure in stage N does NOT prevent
    stages N+1 from running. Failed stages are logged and reported in the
    final summary. This prevents one broken scraper run from blocking
    all trend data from being updated.

HOW TO RUN:
    # Full pipeline:
    python -m ml.scheduler

    # Skip scraping (just run analytics on existing data):
    python -m ml.scheduler --skip-scraper

    # Run skill extraction only:
    python -m ml.scheduler --only-skills

    # Trend analysis for last 6 months with country breakdown:
    python -m ml.scheduler --only-trends --months-back 6 --countries US IN GB
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ml.utils import log_pipeline_summary, timed, utcnow

logger = logging.getLogger(__name__)


# ============================================================================
# STAGE RESULT TRACKING
# ============================================================================

@dataclass
class StageResult:
    """Records the outcome of one pipeline stage."""
    stage:      str
    status:     str           # "success" | "failed" | "skipped"
    started_at: Optional[datetime] = None
    ended_at:   Optional[datetime] = None
    stats:      dict = field(default_factory=dict)
    error:      Optional[str] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds(), 2)
        return None


@dataclass
class PipelineRun:
    """Accumulates results across all stages of one pipeline execution."""
    started_at: datetime = field(default_factory=utcnow)
    stages: list[StageResult] = field(default_factory=list)

    def add(self, result: StageResult) -> None:
        self.stages.append(result)
        status_icon = "✓" if result.status == "success" else ("⚠" if result.status == "skipped" else "✗")
        logger.info(
            "[PIPELINE] %s %s | duration=%.1fs | %s",
            status_icon,
            result.stage.upper(),
            result.duration_seconds or 0,
            result.stats,
        )

    def summary(self) -> dict:
        total_duration = (utcnow() - self.started_at).total_seconds()
        return {
            "total_duration_seconds": round(total_duration, 2),
            "stages_run":     len(self.stages),
            "stages_success": sum(1 for s in self.stages if s.status == "success"),
            "stages_failed":  sum(1 for s in self.stages if s.status == "failed"),
            "stages_skipped": sum(1 for s in self.stages if s.status == "skipped"),
            "stage_details":  [
                {
                    "stage":    s.stage,
                    "status":   s.status,
                    "duration": s.duration_seconds,
                    "stats":    s.stats,
                    "error":    s.error,
                }
                for s in self.stages
            ],
        }


# ============================================================================
# STAGE RUNNERS
# ============================================================================

def _run_stage(name: str, func, **kwargs) -> StageResult:
    """
    Wraps a pipeline stage function with timing, error catching, and logging.

    If the stage function raises any exception:
        - The exception is logged with a full traceback
        - A failed StageResult is returned
        - Execution continues to the next stage (failure isolation)

    This means the pipeline never crashes hard — it always completes
    and produces a full summary of what succeeded and what failed.
    """
    result = StageResult(stage=name, status="running", started_at=utcnow())
    logger.info("━━━ Starting stage: %s ━━━", name.upper())

    try:
        stats = func(**kwargs)
        result.status = "success"
        result.stats = stats or {}
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        result.stats = {}
        logger.error(
            "Stage '%s' FAILED: %s\n%s",
            name, exc, traceback.format_exc(),
        )

    result.ended_at = utcnow()
    return result


def _stage_scraper(max_jobs: int = 200) -> dict:
    """Runs the RemoteOK API scraper."""
    from scraper.playwright_scraper import scrape_remoteok
    return scrape_remoteok(max_jobs=max_jobs)


def _stage_skill_extraction(
    batch_size: int = 50,
    max_batches: Optional[int] = None,
    min_confidence: float = 0.5,
) -> dict:
    """Runs NLP skill extraction on all unprocessed jobs."""
    from ml.skill_extractor import run_skill_extraction
    return run_skill_extraction(
        batch_size=batch_size,
        max_batches=max_batches,
        min_confidence=min_confidence,
    )


def _stage_trend_analysis(
    months_back_count: int = 3,
    top_countries: Optional[list[str]] = None,
    min_job_count: int = 3,
) -> dict:
    """Runs monthly trend computation and upserts SkillTrend rows."""
    from ml.trend_analyzer import run_trend_analysis
    return run_trend_analysis(
        months_back_count=months_back_count,
        top_countries=top_countries,
        min_job_count=min_job_count,
    )


def _stage_salary_analysis() -> dict:
    """Runs salary aggregation and returns a summary report."""
    from ml.salary_analyzer import run_salary_analysis
    report = run_salary_analysis()
    # Return a flat stats dict for the pipeline summary
    return {
        "jobs_analysed":    report.get("total_jobs_with_salary", 0),
        "skills_ranked":    len(report.get("by_skill", [])),
        "countries_ranked": len(report.get("by_country", [])),
    }


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

@timed
def run_pipeline(
    skip_scraper:     bool = False,
    skip_skills:      bool = False,
    skip_trends:      bool = False,
    skip_salary:      bool = False,
    scraper_max_jobs: int = 200,
    skill_batch_size: int = 50,
    skill_max_batches: Optional[int] = None,
    months_back:      int = 3,
    top_countries:    Optional[list[str]] = None,
    min_job_count:    int = 3,
    min_confidence:   float = 0.5,
) -> dict:
    """
    Runs the complete analytics pipeline with failure isolation.

    RETRY SAFETY:
        The pipeline is fully idempotent:
        - Scraper uses ON CONFLICT DO NOTHING — duplicates are skipped
        - Skill extraction only processes is_skills_extracted=False jobs
        - Trend analysis uses ON CONFLICT DO UPDATE — safe to re-run
        - Salary analysis is read-only — no DB writes

        You can run this pipeline multiple times safely. Only new data
        is processed. Already-processed data is updated with fresh values.

    Returns:
        Full pipeline summary dict for logging and monitoring.
    """
    run = PipelineRun()
    logger.info("=" * 60)
    logger.info("  AI Job Market Intelligence Platform — ML Pipeline")
    logger.info("  Started at: %s", run.started_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    logger.info("=" * 60)

    # ── Stage 1: Scraper ─────────────────────────────────────────────────
    if skip_scraper:
        run.add(StageResult(stage="scraper", status="skipped", stats={"reason": "skip_scraper=True"}))
    else:
        result = _run_stage("scraper", _stage_scraper, max_jobs=scraper_max_jobs)
        run.add(result)

    # ── Stage 2: Skill Extraction ─────────────────────────────────────────
    if skip_skills:
        run.add(StageResult(stage="skill_extraction", status="skipped"))
    else:
        result = _run_stage(
            "skill_extraction",
            _stage_skill_extraction,
            batch_size=skill_batch_size,
            max_batches=skill_max_batches,
            min_confidence=min_confidence,
        )
        run.add(result)

    # ── Stage 3: Trend Analysis ───────────────────────────────────────────
    if skip_trends:
        run.add(StageResult(stage="trend_analysis", status="skipped"))
    else:
        result = _run_stage(
            "trend_analysis",
            _stage_trend_analysis,
            months_back_count=months_back,
            top_countries=top_countries,
            min_job_count=min_job_count,
        )
        run.add(result)

    # ── Stage 4: Salary Analysis ──────────────────────────────────────────
    if skip_salary:
        run.add(StageResult(stage="salary_analysis", status="skipped"))
    else:
        result = _run_stage("salary_analysis", _stage_salary_analysis)
        run.add(result)

    # ── Final Summary ─────────────────────────────────────────────────────
    summary = run.summary()
    logger.info("=" * 60)
    logger.info("  Pipeline complete")
    logger.info("  Duration:        %.1fs", summary["total_duration_seconds"])
    logger.info("  Stages success:  %d", summary["stages_success"])
    logger.info("  Stages failed:   %d", summary["stages_failed"])
    logger.info("  Stages skipped:  %d", summary["stages_skipped"])
    logger.info("=" * 60)

    if summary["stages_failed"] > 0:
        logger.warning(
            "Pipeline completed with %d failed stage(s). Check logs above.",
            summary["stages_failed"],
        )

    return summary


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Job Market ML Pipeline Orchestrator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Skip flags
    parser.add_argument("--skip-scraper",  action="store_true", help="Skip scraper stage")
    parser.add_argument("--skip-skills",   action="store_true", help="Skip skill extraction stage")
    parser.add_argument("--skip-trends",   action="store_true", help="Skip trend analysis stage")
    parser.add_argument("--skip-salary",   action="store_true", help="Skip salary analysis stage")

    # Only flags (shorthand for running one stage)
    parser.add_argument("--only-scraper", action="store_true")
    parser.add_argument("--only-skills",  action="store_true")
    parser.add_argument("--only-trends",  action="store_true")
    parser.add_argument("--only-salary",  action="store_true")

    # Stage parameters
    parser.add_argument("--scraper-max-jobs",    type=int, default=200)
    parser.add_argument("--skill-batch-size",    type=int, default=50)
    parser.add_argument("--skill-max-batches",   type=int, default=None)
    parser.add_argument("--min-confidence",      type=float, default=0.5)
    parser.add_argument("--months-back",         type=int, default=3)
    parser.add_argument("--countries", nargs="*", default=None,
                        help="Country codes for per-country trend analysis. e.g. US IN GB")
    parser.add_argument("--min-job-count",       type=int, default=3)

    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    args = _parse_args()

    # Handle --only-X flags (run nothing except the specified stage)
    if args.only_scraper:
        args.skip_skills = args.skip_trends = args.skip_salary = True
    elif args.only_skills:
        args.skip_scraper = args.skip_trends = args.skip_salary = True
    elif args.only_trends:
        args.skip_scraper = args.skip_skills = args.skip_salary = True
    elif args.only_salary:
        args.skip_scraper = args.skip_skills = args.skip_trends = True

    summary = run_pipeline(
        skip_scraper=     args.skip_scraper,
        skip_skills=      args.skip_skills,
        skip_trends=      args.skip_trends,
        skip_salary=      args.skip_salary,
        scraper_max_jobs= args.scraper_max_jobs,
        skill_batch_size= args.skill_batch_size,
        skill_max_batches=args.skill_max_batches,
        months_back=      args.months_back,
        top_countries=    args.countries,
        min_job_count=    args.min_job_count,
        min_confidence=   args.min_confidence,
    )

    # Exit with non-zero code if any stage failed (for CI/CD and alerting)
    if summary["stages_failed"] > 0:
        sys.exit(1)
