# AI Job Market Intelligence Platform

### _Turn raw job postings into market intelligence — automatically._

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PostgreSQL-14%2B-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/FastAPI-0.136-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Streamlit-1.57-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white" alt="Streamlit">
  <img src="https://img.shields.io/badge/spaCy-3.8-09A3D5?style=for-the-badge&logo=spacy&logoColor=white" alt="spaCy">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <img src="screenshots/overview.png" width="95%">
</p>

## ⭐ Project at a Glance

Built to demonstrate production-style data engineering, backend development, NLP, and analytics engineering skills in a single end-to-end project.

- 🔹 **2 Public Job Data Sources** (RemoteOK + Greenhouse)
- 🔹 **6 Production-Ready REST API Endpoints**
- 🔹 **4 Interactive Streamlit Dashboard Pages**
- 🔹 **NLP-powered Skill Extraction using spaCy**
- 🔹 **PostgreSQL + FastAPI + Streamlit + SQLAlchemy**
- 🔹 **End-to-End Data Engineering & Analytics Pipeline**

---

An end-to-end data pipeline that ingests job listings from public APIs, enriches every record with NLP-extracted skills, computes month-over-month demand trends, normalizes multi-currency salaries to USD, and delivers the results through a FastAPI backend and a four-page Streamlit analytics dashboard.

**Job boards show individual listings. This platform answers the aggregate questions that actually matter:**
- _Which skills are rising in demand this month?_
- _What is the salary premium for remote Python roles vs. on-site?_
- _Which companies are posting the most right now?_

> **Portfolio Focus:** This project demonstrates production-style data engineering, backend API development, PostgreSQL optimization, NLP processing, and analytics dashboard development through a complete end-to-end architecture.

---

**Core technologies:**

| Category | Tools |
|----------|-------|
| **Data ingestion** | Python, requests, RemoteOK API, Greenhouse Board API |
| **Storage** | PostgreSQL 14+, SQLAlchemy 2.0, psycopg2 |
| **NLP & analytics** | spaCy 3.8, Pandas, custom KNOWN_SKILLS dictionary |
| **API layer** | FastAPI 0.136, Uvicorn, Pydantic v2 |
| **Dashboard** | Streamlit 1.57, Plotly, BeautifulSoup4 |

---

> 📸 **Screenshots** — see [Section 16](#16-screenshots) for dashboard previews.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Features](#2-features)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Data Flow](#4-data-flow)
5. [Project Structure](#5-project-structure)
6. [Technology Stack](#6-technology-stack)
7. [Design Decisions](#7-design-decisions)
8. [Installation](#8-installation)
9. [Environment Variables](#9-environment-variables)
10. [Running the Project](#10-running-the-project)
11. [API Endpoints](#11-api-endpoints)
12. [Dashboard Pages](#12-dashboard-pages)
13. [Machine Learning Pipeline](#13-machine-learning-pipeline)
14. [Database Schema](#14-database-schema)
15. [Future Roadmap](#15-future-roadmap)
16. [Screenshots](#16-screenshots)
17. [License](#17-license)

---

## 1. Project Overview

The **AI Job Market Intelligence Platform** is an end-to-end data pipeline for the tech job market. It collects job postings from two public APIs — RemoteOK and Greenhouse board companies — enriches each record with NLP-extracted skill tags using spaCy, aggregates month-over-month skill demand trends, normalizes multi-currency salary data to USD, and surfaces everything through a read-only REST API and an interactive analytics dashboard.

The project is structured as four loosely coupled layers that communicate strictly left to right:

```
Public Job APIs → Scraper Layer → PostgreSQL → ML Pipeline → FastAPI → Streamlit
```

The dashboard never reads the database directly. It communicates exclusively through the FastAPI layer, which means the analytics and the database schema can evolve independently.

**Why this project matters:** Job boards show individual listings. This platform answers aggregate questions: *Which skills are rising in demand this month? What is the salary premium for remote Python roles vs. on-site? Which companies are hiring the most right now?* These are the signals that matter to engineers and hiring managers.

---

## 2. Features

### Data Ingestion
- **RemoteOK scraper** — hits the public JSON API at `https://remoteok.com/api`, parses salary strings into structured `salary_min`/`salary_max` integers, infers experience level and job type from title keywords and API tags, filters noise tags, and bulk-inserts with full deduplication (`ON CONFLICT DO NOTHING` on `source_url`)
- **Greenhouse scraper** — queries the public Greenhouse Board API (`https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`) for a configurable list of company board tokens, extracts city/country from location strings, and preserves HTML job descriptions for downstream NLP
- **Scrape Run audit log** — every execution creates a `ScrapeRun` record with job counts, duplicate counts, error messages, and timestamps; visible in the dashboard's Overview page

### NLP & Analytics Pipeline
- **Skill extraction** — spaCy `en_core_web_sm` processes job titles and descriptions; tokens and noun chunks are matched against a curated `KNOWN_SKILLS` dictionary, normalized through a `SKILL_ALIASES` map (e.g. `"reactjs"` → `"react"`, `"golang"` → `"go"`), merged with scraper-provided tags, deduplicated, and written back to `jobs.skills`
- **Trend analysis** — for each month in a configurable lookback window, computes per-skill job counts, month-over-month change, percentage change, and classifies each skill as `RISING / STABLE / DECLINING / NEW`; runs globally and per top-5 country; upserts into the pre-aggregated `skill_trends` table
- **Salary analysis** — loads salary-disclosed jobs, normalizes all currencies to USD using a static conversion rate map (INR, GBP, EUR, CAD, AUD, and others), computes median, 25th percentile, and 75th percentile by skill, country, experience level, and remote vs. on-site
- **Co-occurrence mapping** — for each skill, records which other skills appear in the same job posting as a JSONB map `{"docker": 0.73, "kubernetes": 0.61}` stored in `skill_trends.co_occurring_skills`
- **Failure-isolated orchestration** — the pipeline scheduler catches exceptions per stage so a broken scraper run never blocks trend or salary analytics

### REST API (FastAPI)
- Six production-ready endpoints with full OpenAPI/Swagger documentation at `/docs`
- Paginated, filterable, sortable job listing with 11 filter parameters
- Skill demand ranking powered by PostgreSQL `unnest()` aggregation (runs in the database, not Python)
- Scraper audit log endpoint for operational monitoring
- Health check endpoint that always returns 200 and reports degraded state in the body
- Read-only by design — CORS allows only `GET` methods
- Pydantic v2 response schemas with `from_attributes=True` ORM integration

### Streamlit Dashboard
- **Overview** — 4 KPI metrics, top in-demand skills bar chart (or job title fallback), top hiring companies chart, scrape run audit table, latest run detail expander
- **Skills Analytics** — searchable top-N skills bar chart, sortable data table with CSV download, frequency distribution chart
- **Salary Intelligence** — salary by skill (median USD), top 10 compensation table, remote vs. on-site median comparison with premium/discount percentage, salary distribution histogram
- **Job Explorer** — full-text search, skill filter, company filter, experience level filter, work-type filter, sort options, server-side pagination, per-job detail card with on-demand description loading
- Green/white SaaS design theme with Inter font, dot-grid background, hover animations, and live API/DB health indicator in the sidebar

### Engineering Quality
- **Pydantic-Settings config** — single `Settings` class validates and type-coerces all environment variables at startup; no scattered `os.environ` calls anywhere in the codebase
- **Connection pool tuning** — SQLAlchemy `QueuePool` with configurable `pool_size`, `max_overflow`, `pool_timeout`, and `pool_recycle`; warns at startup if pool ceiling exceeds PostgreSQL's `max_connections`
- **PostgreSQL-native types** — `ARRAY(String)` for skills with GIN index, `JSONB` for raw metadata and co-occurrence maps with GIN index, partial index for unprocessed ML jobs
- **Deduplication at the database layer** — `UNIQUE` constraint on `source_url` guarantees no duplicates even if scraper-side logic has a bug
- **Retry + backoff on HTTP calls** — dashboard `api_client.py` uses a `requests.Session` with `HTTPAdapter` (3 retries, exponential backoff 0.5 / 1 / 2 s)
- **Cache keying correctness** — all Streamlit `@st.cache_data` loaders use explicit named parameters (not `**kwargs`) so Streamlit can build deterministic hash keys

---

## 3. Architecture Diagram

```mermaid
flowchart TD
    subgraph Sources["Data Sources"]
        R["RemoteOK\nPublic JSON API\nhttps://remoteok.com/api"]
        G["Greenhouse Board API\nboards-api.greenhouse.io"]
    end

    subgraph Scraper["Scraper Layer — scraper/"]
        PS["playwright_scraper.py\nRemoteOK ingestion"]
        GS["greenhouse_scraper.py\nGreenhouse ingestion"]
    end

    subgraph DB["PostgreSQL — database/"]
        C["crud.py\nRepository Layer\n~1,300 lines"]
        T1["jobs table"]
        T2["skill_trends table"]
        T3["scrape_runs table"]
    end

    subgraph ML["ML Pipeline — ml/"]
        SCH["scheduler.py\nOrchestrator + CLI"]
        SE["skill_extractor.py\nspaCy NLP"]
        TA["trend_analyzer.py\nMonth-over-month trends"]
        SA["salary_analyzer.py\nUSD normalization"]
    end

    subgraph API["FastAPI — api/"]
        JR["routes/jobs.py\nGET /jobs, /jobs/with-salary, /jobs/{id}"]
        SR["routes/stats.py\nGET /stats/top-skills, /scrape-runs, /health"]
        SC["schemas.py\nPydantic v2 response models"]
    end

    subgraph Dash["Streamlit — dashboard/"]
        AC["api_client.py\nHTTP + retry/backoff"]
        APP["app.py\n4 pages + CSS + caching"]
        CH["charts.py\nPlotly figure factory"]
        UT["utils.py\nFormatters + UI helpers"]
    end

    R --> PS
    G --> GS
    PS --> C
    GS --> C
    C --> T1
    C --> T3
    T1 --> SE --> C
    T1 --> TA --> T2
    T1 --> SA --> T2
    SCH --> PS
    SCH --> GS
    SCH --> SE
    SCH --> TA
    SCH --> SA
    T1 --> JR --> SC
    T2 --> SR --> SC
    T3 --> SR
    SC --> AC --> APP
    APP --> CH
    APP --> UT
```

---

## 4. Data Flow

### Stage 1 — Ingestion (Scraper → Database)

```mermaid
sequenceDiagram
    participant CLI as CLI / ml.scheduler
    participant SC as Scraper
    participant SRC as Source API
    participant DB as PostgreSQL

    CLI->>SC: python -m scraper.playwright_scraper
    SC->>DB: crud.create_scrape_run() — status="running"
    SC->>SRC: GET https://remoteok.com/api
    SRC-->>SC: JSON array of job objects
    SC->>SC: Parse salary strings to salary_min/max integers
    SC->>SC: Infer experience_level from title keywords
    SC->>SC: Infer job_type from tags/title
    SC->>SC: Filter noise tags, build clean skills list
    SC->>SC: Detect is_remote, is_hybrid from location/tags
    SC->>DB: crud.bulk_insert_jobs() — INSERT ON CONFLICT DO NOTHING
    SC->>DB: crud.complete_scrape_run() — status="completed" + stats
```

### Stage 2 — ML Processing (Database → Database)

```mermaid
sequenceDiagram
    participant SCH as ml.scheduler
    participant SE as skill_extractor.py
    participant TA as trend_analyzer.py
    participant SA as salary_analyzer.py
    participant DB as PostgreSQL

    SCH->>SE: run_skill_extraction()
    SE->>DB: SELECT jobs WHERE is_skills_extracted = false
    DB-->>SE: Unprocessed job rows
    SE->>SE: spaCy NLP on title + description
    SE->>SE: Match KNOWN_SKILLS, apply SKILL_ALIASES
    SE->>SE: Merge NLP skills with scraper tags, deduplicate
    SE->>DB: crud.update_job_skills() + mark_skills_extracted()

    SCH->>TA: run_trend_analysis()
    TA->>DB: Load jobs per month via raw SQL + unnest()
    TA->>TA: Count frequencies, compare to previous month
    TA->>TA: Classify RISING / STABLE / DECLINING / NEW
    TA->>TA: Compute co-occurring skills JSONB map
    TA->>DB: crud.upsert_skill_trend() — global + per country

    SCH->>SA: run_salary_analysis()
    SA->>DB: Load jobs WHERE salary_min IS NOT NULL
    SA->>SA: Normalize all currencies to USD (static rates)
    SA->>SA: Compute median, p25, p75 by skill/country/level/remote
```

### Stage 3 — Read Path (Database → API → Dashboard)

```mermaid
sequenceDiagram
    participant ST as Streamlit Dashboard
    participant AC as api_client.py
    participant FA as FastAPI
    participant CR as crud.py
    participant DB as PostgreSQL

    ST->>AC: get_top_skills(limit=20, country="US")
    AC->>FA: GET /api/v1/stats/top-skills?limit=20&country=US
    FA->>CR: crud.get_top_skills(db, limit=20, country="US")
    CR->>DB: SELECT unnest(skills), COUNT(*) FROM jobs GROUP BY 1
    DB-->>CR: [(skill, count), ...]
    CR-->>FA: List of dicts
    FA->>FA: Validate → TopSkillsResponse (Pydantic v2)
    FA-->>AC: JSON response
    AC-->>ST: Parsed Python list (or [] on error)
    ST->>ST: @st.cache_data TTL=300s → charts.bar_top_skills()
```

---

## 5. Project Structure

```
AI_Job_Market_Intelligence/
│
├── config.py                    # Pydantic-Settings singleton — all config lives here
├── requirements.txt             # Pinned dependency list
├── .env                         # Environment variables (not committed)
├── .gitignore
│
├── database/                    # Everything PostgreSQL
│   ├── connection.py            # Engine, session factory, get_db(), get_db_session()
│   ├── models.py                # ORM models: Job, SkillTrend, ScrapeRun + 3 enums
│   ├── crud.py                  # Repository layer (~1,300 lines, all DB reads/writes)
│   └── create_tables.py         # One-shot table creation utility
│
├── scraper/                     # Data ingestion layer
│   ├── playwright_scraper.py    # RemoteOK JSON API scraper
│   └── greenhouse_scraper.py    # Greenhouse Board API scraper
│
├── ml/                          # Analytics pipeline
│   ├── scheduler.py             # Pipeline orchestrator with CLI flags
│   ├── skill_extractor.py       # spaCy NLP skill extraction
│   ├── trend_analyzer.py        # Month-over-month skill demand trends
│   ├── salary_analyzer.py       # Multi-currency salary aggregation (USD normalization)
│   ├── constants.py             # KNOWN_SKILLS, SKILL_ALIASES, USD_CONVERSION_RATES
│   └── utils.py                 # batched(), timed(), month_boundaries(), utcnow()
│
├── api/                         # FastAPI application
│   ├── main.py                  # App assembly, lifespan, CORS, global error handler
│   ├── schemas.py               # Pydantic v2 response models (6 schemas)
│   └── routes/
│       ├── jobs.py              # GET /api/v1/jobs, /jobs/with-salary, /jobs/{id}
│       └── stats.py             # GET /api/v1/stats/top-skills, /scrape-runs, /health
│
├── dashboard/                   # Streamlit analytics dashboard
│   ├── app.py                   # 1,435-line: 4 pages + ~490 lines CSS + caching
│   ├── api_client.py            # HTTP layer with requests.Session + retry/backoff
│   ├── charts.py                # Plotly chart factory (pure go.Figure, zero st.* calls)
│   └── utils.py                 # Formatters, DataFrame converters, pagination, UI helpers
│
├── docs/
│   └── project_analysis.md      # Detailed architecture analysis
│
├── notebooks/                   # Jupyter notebooks for exploratory data analysis
└── tests/                       # Test directory
```

---

## 6. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Language | Python | 3.11+ | All application code |
| Database | PostgreSQL | 14+ | Primary data store |
| ORM | SQLAlchemy | 2.0.49 | Models, query building, connection pooling |
| DB Driver | psycopg2-binary | 2.9.12 | PostgreSQL adapter |
| API Framework | FastAPI | 0.136.1 | REST API with automatic OpenAPI docs |
| ASGI Server | Uvicorn | 0.46.0 | Production-grade async server |
| Validation | Pydantic v2 | 2.13.4 | Response schemas, config validation |
| Config | pydantic-settings | — | `.env` loading with type coercion |
| Dashboard | Streamlit | 1.57.0 | Interactive analytics UI |
| Charts | Plotly | — | Interactive visualizations |
| NLP | spaCy | 3.8.13 | Named entity recognition, noun chunk extraction |
| NLP Model | en_core_web_sm | 3.8.0 | Lightweight English NLP model (12 MB) |
| HTTP Client | requests | 2.33.1 | Scraper API calls, dashboard API client |
| Data | Pandas | 3.0.2 | DataFrame manipulation in ML pipeline |
| HTML Parsing | BeautifulSoup4 | 4.14.3 | Job description HTML stripping |
| Env | python-dotenv | 1.2.2 | `.env` file reading |

### Key PostgreSQL Features Used

| Feature | Where | Why |
|---------|-------|-----|
| `ARRAY(String)` | `jobs.skills` | Store skill lists without a junction table |
| `JSONB` | `jobs.raw_metadata`, `skill_trends.co_occurring_skills` | Schema-flexible, indexed, queryable JSON |
| GIN index | `skills`, `raw_metadata`, `co_occurring_skills` | Fast `ANY()` and JSONB key queries |
| Composite B-tree | `(city, country, posted_at)`, `(experience_level, posted_at)` | Cover the most common filter combinations |
| Partial index | `scraped_at WHERE is_skills_extracted = false` | Tiny, fast ML processing queue index |
| `unnest()` aggregation | `crud.get_top_skills()` | Flatten skill arrays for `GROUP BY` in-database |
| `ON CONFLICT DO NOTHING` | `crud.bulk_insert_jobs()` | Database-level deduplication |
| `ON CONFLICT DO UPDATE` | `crud.upsert_skill_trend()` | Idempotent ML pipeline writes |

---

## 7. Design Decisions

Key architectural choices and the reasoning behind them.

---

**Why a separate REST API instead of the dashboard querying the database directly?**

The dashboard communicates exclusively through FastAPI. This means the Streamlit frontend has no knowledge of the database schema — it only knows the API contract. The database schema and the analytics can therefore evolve independently. It also makes the analytics layer reusable: any client (a CLI script, a mobile app, a second dashboard) can consume the same endpoints without touching the database.

---

**Why FastAPI over Flask or Django?**

FastAPI generates OpenAPI/Swagger documentation automatically from Pydantic schemas, with zero extra code. It provides native async support, built-in request validation, and dependency injection (used here for database session management). The interactive `/docs` UI lets you test every endpoint without a separate client during development.

---

**Why PostgreSQL over a document store or SQLite?**

PostgreSQL's `ARRAY(String)` type lets skills live as a native column — no junction table, no joins — while still supporting `WHERE 'python' = ANY(skills)` queries with a GIN index. `JSONB` handles schema-flexible scraped metadata with full index support. `ON CONFLICT DO NOTHING` / `DO UPDATE` make both deduplication and idempotent ML writes trivial. These are features that SQLite and most document stores do not offer at this level.

---

**Why SQLAlchemy ORM over raw SQL?**

The ORM provides a repository layer (`crud.py`) that centralizes every read and write in one file. Routes contain zero SQL. The connection pool, session lifecycle, and retry logic are handled by SQLAlchemy, not scattered across the codebase. Where raw SQL is genuinely needed (e.g., `unnest(skills)` aggregation in trend analysis), it is used explicitly and is clearly documented.

---

**Why Streamlit for the dashboard?**

Streamlit turns a Python script into an interactive web application with no JavaScript, no HTML templates, and no frontend build step. For a data-heavy analytics dashboard backed by a REST API, it provides fast iteration with `@st.cache_data` for TTL-based caching and `st.session_state` for pagination. The tradeoff is that the entire app re-runs on every interaction — mitigated here by caching all API calls.

---

**Why separate the ML pipeline from the scrapers?**

The scraper's job is to get raw data into the database as fast as possible. NLP (spaCy) and aggregation (trend/salary analysis) are expensive and can be run asynchronously on demand. Decoupling them means a failed scraper run does not block analytics, and analytics can be re-run any number of times without re-scraping. All ML writes are idempotent (`ON CONFLICT DO UPDATE`), so re-running is always safe.

---

**Why PostgreSQL `ARRAY` and `JSONB` instead of a skills junction table?**

A junction table (`job_skills`) would require a join on every query. Since skills are always read as a list alongside the job — never queried inversely in complex ways — an `ARRAY(String)` column with a GIN index provides the same `ANY()` lookup performance with dramatically simpler queries and zero join overhead. `JSONB` is used for raw scraped metadata and co-occurrence maps because the schema varies by source platform and is best left flexible.

---

**Why a repository layer (`crud.py`)?**

All database reads and writes are centralized in `database/crud.py`. Routes in `api/routes/` contain no SQLAlchemy code. ML modules call `crud` functions, not the ORM directly. This means the query logic is tested in one place, the session lifecycle is managed in one place, and swapping a query implementation never requires touching the route or the ML stage that calls it.

---

## 8. Installation

### Prerequisites

- Python 3.11 or higher
- PostgreSQL 14 or higher
- `pip` / virtual environment manager

### Step-by-step Setup

**1. Clone the repository**

```bash
git clone https://github.com/rathorevastav/AI-Job-Market-Intelligence.git
cd AI-Job-Market-Intelligence
```

**2. Create and activate a virtual environment**

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python -m venv venv
source venv/bin/activate
```

**3. Install all dependencies**

```bash
pip install -r requirements.txt
```

**4. Download the spaCy language model**

```bash
python -m spacy download en_core_web_sm
```

**5. Create the PostgreSQL database**

```sql
-- In psql or pgAdmin:
CREATE DATABASE job_market_db;
```

**6. Configure environment variables**

Edit `.env` in the project root with your values (see [Environment Variables](#9-environment-variables) below).

**7. Create all database tables**

```bash
python -c "from database.connection import create_tables; create_tables()"
```

This creates the `jobs`, `skill_trends`, and `scrape_runs` tables with all indexes and constraints.

---

## 9. Environment Variables

Save the following as `.env` in the project root. **Never commit `.env` to Git.**

```env
# PostgreSQL connection
DB_USER=postgres
DB_PASSWORD=your_secure_password_here   # Required — no default
DB_HOST=localhost
DB_PORT=5432
DB_NAME=job_market_db
DB_ECHO=false          # true logs all SQL queries (development only)

# Application
APP_ENV=development    # development | staging | production
LOG_LEVEL=INFO         # DEBUG | INFO | WARNING | ERROR | CRITICAL

# Optional: overrides all DB_* fields (useful for PaaS like Heroku / Railway)
# DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
```

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `DB_USER` | `postgres` | No | PostgreSQL username |
| `DB_PASSWORD` | — | **Yes** | PostgreSQL password |
| `DB_HOST` | `localhost` | No | Database host |
| `DB_PORT` | `5432` | No | PostgreSQL port |
| `DB_NAME` | `job_market_db` | No | Database name |
| `DB_ECHO` | `false` | No | Log every SQL statement |
| `APP_ENV` | `development` | No | Runtime environment |
| `LOG_LEVEL` | `INFO` | No | Python logging level |
| `DATABASE_URL` | auto-built | No | Full DSN — overrides all `DB_*` fields |
| `DB_POOL_SIZE` | `10` | No | Permanent connections in the SQLAlchemy pool |
| `DB_MAX_OVERFLOW` | `20` | No | Extra connections allowed during spikes |
| `DB_POOL_TIMEOUT` | `30` | No | Seconds to wait for a free connection |
| `DB_POOL_RECYCLE` | `1800` | No | Recycle connections after 30 min (prevents stale connections) |

> `config.py` validates all variables at startup using Pydantic. A missing or empty `DB_PASSWORD` raises a clear `ValidationError` immediately — no mysterious runtime failures later.

---

## 10. Running the Project

Each component is an independent process. Start them in this order.

### Step 1 — Verify PostgreSQL is Reachable

```bash
python -c "from database.connection import check_database_connection; check_database_connection()"
```

### Step 2 — Run the Scrapers

**RemoteOK scraper** — fetches from `https://remoteok.com/api`:

```bash
# Fetch all available jobs
python -m scraper.playwright_scraper

# Limit for testing
python -m scraper.playwright_scraper --max-jobs 100
```

**Greenhouse scraper** — fetches from configured company boards:

```bash
# All pre-configured boards
python -m scraper.greenhouse_scraper

# Limit per board
python -m scraper.greenhouse_scraper --max-jobs-per-board 50

# Specific company boards
python -m scraper.greenhouse_scraper --companies stripe airbnb linear
```

Both scrapers automatically create and update a `ScrapeRun` audit record.

### Step 3 — Run the ML Pipeline

```bash
# Full pipeline: both scrapers + skill extraction + trend analysis + salary analysis
python -m ml.scheduler

# Analytics only (skip scraping, process existing data)
python -m ml.scheduler --skip-scraper --skip-greenhouse

# Skill extraction only
python -m ml.scheduler --only-skills

# Trend analysis — last 6 months, with per-country breakdowns
python -m ml.scheduler --only-trends --months-back 6 --countries US IN GB

# Greenhouse boards only, specific companies
python -m ml.scheduler --only-greenhouse --greenhouse-companies stripe airbnb
```

**All CLI flags:**

| Flag | Effect |
|------|--------|
| *(no flags)* | Full pipeline: RemoteOK + Greenhouse + skills + trends + salary |
| `--skip-scraper` | Skip the RemoteOK scraper |
| `--skip-greenhouse` | Skip the Greenhouse scraper |
| `--skip-skills` | Skip skill extraction |
| `--skip-trends` | Skip trend analysis |
| `--skip-salary` | Skip salary analysis |
| `--only-scraper` | RemoteOK scraper only |
| `--only-greenhouse` | Greenhouse scraper only |
| `--only-skills` | Skill extraction only |
| `--only-trends` | Trend analysis only |
| `--only-salary` | Salary analysis only |
| `--months-back N` | Trend lookback window in months (default: 3) |
| `--countries XX YY` | ISO codes for per-country trend breakdowns |
| `--greenhouse-companies A B` | Override the default list of Greenhouse board tokens |

### Step 4 — Start the FastAPI Server

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

| URL | Purpose |
|-----|---------|
| `http://localhost:8000` | Root — returns API info and links |
| `http://localhost:8000/docs` | Swagger UI (interactive docs) |
| `http://localhost:8000/redoc` | ReDoc (readable docs) |
| `http://localhost:8000/api/v1/stats/health` | Health check |

### Step 5 — Start the Streamlit Dashboard

```bash
# Run from the dashboard/ directory so relative imports resolve correctly
cd dashboard
streamlit run app.py
```

The dashboard opens at `http://localhost:8501`. The FastAPI server must be running first — the sidebar shows a live green/red health indicator.

---

## 11. API Endpoints

**Base URL:** `http://localhost:8000/api/v1`

All endpoints are **read-only** (`GET` only). Full interactive docs at `/docs`.

---

### `GET /jobs`

Paginated, filterable list of job postings. All 11 filter parameters are optional and combinable.

| Parameter | Type | Description |
|-----------|------|-------------|
| `city` | string | Partial city name match (case-insensitive) |
| `country` | string | ISO 3166-1 alpha-2 code (e.g. `US`, `IN`, `GB`) |
| `company_name` | string | Partial company name match |
| `skill` | string | Exact skill match in the skills array (e.g. `python`) |
| `experience_level` | enum | `internship` / `entry` / `mid` / `senior` / `lead` / `principal` / `executive` |
| `job_type` | enum | `full_time` / `part_time` / `contract` / `freelance` / `internship` |
| `is_remote` | bool | `true` = remote only, `false` = on-site only |
| `source_platform` | string | `remoteok` or `greenhouse` |
| `posted_after` | ISO 8601 | Jobs posted after this timestamp |
| `posted_before` | ISO 8601 | Jobs posted before this timestamp |
| `search_query` | string | Full-text search across title and description |
| `page` | int | Page number (1-based, default: 1) |
| `page_size` | int | Results per page (1–100, default: 20) |
| `order_by` | string | `posted_at` / `created_at` / `salary_min` / `salary_max` / `company_name` / `title` |
| `descending` | bool | Sort direction (default: `true`) |

**Example response:**

```json
{
  "items": [
    {
      "id": 1042,
      "title": "Senior Python Developer",
      "company_name": "Stripe",
      "city": "San Francisco",
      "country": "US",
      "is_remote": true,
      "experience_level": "senior",
      "job_type": "full_time",
      "skills": ["python", "aws", "postgresql", "docker"],
      "salary_min": 180000,
      "salary_max": 240000,
      "salary_currency": "USD",
      "source_platform": "greenhouse",
      "source_url": "https://boards.greenhouse.io/stripe/jobs/44444",
      "posted_at": "2026-08-01T12:00:00Z"
    }
  ],
  "total": 4821,
  "page": 1,
  "page_size": 20,
  "pages": 242
}
```

---

### `GET /jobs/with-salary`

Returns only jobs where `salary_min` or `salary_max` is disclosed. Accepts the same pagination and sorting parameters as `GET /jobs`, plus optional `country` and `source_platform` filters. Used by the Salary Intelligence dashboard page.

---

### `GET /jobs/{job_id}`

Returns the full job record including `description` text (excluded from list responses for performance).

**Responses:** `200 OK` with full job detail / `404 Not Found`

---

### `GET /stats/top-skills`

Returns the most frequently appearing skills, ranked by job count. Powered by PostgreSQL `unnest()` aggregation — no Python-side counting.

| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | int | Max skills to return (1–100, default: 20) |
| `country` | string | ISO country code filter |
| `posted_after` | ISO 8601 | Count only jobs posted after this date |

**Example response:**

```json
{
  "skills": [
    { "skill": "python", "job_count": 1842 },
    { "skill": "react",  "job_count": 1203 },
    { "skill": "aws",    "job_count":  987 }
  ],
  "total_skills": 20,
  "filters_applied": { "country": "US" }
}
```

> `job_count` reflects only jobs where `is_skills_extracted = true`. Run `python -m ml.scheduler --only-skills` to populate this flag.

---

### `GET /stats/scrape-runs`

Returns recent scraper audit records.

| Parameter | Type | Description |
|-----------|------|-------------|
| `platform` | string | Filter by platform (`remoteok`, `greenhouse`) |
| `status` | string | Filter by status (`completed`, `failed`, `running`) |
| `limit` | int | Max records (1–100, default: 10) |

---

### `GET /stats/health`

Health check. Returns API status, DB connectivity, total job count, and the most recent scrape run. Always returns `200 OK` — degraded state is reported in the response body, never via HTTP status codes.

**Example response:**

```json
{
  "api_status": "ok",
  "database_connected": true,
  "total_jobs": 12483,
  "latest_scrape_run": {
    "platform": "remoteok",
    "status": "completed",
    "jobs_found": 832,
    "jobs_inserted": 214,
    "jobs_skipped_duplicate": 618,
    "started_at": "2026-08-12T07:00:00Z"
  },
  "timestamp": "2026-08-12T13:23:00Z"
}
```

---

## 12. Dashboard Pages

### Overview

**Purpose:** High-level market snapshot and scraper health.

**KPI Row (4 metrics):**
- Total Jobs Indexed (from `/stats/health`)
- Skills Tracked (distinct skills returned from API) — falls back to Recent Jobs count if ML pipeline has not run
- Remote Jobs % (computed from the most recent 100-job batch)
- Last Scrape (relative time: "2 hours ago")

**Charts:**
- **Top In-Demand Skills** — horizontal bar chart of top 10 skills by job count; falls back to Top Job Titles bar chart if the ML pipeline has not processed any jobs yet
- **Top Hiring Companies** — horizontal bar chart of the 8 most active companies in the current filter view; falls back to Scraper History bar chart if no job data

**Tables:**
- **Scraper Audit Log** — platform, status, jobs found/inserted/skipped/failed, start time; downloadable as a DataFrame
- **Latest Scrape Run Detail** — expandable section showing 4 metrics + error message if the last run failed

**Sidebar filters applied:** Country, Time Range

---

### Skills Analytics

**Purpose:** Identify which skills are most in demand across all scraped listings.

**Controls:**
- Search box — client-side filter on skill names
- Top-N selector — 10 / 20 / 30 / 50

**Visualizations:**
- Full-width horizontal bar chart of top-N skills
- **Table tab** — sortable data table (Skill, Job Count); CSV download button
- **Distribution tab** — pie chart of top-half vs. bottom-half skill frequency split

**Info callout:** Highlights the #1 skill and its exact job count.

**Sidebar filters applied:** Country, Time Range

---

### Salary Intelligence

**Purpose:** Compensation benchmarks from salary-disclosed job postings.

**Data source:** `GET /jobs/with-salary` endpoint — only jobs with `salary_min` or `salary_max` set.

**Visualizations:**
- **Salary by Skill (Median USD)** — horizontal bar chart of top 15 skills by median salary; all currencies normalized to USD using static conversion rates from `ml/constants.py`
- **Top 10 Compensation Table** — skill name, median salary (formatted), sample size

**Metrics row:**
- **Remote Median** — median salary across remote-eligible jobs (USD)
- **On-site Median** — median salary across non-remote jobs (USD)
- **Remote Premium** — percentage difference between remote and on-site medians

**Histogram:** Salary distribution across all salary-disclosed jobs in the current filter view.

**Sidebar filters applied:** Country, Time Range

> Note: Only RemoteOK job listings include salary data. Greenhouse boards do not expose compensation. The sample reflects only roles that voluntarily disclose salary.

---

### Job Explorer

**Purpose:** Full search and browse interface across all indexed job postings.

**Filter Row 1:** Keyword search / Skill filter / Company filter / Work Type (All / Remote / On-site)

**Filter Row 2:** Experience Level / Sort (Newest / Oldest / Salary ↑ / Company) / Per Page (10 / 20 / 50)

**Results:**
- Match count: "4,821 jobs match your filters"
- Tabular view of the current page (hides `ID` and `URL` columns from display)
- Server-side pagination with Previous / Next controls; page number persisted in `st.session_state` and reset automatically when any filter changes

**Job Detail Card** (select any job from the results):
- Title, company, location, experience level, salary range
- Job type, remote eligibility, posting date
- Skill tags
- Source platform link (links back to the original job posting)
- "Load full description" button — fetches `GET /jobs/{id}` on demand, strips HTML tags, truncates to 3,000 characters

---

## 13. Machine Learning Pipeline

The ML pipeline is fully decoupled from the scraper. It processes data already in the database and all writes are idempotent — safe to run multiple times.

### Stage 1 — Skill Extraction (`ml/skill_extractor.py`)

```
Input:  SELECT * FROM jobs WHERE is_skills_extracted = false
Output: UPDATE jobs SET skills = [...], is_skills_extracted = true
```

**Algorithm:**
1. Load spaCy `en_core_web_sm` once as a process-level singleton
2. For each unprocessed job: concatenate `title + " " + description[:5000]`
3. Clean text — strip HTML, normalize whitespace, lowercase
4. Run spaCy pipeline → extract tokens and noun chunks
5. Match each token/n-gram against `KNOWN_SKILLS` dictionary (hundreds of curated entries covering languages, frameworks, clouds, databases, and tools)
6. Normalize via `SKILL_ALIASES` map (~100 mappings: `"reactjs"` → `"react"`, `"golang"` → `"go"`, `"nodejs"` → `"node.js"`)
7. Filter `NOISE_WORDS`
8. Merge with skills already in `jobs.skills` (scraper-provided tags)
9. Deduplicate and sort by confidence score
10. Write back via `crud.update_job_skills()` + `crud.mark_skills_extracted()`

Processes in configurable batches (`--batch-size`, `--max-batches`) to manage memory.

---

### Stage 2 — Trend Analysis (`ml/trend_analyzer.py`)

```
Input:  SELECT skills, salary_*, is_remote, country FROM jobs
        WHERE posted_at IN [month window]
Output: UPSERT INTO skill_trends (skill_name, period_start, period_end, job_count, ...)
```

**Algorithm (per month, per geographic scope):**
1. Load jobs for the period using raw SQL with `unnest(skills)` aggregation
2. Count job occurrences per skill → `job_count`
3. Compare to the prior equivalent period → `job_count_change`, `job_count_change_pct`
4. Classify trend direction:
   - `RISING` — percentage increase ≥ `TREND_RISING_THRESHOLD`
   - `DECLINING` — percentage decrease ≥ `TREND_DECLINING_THRESHOLD`
   - `NEW` — skill absent in the prior period
   - `STABLE` — all other cases
5. Compute co-occurring skills: which other skills appear in the same job postings? Stored as JSONB `{"docker": 0.73, "aws": 0.61}`
6. Embed salary aggregates from the salary analyzer
7. Upsert via `crud.upsert_skill_trend()` using `ON CONFLICT DO UPDATE`

**Runs in two scopes:** globally (`country = NULL`) and for each of the top-5 countries by job volume.

---

### Stage 3 — Salary Analysis (`ml/salary_analyzer.py`)

```
Input:  SELECT salary_min, salary_max, salary_currency, skills, country, is_remote
        FROM jobs WHERE salary_min IS NOT NULL AND is_skills_extracted = true
Output: Python dicts — embedded into skill_trends rows by trend_analyzer
```

**Algorithm:**
1. Load salary-disclosed jobs only (both `salary_min` and `salary_max` > 0, both non-null)
2. Normalize to USD using `USD_CONVERSION_RATES` from `ml/constants.py`
   - Supported: USD, EUR, GBP, CAD, AUD, INR, SGD, BRL, JPY, CHF, SEK, NOK, DKK
3. Compute midpoint salary `= (salary_min + salary_max) / 2`
4. Aggregate median, p25, p75 by skill, country, experience level, and remote vs. on-site

All computations are Python-side using sorted list percentiles. At current data volumes (thousands of jobs), this is appropriate. At millions of rows, these aggregations would move to PostgreSQL window functions.

---

### Pipeline Orchestration (`ml/scheduler.py`)

Each stage is wrapped in a `try/except`. A failure in skill extraction does not prevent trend analysis from running. All stage outcomes are tracked in `StageResult` dataclasses and printed in a final summary:

```
[PIPELINE] ✓ SCRAPER_REMOTEOK    | duration=12.3s | {'jobs_inserted': 214}
[PIPELINE] ✓ SCRAPER_GREENHOUSE   | duration=8.7s  | {'jobs_inserted': 89}
[PIPELINE] ✓ SKILL_EXTRACTION     | duration=45.2s | {'processed': 303, 'failed': 0}
[PIPELINE] ✓ TREND_ANALYSIS       | duration=6.1s  | {'skill_trends_upserted': 480}
[PIPELINE] ✓ SALARY_ANALYSIS      | duration=2.3s  | {'salary_jobs_analyzed': 127}
```

---

## 14. Database Schema

### Table: `jobs`

The core entity. One row = one unique job posting.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT` PK | Auto-increment surrogate key |
| `source_url` | `VARCHAR(2048)` UNIQUE | Canonical job URL — the deduplication key |
| `source_platform` | `VARCHAR(100)` | `remoteok` or `greenhouse` |
| `external_id` | `VARCHAR(256)` | Platform's own job ID |
| `title` | `VARCHAR(512)` | Job title as scraped |
| `description` | `TEXT` | Full description (HTML preserved for NLP) |
| `experience_level` | `ENUM` | `internship` / `entry` / `mid` / `senior` / `lead` / `principal` / `executive` |
| `job_type` | `ENUM` | `full_time` / `part_time` / `contract` / `freelance` / `internship` |
| `is_remote` | `BOOLEAN` | Remote-eligible flag |
| `is_hybrid` | `BOOLEAN` | Hybrid work flag |
| `company_name` | `VARCHAR(512)` | Company name as scraped |
| `city` | `VARCHAR(256)` | Parsed city name |
| `country` | `VARCHAR(100)` | ISO 3166-1 alpha-2 code |
| `salary_min` | `BIGINT` | Minimum salary in smallest currency unit |
| `salary_max` | `BIGINT` | Maximum salary in smallest currency unit |
| `salary_currency` | `VARCHAR(10)` | ISO 4217 code (e.g. `USD`, `INR`) |
| `salary_period` | `VARCHAR(20)` | `yearly` / `monthly` / `hourly` |
| `salary_raw` | `VARCHAR(256)` | Original salary string before parsing |
| `skills` | `TEXT[]` | Skills array — scraper tags + NLP pipeline output |
| `posted_at` | `TIMESTAMPTZ` | When posted on the source platform |
| `scraped_at` | `TIMESTAMPTZ` | When our scraper collected this record |
| `is_skills_extracted` | `BOOLEAN` | `true` after spaCy NLP has processed this job |
| `raw_metadata` | `JSONB` | Extra scraped fields (benefits, applicant count, etc.) |
| `quality_score` | `FLOAT` | 0.0–1.0 record completeness score |

**Indexes:** GIN on `skills` / GIN on `raw_metadata` / composite B-tree on `(city, country, posted_at)` / composite B-tree on `(experience_level, posted_at)` / composite B-tree on `(company_name, posted_at)` / partial `(scraped_at) WHERE is_skills_extracted = false`

---

### Table: `skill_trends`

Pre-aggregated skill demand metrics. Written by the ML pipeline.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT` PK | Auto-increment key |
| `skill_name` | `VARCHAR(100)` | Normalized skill name (e.g. `python`, `react`) |
| `skill_category` | `VARCHAR(100)` | `language`, `framework`, `cloud`, `database`, etc. |
| `period_start` | `TIMESTAMPTZ` | Window start (inclusive) |
| `period_end` | `TIMESTAMPTZ` | Window end (exclusive) |
| `granularity` | `VARCHAR(20)` | `daily` / `weekly` / `monthly` |
| `country` | `VARCHAR(100)` | ISO code; `NULL` = global aggregate |
| `job_count` | `INTEGER` | Jobs mentioning this skill in this period |
| `job_count_change` | `INTEGER` | Delta vs. prior equivalent period |
| `job_count_change_pct` | `FLOAT` | Percentage change from prior period |
| `trend_direction` | `ENUM` | `rising` / `stable` / `declining` / `new` |
| `avg_salary_min` | `BIGINT` | Average minimum salary for this skill |
| `avg_salary_max` | `BIGINT` | Average maximum salary |
| `co_occurring_skills` | `JSONB` | `{"docker": 0.73, "aws": 0.61}` — co-occurrence map |
| `computed_at` | `TIMESTAMPTZ` | When the ML pipeline wrote this row |

**Unique constraint:** `(skill_name, period_start, period_end, granularity, country, city)` — enables idempotent upserts.

---

### Table: `scrape_runs`

Audit log. One row per scraper execution.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `BIGINT` PK | Auto-increment key |
| `platform` | `VARCHAR(100)` | `remoteok` or `greenhouse` |
| `status` | `VARCHAR(50)` | `running` / `completed` / `failed` / `partial` |
| `started_at` | `TIMESTAMPTZ` | Scraper start time |
| `completed_at` | `TIMESTAMPTZ` | Scraper finish time (null if still running) |
| `jobs_found` | `INTEGER` | Total jobs returned by the source API |
| `jobs_inserted` | `INTEGER` | New rows added to the `jobs` table |
| `jobs_skipped_duplicate` | `INTEGER` | Rejected by `ON CONFLICT DO NOTHING` |
| `jobs_failed_parsing` | `INTEGER` | Failed during cleaning or normalization |
| `error_message` | `TEXT` | Error detail when `status = failed` |
| `config_snapshot` | `JSONB` | Search parameters used in this run |

---

## 15. Future Roadmap

Items planned for **Version 2**:

| Priority | Item | Description |
|----------|------|-------------|
| High | **Trend API endpoint** | Expose `GET /api/v1/stats/skill-trends` — the `skill_trends` table is fully populated but has no API route yet |
| High | **Automated pipeline scheduling** | Add APScheduler or a cron expression to `ml/scheduler.py` so trend data refreshes without manual CLI invocation |
| High | **Salary page pagination** | Remove the 100-job cap in `_load_jobs_with_salary()` for statistically accurate aggregations at scale |
| Medium | **Additional data sources** | LinkedIn Easy Apply API or Indeed for broader coverage and cross-platform skill comparison |
| Medium | **Trend line chart** | Time-series visualization of Python/React/Kubernetes demand over 6 months using pre-aggregated `skill_trends` data |
| Medium | **Skill correlation network** | Interactive graph built from `co_occurring_skills` JSONB to show how skills cluster together |
| Medium | **Remove dead code** | Delete empty stubs: `api/services/`, `scraper/bs4_parser.py`, `scraper/parser.py`, `dashboard/components/` |
| Low | **Live currency conversion** | Replace static `USD_CONVERSION_RATES` in `ml/constants.py` with a live exchange rate API |
| Low | **Geography chart** | Wire up `charts.pie_jobs_by_country()` (already implemented but unused) to the Overview page |
| Low | **Test coverage** | Integration tests for all 6 API endpoints; unit tests for `crud.get_jobs()` filter logic |

---

## 16. Screenshots

### Overview Page
![Overview Dashboard](screenshots/overview.png)

### Skills Analytics Page
![Skills Dashboard](screenshots/skills.png)

### Salary Intelligence Page
![Salary Dashboard](screenshots/salary.png)

### Job Explorer Page
![Job Explorer](screenshots/jobs.png)

### FastAPI Swagger UI
![FastAPI Swagger UI](screenshots/swagger.png)

---

## 17. License

This project is licensed under the [MIT License](LICENSE).

---

## Credits

- Job data sourced from [RemoteOK](https://remoteok.com/api) and the [Greenhouse Board API](https://developers.greenhouse.io/job-board.html) — both public, unauthenticated JSON endpoints
- NLP powered by [spaCy](https://spacy.io/) `en_core_web_sm`
- Dashboard built with [Streamlit](https://streamlit.io/) and [Plotly](https://plotly.com/python/)
- API framework: [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/)

> Per RemoteOK API terms: any interface displaying RemoteOK job data must credit **Remote OK** as the source and link back with a follow (non-nofollow) link.

---

*Built as a portfolio project demonstrating end-to-end data engineering, NLP, REST API design, and analytics dashboard development.*
