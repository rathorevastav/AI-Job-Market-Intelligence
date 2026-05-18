"""
ml/utils.py

Shared utilities for the ML pipeline.
Pure functions only — no database or ML model dependencies here.
"""

from __future__ import annotations

import functools
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Generator, Iterable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ============================================================================
# TEXT CLEANING
# ============================================================================

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALPHA_RE = re.compile(r"[^a-z0-9\s\.\+\#\-\/]")
_CAMEL_CASE_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def clean_text(text: str) -> str:
    """
    Strips HTML, collapses whitespace, and lowercases.
    Preserves characters meaningful in tech skill names: . + # - /
    """
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.lower()
    text = _NON_ALPHA_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def split_camel_case(text: str) -> str:
    """
    Inserts a space before uppercase letters in camelCase words.
    'JavaScript' → 'Java Script' so 'java' and 'script' are separately tokenized.
    Applied before clean_text() in the extraction pipeline.
    """
    return _CAMEL_CASE_RE.sub(" ", text)


def tokenize(text: str) -> list[str]:
    """
    Splits cleaned text into individual tokens.
    Handles multi-word skill phrases by keeping them intact if they appear
    in the known skills set after cleaning.
    """
    return [tok for tok in text.split() if len(tok) >= 2]


def truncate(text: str, max_chars: int = 5000) -> str:
    """
    Truncates text to max_chars. The NLP pipeline processes at most
    this many characters per document to keep latency predictable.
    """
    if not text:
        return ""
    return text[:max_chars]


# ============================================================================
# BATCHING
# ============================================================================

def batched(iterable: Iterable[T], size: int) -> Generator[list[T], None, None]:
    """
    Yields successive chunks of `size` items from `iterable`.

    WHY NOT itertools.batched?
        itertools.batched is Python 3.12+. This implementation works on 3.10+
        and is explicit about behaviour when the final batch is smaller than `size`.

    Usage:
        for batch in batched(job_ids, 50):
            process(batch)  # batch is always ≤ 50 items
    """
    batch: list[T] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ============================================================================
# SAFE NUMERIC PARSING
# ============================================================================

def safe_int(value: Any, fallback: int = 0) -> int:
    """Converts value to int, returning fallback on any failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def safe_float(value: Any, fallback: float = 0.0) -> float:
    """Converts value to float, returning fallback on any failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def pct_change(current: float, previous: float) -> float | None:
    """
    Calculates percentage change from previous to current.
    Returns None if previous is zero (undefined division).
    """
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamps value to [lo, hi] range."""
    return max(lo, min(hi, value))


# ============================================================================
# TIMING DECORATOR
# ============================================================================

def timed(func):
    """
    Decorator that logs the execution time of the wrapped function.

    Usage:
        @timed
        def run_extraction():
            ...

    Logs: "run_extraction completed in 4.32s"
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger.info("%s completed in %.2fs", func.__name__, elapsed)
        return result
    return wrapper


# ============================================================================
# UTC HELPERS
# ============================================================================

def utcnow() -> datetime:
    """Returns current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def month_boundaries(year: int, month: int) -> tuple[datetime, datetime]:
    """
    Returns the (start, end) datetime boundaries for a given year/month.
    start is inclusive, end is exclusive (first moment of next month).

    Used by trend_analyzer to define period_start / period_end.
    """
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def months_back(n: int) -> list[tuple[int, int]]:
    """
    Returns a list of (year, month) tuples for the past n months,
    ordered oldest to newest. Used to drive the trend computation loop.

    months_back(3) called in May 2026 →
        [(2026, 2), (2026, 3), (2026, 4)]
    """
    now = utcnow()
    results: list[tuple[int, int]] = []
    year, month = now.year, now.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        results.append((year, month))
    return list(reversed(results))


# ============================================================================
# LOGGING HELPERS
# ============================================================================

def log_pipeline_summary(stage: str, stats: dict[str, Any]) -> None:
    """
    Logs a formatted pipeline stage summary.
    All pipeline stages call this at completion for consistent log output.
    """
    parts = " | ".join(f"{k}={v}" for k, v in stats.items())
    logger.info("[PIPELINE] %s | %s", stage.upper(), parts)
