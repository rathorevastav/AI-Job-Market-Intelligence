"""
dashboard/utils.py

Utility functions for the Streamlit dashboard.

FIXES IN THIS VERSION:
    - Removed icon= kwarg from all st.info / st.warning / st.success calls.
      icon= was added in Streamlit 1.19. On earlier versions it raises TypeError
      which Streamlit catches silently, blanking the entire calling page.
    - All st.* calls are now version-safe (compatible with Streamlit 1.12+).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import streamlit as st

# ============================================================================
# FORMATTING
# ============================================================================

def fmt_number(n: Optional[int | float], fallback: str = "—") -> str:
    if n is None:
        return fallback
    return f"{int(n):,}"


def fmt_salary(amount: Optional[int], currency: str = "USD", fallback: str = "Undisclosed") -> str:
    if not amount or amount == 0:
        return fallback
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "CA$", "AUD": "A$"}
    sym = symbols.get(currency.upper(), currency + " ")
    if amount >= 1_000_000:
        return f"{sym}{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sym}{amount / 1_000:.0f}k"
    return f"{sym}{amount:,}"


def fmt_salary_range(
    salary_min: Optional[int],
    salary_max: Optional[int],
    currency: str = "USD",
) -> str:
    if not salary_min and not salary_max:
        return "Undisclosed"
    if salary_min and salary_max:
        return f"{fmt_salary(salary_min, currency)} – {fmt_salary(salary_max, currency)}"
    if salary_min:
        return f"{fmt_salary(salary_min, currency)}+"
    return f"Up to {fmt_salary(salary_max, currency)}"


def fmt_datetime(dt_str: Optional[str], fallback: str = "Unknown") -> str:
    if not dt_str:
        return fallback
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except (ValueError, AttributeError):
        return fallback


def fmt_relative_time(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff_s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if diff_s < 60:        return "Just now"
        if diff_s < 3600:      return f"{diff_s // 60}m ago"
        if diff_s < 86400:     return f"{diff_s // 3600}h ago"
        return f"{diff_s // 86400}d ago"
    except (ValueError, AttributeError):
        return "Unknown"


def fmt_skills_list(skills: Optional[list[str]], max_show: int = 6) -> str:
    if not skills:
        return "—"
    shown = skills[:max_show]
    result = ", ".join(shown)
    if len(skills) > max_show:
        result += f" +{len(skills) - max_show} more"
    return result


def fmt_status_badge(status: str) -> str:
    return {
        "completed": "🟢 Completed",
        "failed":    "🔴 Failed",
        "running":   "🟡 Running",
        "partial":   "🟠 Partial",
    }.get((status or "").lower(), f"⚪ {status.title()}")


def fmt_location(job: dict) -> str:
    """Public helper — formats city/country into a readable location string."""
    parts = [p for p in [job.get("city"), job.get("country")] if p]
    return ", ".join(parts) if parts else "Remote"


# Keep the private alias so any existing internal call sites still work
_fmt_location = fmt_location


# ============================================================================
# DATAFRAME HELPERS
# ============================================================================

def jobs_to_dataframe(jobs: list[dict[str, Any]]) -> pd.DataFrame:
    if not jobs:
        return pd.DataFrame()
    rows = []
    for job in jobs:
        rows.append({
            "ID":       job.get("id"),
            "Title":    job.get("title", "—"),
            "Company":  job.get("company_name") or "—",
            "Location": _fmt_location(job),
            "Remote":   "✓" if job.get("is_remote") else "—",
            "Level":    (job.get("experience_level") or "").title(),
            "Skills":   fmt_skills_list(job.get("skills") or []),
            "Salary":   fmt_salary_range(
                            job.get("salary_min"),
                            job.get("salary_max"),
                            job.get("salary_currency") or "USD",
                        ),
            "Platform": (job.get("source_platform") or "").title(),
            "Posted":   fmt_relative_time(job.get("posted_at")),
            "URL":      job.get("source_url", ""),
        })
    return pd.DataFrame(rows)


def skills_to_dataframe(skills: list[dict[str, Any]]) -> pd.DataFrame:
    if not skills:
        return pd.DataFrame(columns=["Skill", "Job Count"])
    df = pd.DataFrame(skills)
    df = df.rename(columns={"skill": "Skill", "job_count": "Job Count"})
    return df


def scrape_runs_to_dataframe(runs: list[dict[str, Any]]) -> pd.DataFrame:
    if not runs:
        return pd.DataFrame()
    rows = []
    for run in runs:
        rows.append({
            "Platform":   (run.get("platform") or "").title(),
            "Status":     fmt_status_badge(run.get("status") or ""),
            "Started":    fmt_relative_time(run.get("started_at")),
            "Inserted":   fmt_number(run.get("jobs_inserted"), "0"),
            "Found":      fmt_number(run.get("jobs_found"), "0"),
            "Duplicates": fmt_number(run.get("jobs_skipped_duplicate"), "0"),
            "Errors":     fmt_number(run.get("jobs_failed_parsing"), "0"),
        })
    return pd.DataFrame(rows)


def build_salary_by_skill(jobs_with_salary: list[dict]) -> list[dict]:
    """Computes median salary per skill from a list of job dicts."""
    skill_salaries: dict[str, list[float]] = defaultdict(list)
    for job in jobs_with_salary:
        lo = job.get("salary_min") or 0
        hi = job.get("salary_max") or 0
        mid = (lo + hi) / 2 if lo and hi else (lo or hi)
        if mid > 0:
            for skill in (job.get("skills") or []):
                skill_salaries[skill].append(mid)

    result = []
    for skill, vals in skill_salaries.items():
        if len(vals) < 2:
            continue
        sorted_v = sorted(vals)
        n = len(sorted_v)
        result.append({
            "skill":       skill,
            "median_usd":  sorted_v[n // 2],
            "sample_size": n,
            "p25_usd":     sorted_v[n // 4] if n >= 4 else None,
            "p75_usd":     sorted_v[3 * n // 4] if n >= 4 else None,
        })
    return sorted(result, key=lambda x: x["median_usd"], reverse=True)


# ============================================================================
# PAGINATION
# ============================================================================

def render_pagination(total: int, page: int, page_size: int, key_prefix: str = "pg") -> int:
    """Renders prev/next controls. Returns updated page number."""
    pages = max(1, math.ceil(total / page_size) if page_size > 0 else 1)

    col_prev, col_info, col_next = st.columns([1, 3, 1])
    with col_prev:
        # FIX: Do NOT assign page inside button click — Streamlit re-runs on next cycle.
        # Just show the button; the calling page reads st.session_state directly.
        st.button("← Prev", key=f"{key_prefix}_prev", disabled=(page <= 1))
    with col_info:
        start = (page - 1) * page_size + 1
        end   = min(page * page_size, total)
        st.markdown(
            f"<p style='text-align:center;color:#888;margin-top:6px;font-size:13px'>"
            f"Showing {start:,}–{end:,} of {total:,} | Page {page}/{pages}</p>",
            unsafe_allow_html=True,
        )
    with col_next:
        st.button("Next →", key=f"{key_prefix}_next", disabled=(page >= pages))

    # Compute new page from button state
    new_page = page
    if st.session_state.get(f"{key_prefix}_prev"):
        new_page = max(1, page - 1)
    if st.session_state.get(f"{key_prefix}_next"):
        new_page = min(pages, page + 1)
    return new_page


# ============================================================================
# UI HELPERS — all icon= kwargs removed for Streamlit version safety
# ============================================================================

def metric_card(label: str, value: str, delta: Optional[str] = None) -> None:
    st.metric(label=label, value=value, delta=delta)


def show_api_error(message: str = "Could not connect to the API.") -> None:
    # NOTE: icon= kwarg removed — causes TypeError on Streamlit < 1.19
    st.warning(f"⚠️ {message} — Ensure FastAPI is running on localhost:8000")


def show_empty_state(message: str = "No data available.") -> None:
    # NOTE: icon= kwarg removed — causes TypeError on Streamlit < 1.19
    st.info(f"ℹ️ {message}")


def skill_tags(skills: Optional[list[str]], max_show: int = 10) -> None:
    if not skills:
        st.caption("No skills listed")
        return
    displayed = skills[:max_show]
    badges = " ".join(
        f'<span style="background:#1a3a5c;color:#60a5fa;'
        f'padding:2px 10px;border-radius:99px;'
        f'font-size:11px;margin:2px;display:inline-block;'
        f'border:1px solid #2563eb">{skill}</span>'
        for skill in displayed
    )
    if len(skills) > max_show:
        badges += f' <span style="color:#6b7280;font-size:11px">+{len(skills)-max_show} more</span>'
    st.markdown(badges, unsafe_allow_html=True)


def section_header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<h2 style="margin-bottom:2px;color:#f1f5f9">{title}</h2>',
        unsafe_allow_html=True,
    )
    if subtitle:
        st.markdown(
            f'<p style="color:#94a3b8;margin-top:0;margin-bottom:12px;font-size:14px">{subtitle}</p>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<hr style="border:none;border-top:1px solid #1e293b;margin:8px 0 20px 0">',
        unsafe_allow_html=True,
    )


def card_html(content: str) -> None:
    """Renders an HTML string inside a styled dark card."""
    st.markdown(
        f'<div style="background:#0f172a;border:1px solid #1e293b;'
        f'border-radius:12px;padding:20px;margin-bottom:12px">'
        f'{content}</div>',
        unsafe_allow_html=True,
    )
