"""
dashboard/charts.py

Reusable Plotly chart factory functions.

DESIGN RULES:
    - Every function returns a plotly.graph_objects.Figure
    - No Streamlit calls in this file — charts are UI-framework agnostic
    - Data transformation happens here, not in page files
    - All functions accept plain Python dicts/lists (API response shapes)
    - Consistent color palette and layout applied via _apply_theme()

Callers use:
    fig = bar_top_skills(skills_data)
    st.plotly_chart(fig, use_container_width=True)
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ============================================================================
# THEME — green/white SaaS palette
# ============================================================================

# Primary green gradient for sequential scales
_GREEN_SCALE = ["#d1fae5", "#6ee7b7", "#10b981", "#059669", "#065f46"]

# Categorical palette — emerald lead, complementary accents
_PALETTE = [
    "#10b981",  # emerald
    "#3b82f6",  # blue
    "#8b5cf6",  # violet
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#06b6d4",  # cyan
    "#ec4899",  # pink
    "#84cc16",  # lime
]

_FONT = dict(family="Inter, -apple-system, sans-serif", size=12, color="#374151")

_LAYOUT_DEFAULTS = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=_FONT,
    margin=dict(l=10, r=10, t=36, b=10),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02,
        xanchor="right", x=1,
        bgcolor="rgba(0,0,0,0)",
        font=dict(size=11),
    ),
)


def _apply_theme(fig: go.Figure, title: str = "") -> go.Figure:
    """Applies the light SaaS theme to any figure."""
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=14, color="#0d1f17", weight=600),
            pad=dict(b=8),
        ),
        **_LAYOUT_DEFAULTS,
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        color="#9ca3af",
        tickfont=dict(size=11),
        linecolor="#e2ece8",
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(16,185,129,0.1)",  # very subtle green tint matches palette
        gridwidth=1,
        zeroline=False,
        color="#9ca3af",
        tickfont=dict(size=11),
    )
    return fig


# ============================================================================
# SKILLS CHARTS
# ============================================================================

def bar_top_skills(
    skills: list[dict[str, Any]],
    title: str = "Top Skills by Job Count",
    max_skills: int = 20,
    orientation: str = "h",
) -> go.Figure:
    """
    Horizontal bar chart of skill frequencies.

    Args:
        skills:      List of {"skill": str, "job_count": int} from API
        title:       Chart title
        max_skills:  Cap displayed skills (avoids overcrowded chart)
        orientation: "h" for horizontal (recommended), "v" for vertical
    """
    if not skills:
        return _empty_figure("No skill data available")

    df = pd.DataFrame(skills[:max_skills])
    df = df.sort_values("job_count", ascending=(orientation == "h"))

    if orientation == "h":
        fig = px.bar(
            df, x="job_count", y="skill", orientation="h",
            color="job_count",
            color_continuous_scale=_GREEN_SCALE,
            labels={"job_count": "Job Count", "skill": ""},
        )
    else:
        fig = px.bar(
            df, x="skill", y="job_count",
            color="job_count",
            color_continuous_scale=_GREEN_SCALE,
            labels={"job_count": "Job Count", "skill": ""},
        )

    fig.update_coloraxes(showscale=False)
    fig.update_traces(
        hovertemplate="<b>%{y}</b>  ·  %{x:,} jobs<extra></extra>",
        marker_line_width=0,
        # Rounded bar ends — supported in Plotly 5.11+; silently ignored on older versions
        marker=dict(cornerradius=4),
    )
    _apply_theme(fig, title)
    return fig


def bar_skills_comparison(
    skills_a: list[dict],
    skills_b: list[dict],
    label_a: str = "Period A",
    label_b: str = "Period B",
    title: str = "Skill Demand Comparison",
) -> go.Figure:
    """
    Grouped bar chart comparing skill counts across two periods or filters.
    Useful for "this month vs last month" comparisons.
    """
    if not skills_a and not skills_b:
        return _empty_figure("No comparison data")

    df_a = pd.DataFrame(skills_a).rename(columns={"job_count": label_a})
    df_b = pd.DataFrame(skills_b).rename(columns={"job_count": label_b})

    df = df_a.merge(df_b, on="skill", how="outer").fillna(0)
    df = df.sort_values(label_a, ascending=False).head(15)

    fig = go.Figure()
    fig.add_trace(go.Bar(name=label_a, x=df["skill"], y=df[label_a], marker_color=_PALETTE[0]))
    fig.add_trace(go.Bar(name=label_b, x=df["skill"], y=df[label_b], marker_color=_PALETTE[1]))
    fig.update_layout(barmode="group")
    _apply_theme(fig, title)
    return fig


def pie_remote_vs_onsite(
    remote_count: int,
    onsite_count: int,
    title: str = "Remote vs On-site Jobs",
) -> go.Figure:
    """Donut chart comparing remote and on-site job distribution."""
    if remote_count == 0 and onsite_count == 0:
        return _empty_figure("No location data")

    labels = ["Remote", "On-site"]
    values = [remote_count, onsite_count]
    colors = ["#10b981", "#d1fae5"]

    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.58,
        marker=dict(colors=colors, line=dict(color="#ffffff", width=2)),
        textinfo="label+percent",
        textfont=dict(size=12, color="#374151"),
        hovertemplate="%{label}: %{value:,} jobs — %{percent}<extra></extra>",
    ))
    _apply_theme(fig, title)
    return fig


def pie_jobs_by_country(
    country_counts: dict[str, int],
    title: str = "Jobs by Country",
    top_n: int = 8,
) -> go.Figure:
    """Pie chart of job distribution by country."""
    if not country_counts:
        return _empty_figure("No country data")

    sorted_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)
    top = sorted_countries[:top_n]
    other_count = sum(v for _, v in sorted_countries[top_n:])
    if other_count > 0:
        top.append(("Other", other_count))

    labels = [k for k, _ in top]
    values = [v for _, v in top]

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        textinfo="label+percent",
        marker=dict(colors=_PALETTE[:len(top)]),
        hovertemplate="%{label}: %{value:,} jobs<extra></extra>",
    ))
    _apply_theme(fig, title)
    return fig


# ============================================================================
# SALARY CHARTS
# ============================================================================

def bar_salary_by_skill(
    salary_data: list[dict[str, Any]],
    title: str = "Median Annual Salary by Skill (USD)",
    top_n: int = 15,
) -> go.Figure:
    """
    Horizontal bar chart of median salary per skill.
    Error bars show 25th–75th percentile range.
    """
    if not salary_data:
        return _empty_figure("No salary data — salary disclosure is optional on most platforms")

    df = pd.DataFrame(salary_data[:top_n]).dropna(subset=["median_usd"])
    df = df.sort_values("median_usd", ascending=True)

    error_minus = (df["median_usd"] - df.get("p25_usd", df["median_usd"])).clip(lower=0)
    error_plus  = (df.get("p75_usd", df["median_usd"]) - df["median_usd"]).clip(lower=0)

    fig = go.Figure(go.Bar(
        x=df["median_usd"],
        y=df["skill"],
        orientation="h",
        marker=dict(
            color=df["median_usd"],
            colorscale=_GREEN_SCALE,
            line=dict(width=0),
        ),
        error_x=dict(
            type="data", symmetric=False,
            array=error_plus.tolist(),
            arrayminus=error_minus.tolist(),
            color="rgba(16,185,129,0.3)",
            thickness=2, width=4,
        ),
        hovertemplate="<b>%{y}</b><br>Median: $%{x:,.0f}<extra></extra>",
    ))
    fig.update_xaxes(tickprefix="$", tickformat=",")
    _apply_theme(fig, title)
    return fig


def bar_salary_by_country(
    country_data: list[dict[str, Any]],
    title: str = "Median Salary by Country (USD)",
) -> go.Figure:
    """Horizontal bar chart of median salary per country."""
    if not country_data:
        return _empty_figure("No country salary data available")

    df = pd.DataFrame(country_data).dropna(subset=["median_usd"])
    df = df.sort_values("median_usd", ascending=True)

    fig = px.bar(
        df, x="median_usd", y="country", orientation="h",
        color="median_usd",
        color_continuous_scale=_GREEN_SCALE,
        labels={"median_usd": "Median USD", "country": ""},
        hover_data={"sample_size": True},
        custom_data=["sample_size"],
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>Median: $%{x:,.0f}<br>Sample: %{customdata[0]}<extra></extra>"
    )
    fig.update_coloraxes(showscale=False)
    fig.update_xaxes(tickprefix="$", tickformat=",")
    _apply_theme(fig, title)
    return fig


def bar_salary_by_experience(
    experience_data: list[dict[str, Any]],
    title: str = "Median Salary by Experience Level (USD)",
) -> go.Figure:
    """Bar chart showing salary progression by seniority."""
    if not experience_data:
        return _empty_figure("No experience salary data available")

    level_order = ["internship", "entry", "mid", "senior", "lead", "principal", "executive"]
    df = pd.DataFrame(experience_data).dropna(subset=["median_usd"])
    df["experience_level"] = pd.Categorical(df["experience_level"], categories=level_order, ordered=True)
    df = df.sort_values("experience_level")

    fig = px.bar(
        df, x="experience_level", y="median_usd",
        color="median_usd",
        color_continuous_scale=_GREEN_SCALE,
        labels={"experience_level": "Level", "median_usd": "Median USD"},
        text="median_usd",
    )
    fig.update_traces(
        texttemplate="$%{text:,.0f}",
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Median: $%{y:,.0f}<extra></extra>",
    )
    fig.update_coloraxes(showscale=False)
    fig.update_yaxes(tickprefix="$", tickformat=",")
    _apply_theme(fig, title)
    return fig


def gauge_remote_salary_premium(
    premium_pct: Optional[float],
    title: str = "Remote Salary Premium",
) -> go.Figure:
    """
    Gauge chart showing the percentage premium for remote vs on-site roles.
    """
    if premium_pct is None:
        return _empty_figure("Insufficient salary data for remote premium calculation")

    color = _PALETTE[1] if premium_pct >= 0 else _PALETTE[2]
    fig = go.Figure(go.Indicator(
        mode="number+delta",
        value=premium_pct,
        number={"suffix": "%", "font": {"size": 42}},
        delta={"reference": 0, "suffix": "%"},
        title={"text": title, "font": {"size": 15}},
        domain={"x": [0, 1], "y": [0, 1]},
    ))
    fig.update_layout(**_LAYOUT_DEFAULTS)
    return fig


# ============================================================================
# SCRAPE RUN CHARTS
# ============================================================================

def bar_scrape_history(
    runs: list[dict[str, Any]],
    title: str = "Recent Scrape Run History",
) -> go.Figure:
    """Bar chart showing jobs inserted per scrape run over time."""
    if not runs:
        return _empty_figure("No scrape run history")

    df = pd.DataFrame(runs).dropna(subset=["started_at"])
    df["started_at"] = pd.to_datetime(df["started_at"])
    df = df.sort_values("started_at")

    colors = [
        "#10b981" if s == "completed" else "#ef4444"
        for s in df["status"]
    ]
    opacities = [
        1.0 if s == "completed" else 0.7
        for s in df["status"]
    ]

    fig = go.Figure(go.Bar(
        x=df["started_at"].dt.strftime("%m/%d %H:%M"),
        y=df["jobs_inserted"].fillna(0),
        marker=dict(
            color=colors,
            opacity=opacities,
            line=dict(width=0),
            cornerradius=4,
        ),
        hovertemplate="<b>%{x}</b><br>Inserted: %{y:,}<extra></extra>",
    ))
    _apply_theme(fig, title)
    fig.update_xaxes(tickangle=-30)
    return fig


# ============================================================================
# UTILITY
# ============================================================================

def _empty_figure(message: str) -> go.Figure:
    """Returns a blank figure with a centered message — light theme."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=13, color="#9ca3af", family="Inter, sans-serif"),
    )
    fig.update_layout(
        **_LAYOUT_DEFAULTS,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig
