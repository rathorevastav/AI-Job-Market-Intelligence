"""
dashboard/api_client.py

Single module responsible for all HTTP communication with the FastAPI backend.

DESIGN RULES:
    - No Streamlit imports in this file — it is UI-agnostic
    - Every public function returns typed Python objects (dicts/lists)
    - All HTTP errors are caught here and converted to empty safe defaults
    - Retry logic lives here, not in the page files
    - Endpoint URLs are centralized in one place (API_ENDPOINTS)

This separation means:
    - The API client can be unit-tested without Streamlit
    - Page files focus entirely on rendering, never on HTTP mechanics
    - Changing the API base URL requires editing one constant
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

API_BASE_URL = "http://localhost:8000/api/v1"
REQUEST_TIMEOUT = 10          # seconds
MAX_RETRIES     = 3
BACKOFF_FACTOR  = 0.5         # wait 0.5s, 1s, 2s between retries


# ============================================================================
# SESSION WITH RETRY
# ============================================================================

def _build_session() -> requests.Session:
    """
    Creates a requests Session with automatic retry on connection errors.

    WHY A SESSION?
        A Session reuses the underlying TCP connection across multiple
        requests (connection pooling). For a dashboard making 5–10 API
        calls per page render, this reduces latency significantly compared
        to opening a new connection every time.

    RETRY STRATEGY:
        Retries on connection errors and 5xx server errors only.
        Does NOT retry on 4xx (client errors like 404, 422) — those
        are deterministic failures that retrying won't fix.
    """
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://",  adapter)
    session.mount("https://", adapter)
    return session


_session = _build_session()


# ============================================================================
# CORE REQUEST HELPER
# ============================================================================

def _get(endpoint: str, params: Optional[dict] = None) -> Optional[dict | list]:
    """
    Makes a GET request to the API and returns parsed JSON.

    Returns None on any failure — callers must handle None gracefully.
    All errors are logged but never re-raised, keeping the dashboard
    functional even when the API is temporarily unavailable.
    """
    params = {k: v for k, v in (params or {}).items() if v is not None}
    url = f"{API_BASE_URL}{endpoint}"

    try:
        response = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("API unreachable: %s — is the FastAPI server running?", url)
        return None
    except requests.exceptions.Timeout:
        logger.error("API timeout after %ds: %s", REQUEST_TIMEOUT, url)
        return None
    except requests.exceptions.HTTPError as exc:
        logger.error("API HTTP error %s: %s", exc.response.status_code, url)
        return None
    except Exception as exc:
        logger.error("Unexpected API error on %s: %s", url, exc)
        return None


# ============================================================================
# TYPED API FUNCTIONS
# ============================================================================

def get_health() -> dict[str, Any]:
    """
    GET /stats/health

    Returns:
        {
            "api_status":         "ok",
            "database_connected": True,
            "total_jobs":         1482,
            "latest_scrape_run":  {...} | None,
            "timestamp":          "2026-05-13T...",
        }
        Returns a safe default dict if the API is unreachable.
    """
    data = _get("/stats/health")
    if data is None:
        return {
            "api_status":         "unreachable",
            "database_connected": False,
            "total_jobs":         0,
            "latest_scrape_run":  None,
            "timestamp":          None,
        }
    return data


def get_top_skills(
    limit: int = 20,
    country: Optional[str] = None,
    posted_after: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    GET /stats/top-skills

    Returns:
        List of {"skill": "python", "job_count": 142} dicts,
        sorted by job_count descending.
        Returns [] if the API is unavailable or returns no data.
    """
    params = {"limit": limit}
    if country:
        params["country"] = country
    if posted_after:
        params["posted_after"] = posted_after

    data = _get("/stats/top-skills", params=params)
    if not data:
        return []
    return data.get("skills", [])


def get_scrape_runs(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    GET /stats/scrape-runs

    Returns list of scrape run audit records, newest first.
    """
    params: dict = {"limit": limit}
    if platform:
        params["platform"] = platform
    if status:
        params["status"] = status

    data = _get("/stats/scrape-runs", params=params)
    if not data:
        return []
    return data.get("runs", [])


def get_jobs(
    skill: Optional[str]           = None,
    country: Optional[str]         = None,
    city: Optional[str]            = None,
    company_name: Optional[str]    = None,
    experience_level: Optional[str] = None,
    job_type: Optional[str]        = None,
    is_remote: Optional[bool]      = None,
    source_platform: Optional[str] = None,
    search_query: Optional[str]    = None,
    posted_after: Optional[str]    = None,
    posted_before: Optional[str]   = None,
    page: int                      = 1,
    page_size: int                 = 20,
    order_by: str                  = "posted_at",
    descending: bool               = True,
) -> dict[str, Any]:
    """
    GET /jobs with all filter and pagination parameters.

    Returns:
        {
            "items":     [JobSummary, ...],
            "total":     1482,
            "page":      1,
            "page_size": 20,
            "pages":     75,
        }
        Returns empty paginated structure on failure.
    """
    params: dict = {
        "page":       page,
        "page_size":  page_size,
        "order_by":   order_by,
        "descending": str(descending).lower(),
    }
    # Only add optional params that have a value
    if skill:             params["skill"]             = skill
    if country:           params["country"]           = country
    if city:              params["city"]              = city
    if company_name:      params["company_name"]      = company_name
    if experience_level:  params["experience_level"]  = experience_level
    if job_type:          params["job_type"]          = job_type
    if is_remote is not None:
                          params["is_remote"]         = str(is_remote).lower()
    if source_platform:   params["source_platform"]   = source_platform
    if search_query:      params["search_query"]      = search_query
    if posted_after:      params["posted_after"]      = posted_after
    if posted_before:     params["posted_before"]     = posted_before

    data = _get("/jobs", params=params)
    if not data:
        return {"items": [], "total": 0, "page": 1, "page_size": page_size, "pages": 0}
    return data

def get_jobs_with_salary(
    country=None,
    source_platform=None,
    page=1,
    page_size=100,
    posted_after=None,
):
    """
    Fetch only jobs that contain salary information.
    """
    params = {
        "country": country,
        "source_platform": source_platform,
        "posted_after": posted_after,
        "page": page,
        "page_size": page_size,
    }

    # Remove None values
    params = {k: v for k, v in params.items() if v is not None}

    return _get("/jobs/with-salary", params=params)

def get_job_detail(job_id: int) -> Optional[dict[str, Any]]:
    """
    GET /jobs/{job_id}

    Returns the full job record including description, or None if not found.
    """
    return _get(f"/jobs/{job_id}")


def check_api_reachable() -> bool:
    """
    Quick connectivity check. Used by the sidebar health indicator.
    Returns True if the API responds within REQUEST_TIMEOUT seconds.
    """
    try:
        response = _session.get(
            f"{API_BASE_URL}/stats/health",
            timeout=3,
        )
        return response.status_code == 200
    except Exception:
        return False
