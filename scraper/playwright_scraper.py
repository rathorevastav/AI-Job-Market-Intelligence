"""
scraper/playwright_scraper.py

Collects job listings from RemoteOK via their public JSON API.
Integrates with the database CRUD layer for persistence and audit logging.

WHY API INSTEAD OF DOM SCRAPING:
    RemoteOK exposes a documented public JSON API at https://remoteok.com/api
    that returns structured data directly. The Playwright approach failed because:
      1. RemoteOK detects headless Chrome via navigator.webdriver and serves a
         bot-detection stub — a skeleton table with empty placeholder rows.
      2. The stub causes tr.job[data-id] to return 0 real rows regardless of
         selector strategy, because no real content is ever loaded.
    The API bypasses the browser entirely. No DOM, no detection surface,
    no stealth patches needed. Structured JSON is consumed directly.

HOW TO RUN LOCALLY:
    pip install requests

    python -m scraper.playwright_scraper

    # With options:
    python -m scraper.playwright_scraper --max-jobs 50

EXPECTED OUTPUT STRUCTURE (one cleaned job dict):
    {
        "source_url":       "https://remoteok.com/remote-jobs/1131572",
        "source_platform":  "remoteok",
        "external_id":      "1131572",
        "title":            "Senior AI System Software Developer",
        "company_name":     "Wealthsimple Technologies",
        "location_raw":     "",
        "city":             None,
        "country":          None,
        "is_remote":        True,
        "is_hybrid":        False,
        "skills":           ["system", "developer", "software", "react", "golang"],
        "description":      "Wealthsimple's mission is to help everyone...",
        "job_type":         "full_time",
        "experience_level": "senior",
        "salary_min":       151200,
        "salary_max":       189000,
        "salary_currency":  "USD",
        "salary_period":    "yearly",
        "salary_raw":       None,
        "posted_at":        datetime(2026, 5, 13, 0, 1, 58, tzinfo=timezone.utc),
        "scraped_at":       datetime(..., tzinfo=timezone.utc),
    }

API TERMS (RemoteOK requirement):
    Link back to RemoteOK with a follow link (no nofollow) and credit
    "Remote OK" as the source wherever job data is displayed.
    See: https://remoteok.com/api
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config import settings
from database.connection import get_db_session
from database.crud import (
    bulk_insert_jobs,
    complete_scrape_run,
    create_scrape_run,
    fail_scrape_run,
)
from database.models import JobType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORM        = "remoteok"
API_URL         = "https://remoteok.com/api"
BASE_URL        = "https://remoteok.com"

REQUEST_TIMEOUT = 30        # seconds — connect + read
MAX_RETRIES     = 3
RETRY_BACKOFF   = [3, 8, 15]  # seconds between attempts

# Tags RemoteOK uses for platform filtering — not real tech skills.
# Confirmed from live API response (May 2025).
_NOISE_TAGS: frozenset[str] = frozenset({
    "digital nomad", "non tech", "non-tech", "adult", "exec",
    "finance", "legal", "medical", "hr", "operations", "operational",
    "strategy", "sales", "marketing", "recruiting", "recruiter",
    "management", "content", "design", "growth", "leader", "non-technical",
})

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; "
        "https://github.com/your-org/ai-job-market-platform)"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================================
# SECTION 1 — API TRANSPORT
# ============================================================================

def _fetch_api(url: str = API_URL) -> list[dict]:
    """
    Fetches the RemoteOK JSON API with retry logic.

    RESPONSE STRUCTURE (confirmed from live API, May 2025):
        Array where:
          index 0   → metadata object  { "legal": "...", "last_updated": N }
          index 1+  → job objects

        Metadata object is identified by having a "legal" key.
        Every job object has a numeric string "id" key.

    Returns:
        List of raw job dicts (metadata object already removed).

    Raises:
        requests.RequestException if all retries are exhausted.
        requests.HTTPError for non-retryable HTTP errors.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Fetching %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning("Rate limited — waiting %ds before retry", retry_after)
                time.sleep(retry_after)
                continue

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, list):
                raise ValueError(
                    f"Expected JSON array, got {type(data).__name__}. "
                    "API structure may have changed."
                )

            # Keep only objects that have "id" — the job identifier.
            # The metadata object at index 0 has "legal" but no "id".
            jobs = [
                item for item in data
                if isinstance(item, dict) and "id" in item
            ]

            logger.info(
                "API returned %d total items, %d job objects extracted",
                len(data), len(jobs),
            )
            return jobs

        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Connection error (attempt %d/%d) — retrying in %ds: %s",
                attempt, MAX_RETRIES, wait, exc,
            )
            time.sleep(wait)

        except requests.exceptions.Timeout as exc:
            last_exc = exc
            wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Timeout (attempt %d/%d) — retrying in %ds",
                attempt, MAX_RETRIES, wait,
            )
            time.sleep(wait)

        except requests.exceptions.HTTPError as exc:
            # Non-retryable: 400, 403, 404, 500
            logger.error("HTTP error fetching API: %s", exc)
            raise

        except (ValueError, KeyError) as exc:
            logger.error("API response parsing error: %s", exc)
            raise

    logger.error("All %d API fetch attempts exhausted", MAX_RETRIES)
    raise requests.RequestException(
        f"Failed to fetch {url} after {MAX_RETRIES} attempts"
    ) from last_exc


# ============================================================================
# SECTION 2 — FIELD-LEVEL PARSERS
# ============================================================================

def _strip_html(text: str) -> str:
    """
    Removes HTML markup and collapses whitespace to plain text.

    RemoteOK descriptions often contain raw HTML (p, ul, li, br, strong).
    Plain text is stored for NLP skill extraction by the ML pipeline.
    """
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</li>|</div>|</h\d>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_spam_footer(text: str) -> str:
    """
    Removes the anti-spam verification footer RemoteOK appends to every description.

    Observed pattern in live API data:
        'Please mention the word **PERFECTION** and tag RMTguMjA4...'

    This is an applicant-tracking artefact and adds noise to NLP pipelines.
    """
    return re.sub(
        r"Please mention the word.*$",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()


def _parse_posted_at(date_str: str, epoch: Optional[int] = None) -> Optional[datetime]:
    """
    Converts API date fields to a UTC-aware datetime.

    Prefers the ISO 8601 date string because it carries explicit timezone.
    Falls back to the epoch integer if date_str is absent or malformed.

    Live API format: "2026-05-13T00:01:58+00:00"
    Epoch example:   1778630518
    """
    if date_str:
        try:
            dt = datetime.fromisoformat(str(date_str))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            logger.debug("Could not parse date string: %s", date_str)

    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            logger.debug("Could not parse epoch: %s", epoch)

    return None


def _parse_salary(
    salary_min_raw: object,
    salary_max_raw: object,
) -> tuple[Optional[int], Optional[int], str]:
    """
    Normalises RemoteOK API salary fields.

    CRITICAL BEHAVIOUR:
        RemoteOK uses integer 0 to mean "not disclosed" — NOT actual zero
        salary. We convert 0 → None so the DB stores NULL (undisclosed).
        The API always returns annual USD amounts when disclosed.

    Live examples:
        salary_min=151200, salary_max=189000  → (151200, 189000, "USD")
        salary_min=0,      salary_max=0       → (None, None, "USD")
    """
    try:
        mn = int(salary_min_raw or 0)
        mx = int(salary_max_raw or 0)
    except (TypeError, ValueError):
        return None, None, "USD"

    return (
        mn if mn > 0 else None,
        mx if mx > 0 else None,
        "USD",
    )


def _parse_location(location_raw: str) -> tuple[Optional[str], Optional[str], bool]:
    """
    Parses RemoteOK's free-text location field into structured fields.

    All jobs on RemoteOK are remote-friendly by definition, but the
    location field encodes the geographic scope of remote eligibility.

    Live examples mapped to output:
        ""                       → (None, None,  True)
        "Remote"                 → (None, None,  True)
        "Remoto"                 → (None, None,  True)   ← Portuguese
        "Remote - United States" → (None, "US",  True)
        "Remote, USA"            → (None, "US",  True)
        "Remote - US"            → (None, "US",  True)
        "United States"          → (None, "US",  True)
        "USA Remote"             → (None, "US",  True)
        "Remote - US"            → (None, "US",  True)
        "San Francisco"          → ("San Francisco", None, False)
        "Amsterdam"              → ("Amsterdam",     None, False)
        "Hyderabad"              → ("Hyderabad",     None, False)
        "Spain"                  → (None, "ES",  False)

    Returns:
        (city, country_iso2, is_remote)
    """
    raw = (location_raw or "").strip()
    if not raw:
        return None, None, True

    lower = raw.lower()

    _REMOTE_KEYWORDS = ("remote", "remoto", "anywhere", "worldwide", "global", "anywhere")
    is_explicitly_remote = any(kw in lower for kw in _REMOTE_KEYWORDS)

    _COUNTRY_MAP: dict[str, str] = {
        "united states": "US", "usa": "US", " us ": "US",
        "u.s.": "US", "us remote": "US", "remote - us": "US",
        "united kingdom": "GB", "uk": "GB", "britain": "GB",
        "canada": "CA", "germany": "DE", "deutschland": "DE",
        "france": "FR", "australia": "AU", "india": "IN",
        "spain": "ES", "españa": "ES", "brazil": "BR", "brasil": "BR",
        "netherlands": "NL", "portugal": "PT", "poland": "PL",
        "italy": "IT", "mexico": "MX", "singapore": "SG",
        "ukraine": "UA", "romania": "RO", "argentina": "AR",
        "colombia": "CO", "chile": "CL", "israel": "IL",
        "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
    }

    country: Optional[str] = None
    for name, code in _COUNTRY_MAP.items():
        if name in lower:
            country = code
            break

    if is_explicitly_remote:
        # Remote + country scope (e.g. "Remote - United States")
        return None, country, True

    if country:
        # Country-only text (e.g. "United States") — treat as remote with country scope
        return None, country, True

    # City-level or ambiguous — store as city, not classified as remote
    city = raw if len(raw) <= 100 else None
    return city, None, False


def _normalize_skills(tags: object) -> list[str]:
    """
    Filters and normalises the API tags list into a clean skills list.

    RemoteOK tags mix tech skills with category/lifestyle tags.
    Noise tags are filtered via _NOISE_TAGS. Remaining tags are
    lowercased, deduplicated, and stored as the skills array.

    The ML pipeline's spaCy extractor supplements this with skill
    extraction from the full description text.
    """
    if not tags or not isinstance(tags, list):
        return []

    seen: set[str] = set()
    result: list[str] = []

    for tag in tags:
        if not isinstance(tag, str):
            continue
        normalised = tag.lower().strip()
        if not normalised or len(normalised) < 2:
            continue
        if normalised in _NOISE_TAGS:
            continue
        if normalised in seen:
            continue
        seen.add(normalised)
        result.append(normalised)

    return result


def _infer_experience_level(title: str, tags: list) -> Optional[str]:
    """Infers seniority from job title and tags. Returns ExperienceLevel string."""
    tags_list = tags if isinstance(tags, list) else []
    combined = f"{title} {' '.join(str(t) for t in tags_list)}".lower()

    if any(w in combined for w in ("intern", "internship", "entry level", "graduate")):
        return "internship"
    if any(w in combined for w in ("junior", "jr.", " jr ", " entry ")):
        return "entry"
    if any(w in combined for w in ("principal", "distinguished", "staff engineer")):
        return "principal"
    if any(w in combined for w in ("senior", "sr.", " sr ", " lead ", "head of")):
        return "senior"
    if any(w in combined for w in ("director", "vp ", "vice president", "chief", "cto", "ceo", "founder")):
        return "executive"
    return "mid"


def _infer_job_type(title: str, tags: list) -> str:
    """Infers employment type from title and tags."""
    tags_list = tags if isinstance(tags, list) else []
    combined = f"{title} {' '.join(str(t) for t in tags_list)}".lower()

    if "contract" in combined or "contractor" in combined:
        return JobType.CONTRACT
    if "freelance" in combined:
        return JobType.FREELANCE
    if "part" in combined and "time" in combined:
        return JobType.PART_TIME
    if "intern" in combined:
        return JobType.INTERNSHIP
    return JobType.FULL_TIME


# ============================================================================
# SECTION 3 — RECORD NORMALISATION
# ============================================================================

def _normalise_job(raw: dict) -> Optional[dict]:
    """
    Transforms one raw API job object into a database-ready dict.

    API FIELD → DATABASE COLUMN MAPPING:
    ┌───────────────────┬──────────────────────┬───────────────────────────────┐
    │ API field         │ DB column            │ Transform                     │
    ├───────────────────┼──────────────────────┼───────────────────────────────┤
    │ id                │ external_id          │ str()                         │
    │ url               │ source_url           │ direct                        │
    │ position          │ title                │ strip()                       │
    │ company           │ company_name         │ strip(), None if empty        │
    │ tags              │ skills               │ _normalize_skills()           │
    │ location          │ location_raw         │ direct                        │
    │                   │ city                 │ _parse_location()             │
    │                   │ country              │ _parse_location()             │
    │                   │ is_remote            │ _parse_location()             │
    │ description       │ description          │ _strip_html() + _strip_spam() │
    │ salary_min        │ salary_min           │ 0 → None                      │
    │ salary_max        │ salary_max           │ 0 → None                      │
    │ (always USD)      │ salary_currency      │ "USD"                         │
    │ date              │ posted_at            │ _parse_posted_at()            │
    │ epoch             │ posted_at (fallback) │ _parse_posted_at()            │
    │ apply_url         │ raw_metadata         │ stored in JSONB               │
    │ company_logo      │ raw_metadata         │ stored in JSONB               │
    │ slug              │ raw_metadata         │ stored in JSONB               │
    │ (inferred)        │ experience_level     │ _infer_experience_level()     │
    │ (inferred)        │ job_type             │ _infer_job_type()             │
    │ (always True)     │ is_hybrid            │ False                         │
    └───────────────────┴──────────────────────┴───────────────────────────────┘

    Returns:
        Database-ready dict, or None if required fields are missing.
        Required: id, position, url — same gate as the old _clean_job().
    """
    job_id = str(raw.get("id", "")).strip()
    title  = str(raw.get("position", "")).strip()
    url    = str(raw.get("url", "")).strip()

    if not job_id or not title or not url:
        logger.debug(
            "Skipping — missing required field | id=%s title=%s url=%s",
            job_id or "MISSING",
            (title[:40] if title else "MISSING"),
            (url[:60] if url else "MISSING"),
        )
        return None

    tags_raw     = raw.get("tags") or []
    location_raw = str(raw.get("location") or "")

    city, country, is_remote         = _parse_location(location_raw)
    salary_min, salary_max, currency = _parse_salary(
        raw.get("salary_min", 0),
        raw.get("salary_max", 0),
    )

    raw_desc  = str(raw.get("description") or "")
    clean_desc = _strip_spam_footer(_strip_html(raw_desc))

    posted_at = _parse_posted_at(
        str(raw.get("date") or ""),
        raw.get("epoch"),
    )

    return {
        # ── Identity ─────────────────────────────────────────────────
        "source_url":       url,
        "source_platform":  PLATFORM,
        "external_id":      job_id,

        # ── Core ─────────────────────────────────────────────────────
        "title":            title,
        "company_name":     str(raw.get("company") or "").strip() or None,

        # ── Location ─────────────────────────────────────────────────
        "location_raw":     location_raw or None,
        "city":             city,
        "country":          country,
        "is_remote":        is_remote,
        "is_hybrid":        False,

        # ── Skills ───────────────────────────────────────────────────
        "skills":           _normalize_skills(tags_raw),

        # ── Content ──────────────────────────────────────────────────
        "description":      clean_desc or None,

        # ── Classification ───────────────────────────────────────────
        "job_type":         _infer_job_type(title, tags_raw),
        "experience_level": _infer_experience_level(title, tags_raw),

        # ── Compensation ─────────────────────────────────────────────
        "salary_min":       salary_min,
        "salary_max":       salary_max,
        "salary_currency":  currency,
        "salary_period":    "yearly" if salary_min else None,
        "salary_raw":       None,  # API gives structured values — no raw string

        # ── Timing ───────────────────────────────────────────────────
        "posted_at":        posted_at,
        "scraped_at":       datetime.now(timezone.utc),

        # ── JSONB metadata ───────────────────────────────────────────
        "raw_metadata": {
            "apply_url":    str(raw.get("apply_url") or "") or None,
            "company_logo": str(raw.get("company_logo") or "") or None,
            "slug":         str(raw.get("slug") or "") or None,
            "api_source":   API_URL,
        },
    }


# ============================================================================
# SECTION 4 — ORCHESTRATION
# ============================================================================

def scrape_remoteok(max_jobs: int = 200) -> dict:
    """
    Main entry point. Fetches RemoteOK API and persists results to PostgreSQL.

    Orchestration flow (structure identical to the original Playwright version):
        1. Create ScrapeRun audit record  → status = "running"
        2. Fetch raw job list from API
        3. Normalise each raw object into a database-ready dict
        4. Bulk-insert via bulk_insert_jobs() (ON CONFLICT DO NOTHING dedup)
        5. Mark ScrapeRun as completed with final counts
        6. On any exception → mark ScrapeRun as failed + re-raise

    Args:
        max_jobs: Cap on jobs processed per run. The API returns 150–300 jobs.
                  Capping guards against unexpected response size changes.

    Returns:
        Stats dict with the same keys as ScrapeRun fields.
    """
    config_snapshot = {
        "platform": PLATFORM,
        "api_url":  API_URL,
        "max_jobs": max_jobs,
    }

    stats = {
        "jobs_found":             0,
        "jobs_inserted":          0,
        "jobs_skipped_duplicate": 0,
        "jobs_failed_parsing":    0,
        "pages_scraped":          1,  # one HTTP request = one "page"
    }

    with get_db_session() as db:
        run = create_scrape_run(db, platform=PLATFORM, config_snapshot=config_snapshot)
        if not run:
            logger.error("Could not create ScrapeRun — aborting")
            return stats

        run_id = run.id
        logger.info(
            "Scrape started | run_id=%d platform=%s max_jobs=%d",
            run_id, PLATFORM, max_jobs,
        )

        try:
            # ── Fetch ────────────────────────────────────────────────
            raw_jobs = _fetch_api(API_URL)
            stats["jobs_found"] = len(raw_jobs)

            if len(raw_jobs) > max_jobs:
                logger.info(
                    "Capping at %d (API returned %d)", max_jobs, len(raw_jobs)
                )
                raw_jobs = raw_jobs[:max_jobs]

            # ── Normalise ────────────────────────────────────────────
            normalised: list[dict] = []
            for raw in raw_jobs:
                record = _normalise_job(raw)
                if record:
                    normalised.append(record)
                else:
                    stats["jobs_failed_parsing"] += 1

            logger.info(
                "Normalisation: %d valid / %d rejected out of %d",
                len(normalised), stats["jobs_failed_parsing"], len(raw_jobs),
            )

            # ── Persist ──────────────────────────────────────────────
            if normalised:
                insert_stats = bulk_insert_jobs(db, normalised)
                stats["jobs_inserted"]          = insert_stats["inserted"]
                stats["jobs_skipped_duplicate"] = insert_stats["skipped_duplicate"]
                stats["jobs_failed_parsing"]   += insert_stats["failed"]

            complete_scrape_run(
                db,
                run_id,
                pages_scraped=          stats["pages_scraped"],
                jobs_found=             stats["jobs_found"],
                jobs_inserted=          stats["jobs_inserted"],
                jobs_skipped_duplicate= stats["jobs_skipped_duplicate"],
                jobs_failed_parsing=    stats["jobs_failed_parsing"],
            )

            logger.info(
                "Scrape done | run_id=%d inserted=%d duplicates=%d failed=%d",
                run_id,
                stats["jobs_inserted"],
                stats["jobs_skipped_duplicate"],
                stats["jobs_failed_parsing"],
            )

        except Exception as exc:
            logger.exception("Unhandled exception | run_id=%d", run_id)
            fail_scrape_run(
                db,
                run_id,
                error_message=str(exc),
                pages_scraped=stats["pages_scraped"],
                jobs_inserted=stats["jobs_inserted"],
            )

    return stats


# ============================================================================
# SECTION 5 — CLI ENTRY POINT
# ============================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RemoteOK API job scraper",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=200,
        help="Maximum jobs to process per run",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    args = _parse_args()
    result = scrape_remoteok(max_jobs=args.max_jobs)

    print("\n" + "─" * 50)
    print(f"  Jobs found:    {result['jobs_found']}")
    print(f"  Jobs inserted: {result['jobs_inserted']}")
    print(f"  Duplicates:    {result['jobs_skipped_duplicate']}")
    print(f"  Parse errors:  {result['jobs_failed_parsing']}")
    print("─" * 50 + "\n")
