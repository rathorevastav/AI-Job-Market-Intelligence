"""
scraper/greenhouse_scraper.py

Collects job listings from company Greenhouse job boards via the public Board API.
Integrates with the database CRUD layer for persistence and audit logging.

GREENHOUSE BOARD API:
    Public endpoint — no authentication required.
    GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

    Response structure:
        {
            "jobs": [
                {
                    "id":             44444,
                    "title":          "Product Engineer",
                    "updated_at":     "2013-07-02T19:39:23Z",
                    "requisition_id": "50",
                    "location":       {"name": "San Francisco, CA"},
                    "content":        "<p>Job description HTML...</p>",
                    "absolute_url":   "https://boards.greenhouse.io/company/jobs/44444",
                    "departments":    [{"id": 1, "name": "Engineering"}],
                    "offices":        [{"id": 1, "name": "San Francisco"}],
                    "metadata":       null
                },
                ...
            ],
            "meta": {"total": 42}
        }

    The ?content=true query param is required to include the job description.

HOW TO RUN LOCALLY:
    python -m scraper.greenhouse_scraper

    # With options:
    python -m scraper.greenhouse_scraper --max-jobs-per-board 50
    python -m scraper.greenhouse_scraper --companies stripe airbnb linear

EXPECTED OUTPUT STRUCTURE (one cleaned job dict):
    {
        "source_url":       "https://boards.greenhouse.io/stripe/jobs/44444",
        "source_platform":  "greenhouse",
        "external_id":      "stripe::44444",
        "title":            "Product Engineer",
        "company_name":     "Stripe",
        "location_raw":     "San Francisco, CA",
        "city":             "San Francisco",
        "country":          "US",
        "is_remote":        False,
        "is_hybrid":        False,
        "skills":           [],
        "description":      "We are looking for...",
        "job_type":         "full_time",
        "experience_level": "mid",
        "salary_min":       None,
        "salary_max":       None,
        "salary_currency":  "USD",
        "salary_period":    None,
        "salary_raw":       None,
        "posted_at":        datetime(2013, 7, 2, 19, 39, 23, tzinfo=timezone.utc),
        "scraped_at":       datetime(..., tzinfo=timezone.utc),
    }
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

PLATFORM       = "greenhouse"
BOARD_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

REQUEST_TIMEOUT = 20
MAX_RETRIES     = 3
RETRY_BACKOFF   = [3, 8, 15]

DEFAULT_COMPANIES: list[str] = [
    "airbnb",
    "canva",
    "coinbase",
    "datadog",
    "discord",
    "dropbox",
    "figma",
    "gitlab",
    "hashicorp",
    "hubspot",
    "linear",
    "mongodb",
    "notion",
    "plaid",
    "postman",
    "reddit",
    "rippling",
    "robinhood",
    "shopify",
    "singlestore",
    "snowflake",
    "sourcegraph",
    "square",
    "stripe",
    "twilio",
    "vercel",
    "webflow",
    "zapier",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; "
        "https://github.com/your-org/ai-job-market-platform)"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_REMOTE_KEYWORDS = (
    "remote", "anywhere", "worldwide", "distributed",
    "home office", "work from home", "wfh", "virtual", "globally",
)

_COUNTRY_MAP: dict[str, str] = {
    "united states": "US", "usa": "US", "u.s.": "US",
    "united kingdom": "GB", "uk": "GB", "britain": "GB",
    "canada": "CA", "germany": "DE", "deutschland": "DE",
    "france": "FR", "australia": "AU", "india": "IN",
    "spain": "ES", "brazil": "BR", "brasil": "BR",
    "netherlands": "NL", "portugal": "PT", "poland": "PL",
    "italy": "IT", "mexico": "MX", "singapore": "SG",
    "ukraine": "UA", "romania": "RO", "argentina": "AR",
    "colombia": "CO", "chile": "CL", "israel": "IL",
    "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
    "ireland": "IE", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "finland": "FI", "switzerland": "CH",
    "austria": "AT", "belgium": "BE", "czech republic": "CZ",
    "hungary": "HU", "japan": "JP", "south korea": "KR",
    "korea": "KR", "china": "CN", "taiwan": "TW",
    "hong kong": "HK", "thailand": "TH", "vietnam": "VN",
    "indonesia": "ID", "philippines": "PH", "malaysia": "MY",
    "new zealand": "NZ",
}


# ============================================================================
# SECTION 1 — API TRANSPORT
# ============================================================================

def _fetch_board(company_slug: str) -> list[dict]:
    """
    Fetches all jobs from one company's Greenhouse board with retry logic.

    404 responses are treated as a skip — the board is private or the slug
    has changed. All other HTTP errors raise after retries are exhausted.

    Returns:
        List of raw job dicts, each annotated with "_company_slug".
    """
    url = f"{BOARD_API_BASE}/{company_slug}/jobs?content=true"
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "Fetching board '%s' (attempt %d/%d)",
                company_slug, attempt, MAX_RETRIES,
            )
            response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)

            if response.status_code == 404:
                logger.warning(
                    "Board '%s' returned 404 — slug may be wrong or board is private. Skipping.",
                    company_slug,
                )
                return []

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning(
                    "Rate limited on '%s' — waiting %ds", company_slug, retry_after,
                )
                time.sleep(retry_after)
                continue

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict) or "jobs" not in data:
                raise ValueError(
                    f"Unexpected response for board '{company_slug}': "
                    f"expected dict with 'jobs' key, got {type(data).__name__}"
                )

            jobs = data["jobs"]
            if not isinstance(jobs, list):
                raise ValueError(
                    f"'jobs' field for board '{company_slug}' is not a list"
                )

            for job in jobs:
                job["_company_slug"] = company_slug

            logger.info("Board '%s': %d job(s) returned", company_slug, len(jobs))
            return jobs

        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Connection error on '%s' (attempt %d/%d) — retrying in %ds: %s",
                company_slug, attempt, MAX_RETRIES, wait, exc,
            )
            time.sleep(wait)

        except requests.exceptions.Timeout as exc:
            last_exc = exc
            wait = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Timeout on '%s' (attempt %d/%d) — retrying in %ds",
                company_slug, attempt, MAX_RETRIES, wait,
            )
            time.sleep(wait)

        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error on board '%s': %s", company_slug, exc)
            raise

        except (ValueError, KeyError) as exc:
            logger.error("Response parsing error for board '%s': %s", company_slug, exc)
            raise

    logger.error("All %d fetch attempts exhausted for board '%s'", MAX_RETRIES, company_slug)
    raise requests.RequestException(
        f"Failed to fetch board '{company_slug}' after {MAX_RETRIES} attempts"
    ) from last_exc


# ============================================================================
# SECTION 2 — FIELD-LEVEL PARSERS
# ============================================================================

def _strip_html(text: str) -> str:
    """
    Converts HTML markup to clean plain text using BeautifulSoup.

    Greenhouse job descriptions contain deeply nested HTML that regex
    stripping cannot handle reliably. BeautifulSoup parses the full
    DOM tree so every tag is removed regardless of nesting depth.

    Conversion rules:
        <br>           → newline
        <li>           → "• " bullet prefix on its own line
        <p>, <div>,
        <h1>–<h6>     → text content followed by a blank line
        <ul>, <ol>     → blank line after the list
        All other tags → text content only (no tag emitted)
        HTML entities  → decoded automatically by bs4 (html.parser)
    """
    if not text or not text.strip():
        return ""

    try:
        from bs4 import BeautifulSoup, NavigableString, Tag

        BLOCKS = {"p", "div", "section", "article",
                  "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}
        lines: list[str] = []

        def _inline(node) -> str:
            if isinstance(node, NavigableString):
                return str(node)
            if not isinstance(node, Tag):
                return ""
            if node.name == "br":
                return "\n"
            return "".join(_inline(c) for c in node.children)

        def _walk(node) -> None:
            if isinstance(node, NavigableString):
                t = str(node)
                if t.strip():
                    lines.append(t.strip())
                return
            if not isinstance(node, Tag):
                return
            n = node.name
            if n == "br":
                lines.append("")
                return
            if n == "li":
                t = _inline(node).strip()
                if t:
                    lines.append(f"• {t}")
                return
            if n in BLOCKS:
                if n in ("ul", "ol"):
                    for c in node.children:
                        _walk(c)
                    lines.append("")
                    return
                t = _inline(node).strip()
                if t:
                    lines.append(t)
                    lines.append("")
                return
            for c in node.children:
                _walk(c)

        _walk(BeautifulSoup(text, "html.parser"))

        result: list[str] = []
        prev_blank = False
        for line in lines:
            blank = line.strip() == ""
            if blank and prev_blank:
                continue
            result.append(line)
            prev_blank = blank

        return "\n".join(result).strip()

    except Exception:
        # bs4 unavailable or parse error — fall back to safe regex strip
        import re as _re
        t = _re.sub(r"<br\s*/?>", "\n", text, flags=_re.IGNORECASE)
        t = _re.sub(r"</?(li)[^>]*>", "\n• ", t, flags=_re.IGNORECASE)
        t = _re.sub(r"<[^>]+>", " ", t)
        t = _re.sub(r"[ \t]+", " ", t)
        t = _re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

def _parse_posted_at(date_str: Optional[str]) -> Optional[datetime]:
    """Converts a Greenhouse ISO 8601 datetime string to a UTC-aware datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        logger.debug("Could not parse Greenhouse date: %s", date_str)
        return None


def _parse_location(
    location_name: str,
) -> tuple[Optional[str], Optional[str], bool, bool]:
    """
    Parses a Greenhouse location.name string into structured fields.

    Common patterns:
        "San Francisco, CA"           → ("San Francisco", "US",  False, False)
        "Remote"                      → (None,            None,  True,  False)
        "Remote - US"                 → (None,            "US",  True,  False)
        "Remote or San Francisco, CA" → (None,            "US",  True,  True)
        "London, England"             → ("London",        "GB",  False, False)
        "Anywhere"                    → (None,            None,  True,  False)

    Returns:
        (city, country_iso2, is_remote, is_hybrid)
    """
    raw = (location_name or "").strip()
    if not raw:
        return None, None, False, False

    lower = raw.lower()

    is_remote = any(kw in lower for kw in _REMOTE_KEYWORDS)
    is_hybrid = is_remote and any(
        kw in lower for kw in ("or ", "and ", "/", "hybrid", "flexible")
    )

    country: Optional[str] = None
    for name, code in _COUNTRY_MAP.items():
        if name in lower:
            country = code
            break

    # Infer US from ", ST" state-abbreviation suffix when no country matched
    if not country and re.search(r",\s*[A-Z]{2}$", raw):
        country = "US"

    if is_remote:
        return None, country, True, is_hybrid

    city_match = re.match(r"^([^,]+)", raw)
    city = city_match.group(1).strip() if city_match else None
    if city and len(city) > 100:
        city = None

    return city, country, False, False


def _infer_experience_level(title: str) -> Optional[str]:
    """Infers seniority from job title. Returns ExperienceLevel string."""
    lower = title.lower()
    if any(w in lower for w in ("intern", "internship", "graduate", "entry level")):
        return "internship"
    if any(w in lower for w in ("junior", "jr.", " jr ", " entry ")):
        return "entry"
    if any(w in lower for w in ("principal", "distinguished", "staff engineer")):
        return "principal"
    if any(w in lower for w in ("senior", "sr.", " sr ", " lead ", "head of", "staff ")):
        return "senior"
    if any(w in lower for w in ("director", "vp ", "vice president", "chief", "cto", "ceo", "founder")):
        return "executive"
    return "mid"


def _infer_job_type(title: str) -> str:
    """Infers employment type from job title."""
    lower = title.lower()
    if "contract" in lower or "contractor" in lower:
        return JobType.CONTRACT
    if "freelance" in lower:
        return JobType.FREELANCE
    if "part" in lower and "time" in lower:
        return JobType.PART_TIME
    if "intern" in lower:
        return JobType.INTERNSHIP
    return JobType.FULL_TIME


def _extract_department(raw: dict) -> Optional[str]:
    """Extracts the first department name from the departments array."""
    departments = raw.get("departments") or []
    if departments and isinstance(departments, list):
        first = departments[0]
        if isinstance(first, dict):
            return str(first.get("name") or "").strip() or None
    return None


def _slug_to_company_name(slug: str) -> str:
    """
    Converts a board token slug to a human-readable company name.

    "stripe"       → "Stripe"
    "single-store" → "Single Store"
    """
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())


# ============================================================================
# SECTION 3 — RECORD NORMALISATION
# ============================================================================

def _normalise_job(raw: dict) -> Optional[dict]:
    """
    Transforms one raw Greenhouse job object into a database-ready dict.

    API FIELD → DATABASE COLUMN MAPPING:
    ┌───────────────────────┬──────────────────────┬───────────────────────────────┐
    │ API field             │ DB column            │ Transform                     │
    ├───────────────────────┼──────────────────────┼───────────────────────────────┤
    │ id                    │ external_id          │ "{slug}::{id}" composite key  │
    │ absolute_url          │ source_url           │ direct (globally unique)      │
    │ title                 │ title                │ strip()                       │
    │ _company_slug         │ company_name         │ _slug_to_company_name()       │
    │ location.name         │ location_raw         │ direct                        │
    │                       │ city                 │ _parse_location()             │
    │                       │ country              │ _parse_location()             │
    │                       │ is_remote            │ _parse_location()             │
    │                       │ is_hybrid            │ _parse_location()             │
    │ content               │ description          │ _strip_html()                 │
    │ updated_at            │ posted_at            │ _parse_posted_at()            │
    │ departments[0].name   │ company_industry     │ _extract_department()         │
    │ (not in public API)   │ salary_min/max       │ None                          │
    │ (inferred)            │ experience_level     │ _infer_experience_level()     │
    │ (inferred)            │ job_type             │ _infer_job_type()             │
    └───────────────────────┴──────────────────────┴───────────────────────────────┘

    EXTERNAL ID:
        Greenhouse job IDs are scoped per board — the same integer can exist
        on two boards. We use "{slug}::{id}" as a human-readable composite key
        and rely on absolute_url (the unique constraint column) for dedup.

    Returns:
        Database-ready dict, or None if required fields are missing.
    """
    job_id       = str(raw.get("id", "")).strip()
    title        = str(raw.get("title") or "").strip()
    absolute_url = str(raw.get("absolute_url") or "").strip()
    company_slug = str(raw.get("_company_slug") or "").strip()

    if not job_id or not title or not absolute_url:
        logger.debug(
            "Skipping — missing required field | id=%s title=%s url=%s",
            job_id or "MISSING",
            title[:40] if title else "MISSING",
            absolute_url[:60] if absolute_url else "MISSING",
        )
        return None

    location_obj  = raw.get("location") or {}
    location_name = (
        str(location_obj.get("name") or "").strip()
        if isinstance(location_obj, dict)
        else ""
    )
    city, country, is_remote, is_hybrid = _parse_location(location_name)

    raw_content = str(raw.get("content") or "")
    description = _strip_html(raw_content) or None

    posted_at = _parse_posted_at(raw.get("updated_at"))

    return {
        # ── Identity ──────────────────────────────────────────────────
        "source_url":       absolute_url,
        "source_platform":  PLATFORM,
        "external_id":      f"{company_slug}::{job_id}",

        # ── Core ──────────────────────────────────────────────────────
        "title":            title,
        "company_name":     _slug_to_company_name(company_slug) if company_slug else None,

        # ── Location ──────────────────────────────────────────────────
        "location_raw":     location_name or None,
        "city":             city,
        "country":          country,
        "is_remote":        is_remote,
        "is_hybrid":        is_hybrid,

        # ── Skills (ML pipeline populates from description later) ──────
        "skills":           [],

        # ── Content ───────────────────────────────────────────────────
        "description":      description,

        # ── Classification ────────────────────────────────────────────
        "job_type":         _infer_job_type(title),
        "experience_level": _infer_experience_level(title),

        # ── Department stored in company_industry ──────────────────────
        "company_industry": _extract_department(raw),

        # ── Compensation (not in Greenhouse public board API) ──────────
        "salary_min":       None,
        "salary_max":       None,
        "salary_currency":  "USD",
        "salary_period":    None,
        "salary_raw":       None,

        # ── Timing ────────────────────────────────────────────────────
        "posted_at":        posted_at,
        "scraped_at":       datetime.now(timezone.utc),

        # ── JSONB metadata ─────────────────────────────────────────────
        "raw_metadata": {
            "greenhouse_job_id": job_id,
            "company_slug":      company_slug,
            "requisition_id":    str(raw.get("requisition_id") or "") or None,
            "department":        _extract_department(raw),
            "offices":           [
                o.get("name") for o in (raw.get("offices") or [])
                if isinstance(o, dict) and o.get("name")
            ],
            "api_source":        f"{BOARD_API_BASE}/{company_slug}/jobs",
        },
    }


# ============================================================================
# SECTION 4 — ORCHESTRATION
# ============================================================================

def scrape_greenhouse(
    companies: Optional[list[str]] = None,
    max_jobs_per_board: int = 500,
    max_jobs: int = 5000,
    inter_board_delay: float = 1.0,
) -> dict:
    """
    Main entry point. Scrapes all configured Greenhouse boards and persists
    results to PostgreSQL.

    Orchestration flow (identical pattern to scrape_remoteok):
        1. Create ScrapeRun audit record  → status = "running"
        2. For each board:
           a. Fetch raw job list
           b. Normalise each object
           c. Accumulate into batch
        3. Bulk-insert entire batch (ON CONFLICT DO NOTHING dedup)
        4. Mark ScrapeRun as completed with final stats
        5. On unhandled exception → mark ScrapeRun as failed

    DEDUPLICATION:
        absolute_url is globally unique per Greenhouse job post.
        The unique constraint on source_url + ON CONFLICT DO NOTHING in
        bulk_insert_jobs handles re-runs without duplicates.

    Args:
        companies:           Board slugs to scrape. Defaults to DEFAULT_COMPANIES.
        max_jobs_per_board:  Per-board cap.
        max_jobs:            Hard cap across all boards combined.
        inter_board_delay:   Seconds between board requests.
    """
    boards = companies or DEFAULT_COMPANIES

    config_snapshot = {
        "platform":           PLATFORM,
        "boards":             boards,
        "max_jobs_per_board": max_jobs_per_board,
        "max_jobs":           max_jobs,
    }

    stats = {
        "jobs_found":             0,
        "jobs_inserted":          0,
        "jobs_skipped_duplicate": 0,
        "jobs_failed_parsing":    0,
        "pages_scraped":          len(boards),
    }

    with get_db_session() as db:
        run = create_scrape_run(db, platform=PLATFORM, config_snapshot=config_snapshot)
        if not run:
            logger.error("Could not create ScrapeRun — aborting")
            return stats

        run_id = run.id
        logger.info(
            "Scrape started | run_id=%d platform=%s boards=%d max_jobs=%d",
            run_id, PLATFORM, len(boards), max_jobs,
        )

        try:
            all_normalised: list[dict] = []

            for board_index, slug in enumerate(boards):
                if len(all_normalised) >= max_jobs:
                    logger.info(
                        "Reached global max_jobs=%d after %d boards — stopping",
                        max_jobs, board_index,
                    )
                    break

                if board_index > 0:
                    time.sleep(inter_board_delay)

                try:
                    raw_jobs = _fetch_board(slug)
                except Exception as exc:
                    logger.error("Board '%s' failed — skipping: %s", slug, exc)
                    stats["jobs_failed_parsing"] += 1
                    continue

                if not raw_jobs:
                    continue

                if len(raw_jobs) > max_jobs_per_board:
                    logger.info(
                        "Board '%s': capping at %d (board has %d)",
                        slug, max_jobs_per_board, len(raw_jobs),
                    )
                    raw_jobs = raw_jobs[:max_jobs_per_board]

                stats["jobs_found"] += len(raw_jobs)

                for raw in raw_jobs:
                    record = _normalise_job(raw)
                    if record:
                        all_normalised.append(record)
                    else:
                        stats["jobs_failed_parsing"] += 1

                logger.debug(
                    "Board '%s' done — %d records accumulated",
                    slug, len(all_normalised),
                )

            if len(all_normalised) > max_jobs:
                all_normalised = all_normalised[:max_jobs]

            logger.info(
                "Normalisation complete: %d valid / %d rejected out of %d found",
                len(all_normalised), stats["jobs_failed_parsing"], stats["jobs_found"],
            )

            if all_normalised:
                insert_stats = bulk_insert_jobs(db, all_normalised)
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
        description="Greenhouse job board scraper",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--companies",
        nargs="*",
        default=None,
        metavar="SLUG",
        help=(
            "Greenhouse board slugs to scrape. "
            "Example: --companies stripe airbnb linear"
        ),
    )
    parser.add_argument(
        "--max-jobs-per-board",
        type=int,
        default=500,
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=5000,
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        dest="inter_board_delay",
        help="Seconds to wait between board requests",
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
    result = scrape_greenhouse(
        companies=args.companies,
        max_jobs_per_board=args.max_jobs_per_board,
        max_jobs=args.max_jobs,
        inter_board_delay=args.inter_board_delay,
    )

    print("\n" + "─" * 50)
    print(f"  Boards scraped: {result['pages_scraped']}")
    print(f"  Jobs found:     {result['jobs_found']}")
    print(f"  Jobs inserted:  {result['jobs_inserted']}")
    print(f"  Duplicates:     {result['jobs_skipped_duplicate']}")
    print(f"  Parse errors:   {result['jobs_failed_parsing']}")
    print("─" * 50 + "\n")