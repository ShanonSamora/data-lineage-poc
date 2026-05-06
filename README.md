# Data Lineage POC

**Static Data Lineage with AI** — Automatic column-level lineage reconstruction by analyzing source code, no pipeline execution required.

> Feed it your SQL scripts and Python pipelines. It traces every column back to its origin and shows you the full dependency graph.

---

## Why This Exists

If you've ever had to answer *"where does this number come from?"* by manually tracing through dozens of SQL scripts, views, and Python notebooks — this tool automates that.

| Problem | How This Solves It |
|---|---|
| Manual lineage docs get stale the moment code changes | Lineage is extracted directly from source code — always current |
| Commercial tools (Collibra, Atlan) cost $100K–$500K/year | Self-hosted, open-source stack |
| Runtime lineage tools (OpenLineage, dbt) miss legacy SQL, notebooks, stored procs | Static analysis works on any code, no execution needed |
| Pure SQL parsers can't handle dynamic queries or Python transformations | Hybrid approach: deterministic parser + LLM fallback |

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│              Source Code Repositories (multi-repo)             │
│  .sql files    .py files    ADF JSON    stored procs          │
└──────────────────────┬────────────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │  Hybrid Engine  │
              └────────┬────────┘
                       │
       ┌───────────────┼────────────────┐
       │               │                │
┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼───────┐
│  sqlglot    │  │ ADF Parser │  │ LLM (GPT-4.1)│
│ determin.   │  │ determin.  │  │   fallback   │
│             │  │            │  │              │
│• CREATE/VIEW│  │• Pipelines │  │• stored procs│
│• INSERT..SEL│  │• Datasets  │  │• dynamic SQL │
│• JOINs/CTEs │  │• Dataflows │  │• Python/Panda│
│ conf = 1.0  │  │ conf = 1.0 │  │ conf = 0.85  │
└──────┬──────┘  └─────┬──────┘  └──────┬───────┘
       │               │                │
       └───────────────┼────────────────┘
                       │
              ┌────────▼────────┐
              │  Lineage Graph  │──── REST API ──── Web UI
              │  (Neo4j / mem)  │                  (vis.js)
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  CI/CD          │──── GitHub Actions
              │  PR Impact      │──── auto-comment on PRs
              └─────────────────┘
```

**The key insight:** standard SQL (CREATE, INSERT...SELECT, views) is parsed deterministically with `sqlglot` at 100% confidence. Complex cases (stored procedures, dynamic SQL, Python/Pandas transformations) fall back to an LLM that returns structured lineage at ~85% confidence. Both results merge into a single graph.

---

## Quick Start

### 1. Install

```bash
# Python 3.11+
git clone <repo-url>
cd data-lineage-poc
pip install -e .
```

### 2. Run (No Neo4j, No API Key needed)

The fastest way to see it work — analyzes the included `sample_repo/` and serves the web UI:

```bash
python main.py
```

Open **http://localhost:8000** → click **"Analyze (Local)"** → explore the graph.

This mode uses in-memory storage. No external dependencies required.

### 3. Run with Neo4j (persistent storage, graph queries)

```bash
# Start Neo4j
docker compose up -d

# Copy and configure environment
cp .env.example .env
# Edit .env → set OPENAI_API_KEY if you want LLM fallback

# Start server
python main.py
```

Open **http://localhost:8000** → click **"Analyze (Neo4j)"**.

Neo4j Browser available at **http://localhost:7474** (user: `neo4j`, password: `lineage-poc-2024`).

### 4. CLI Mode (no server)

```bash
# Print lineage JSON to stdout (no Neo4j)
python main.py --analyze-local

# Analyze and store in Neo4j
python main.py --analyze

# Multi-repo analysis
python main.py --analyze-multi-local

# PR impact check (requires Git repo)
python main.py --pr-check --base-ref origin/main --head-ref HEAD
```

---

## Project Structure

```
data-lineage-poc/
├── main.py                  # Entry point (server or CLI)
├── pyproject.toml           # Dependencies and project metadata
├── docker-compose.yml       # Neo4j container
├── .env.example             # Configuration template
│
├── src/
│   ├── api.py               # FastAPI REST API + web UI routes
│   ├── config.py            # Settings from environment / .env
│   ├── engine.py            # Hybrid lineage engine (orchestrator)
│   ├── models.py            # Pydantic models: nodes, edges, graph
│   ├── parser_sql.py        # Deterministic SQL parser (sqlglot)
│   ├── parser_llm.py        # LLM fallback parser (OpenAI)
│   ├── parser_adf.py        # Azure Data Factory JSON parser
│   ├── graph_store.py       # Neo4j persistence and queries
│   ├── diff.py              # Graph diff engine (before/after comparison)
│   ├── pr_analyzer.py       # PR lineage impact analyzer
│   └── templates/
│       └── index.html       # Web UI (single-page, vis.js graph)
│
├── sample_repo/             # Example data warehouse to analyze
│   ├── sql/
│   │   ├── 01_staging_tables.sql       # Raw source tables (stg_*)
│   │   ├── 02_intermediate_views.sql   # Business logic layer (int_*)
│   │   ├── 03_reporting_tables.sql     # Reporting mart (rpt_*)
│   │   └── 04_stored_procedures.sql    # Stored proc (LLM-only)
│   ├── python/
│   │   └── transform_pipeline.py       # Pandas pipeline (LLM-only)
│   └── adf/                            # Azure Data Factory artifacts
│       ├── pipeline/                   # ADF pipeline definitions
│       ├── dataset/                    # ADF dataset definitions
│       └── dataflow/                   # ADF dataflow definitions
│
├── tests/                   # Automated test suite (23 tests)
│   ├── test_diff.py         # 9 tests for graph diff engine
│   ├── test_pr_analyzer.py  # 5 tests for PR impact analysis
│   └── test_parser_adf.py   # 9 tests for ADF parser
│
├── .github/workflows/
│   └── lineage-check.yml    # GitHub Action: auto PR lineage check
│
└── docs/
    └── data-lineage.md      # Research paper
```

## Sample Data Pipeline

The included `sample_repo/` simulates a financial data warehouse with three layers:

```
STAGING (raw sources)          INTERMEDIATE (business logic)      REPORTING (dashboards)
─────────────────────          ─────────────────────────────      ──────────────────────
stg_customers          ──┐
stg_accounts           ──┼──►  int_customers              ──┐
stg_branches           ──┘                                   ├──►  rpt_customer_exposure
stg_transactions       ──┬──►  int_daily_balances          ──┤
stg_exchange_rates     ──┘                                   ├──►  rpt_regional_risk_summary
stg_transactions       ──────► int_transaction_risk        ──┘
                                                               ┌──  rpt_monthly_summary
stg_transactions + int_* ──────► sp_refresh_monthly_summary ──┘    (stored proc → LLM)
```

The full sample repo (SQL + Python + ADF) produces **98 nodes** and **110 edges** from deterministic parsing alone. With the LLM enabled, the stored procedure and Python file add more coverage.

---

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/analyze` | Scan repo → extract lineage → store in Neo4j |
| `POST` | `/analyze-local` | Scan repo → extract lineage → store in memory |
| `POST` | `/analyze-multi` | Multi-repo analysis → store in Neo4j |
| `POST` | `/pr-check` | PR lineage impact analysis (Git diff) |
| `GET` | `/graph` | Full lineage graph from Neo4j |
| `GET` | `/graph-local` | Full lineage graph from memory |
| `GET` | `/upstream/{node_id}` | Trace where a column/table gets its data from |
| `GET` | `/downstream/{node_id}` | Trace what depends on a column/table |
| `GET` | `/impact/{node_id}` | Impact analysis: what breaks if this changes? |
| `GET` | `/search?q=keyword` | Search nodes by name |
| `GET` | `/` | Web UI |

### Example: "Where does `rpt_customer_exposure.risk_level` come from?"

```bash
curl http://localhost:8000/upstream/rpt_customer_exposure.risk_level
```

### Example: "What breaks if I change `stg_transactions.amount`?"

```bash
curl http://localhost:8000/impact/stg_transactions.amount
```

---

## Configuration

All settings are loaded from environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection string |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `lineage-poc-2024` | Neo4j password |
| `OPENAI_API_KEY` | *(empty)* | OpenAI API key for LLM fallback |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Model for code analysis |
| `REPO_PATH` | `./sample_repo` | Directory to analyze |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |

### LLM Model Choice

The project uses **GPT-4.1-mini** by default. Rationale:

- Best code comprehension in its pricing tier for structured extraction tasks
- Native JSON output adherence — critical for parsing code into lineage graphs
- Same OpenAI SDK already in the dependency tree
- ~$0.40/1M input tokens (vs $2.50 for GPT-4.1, $10 for Claude Sonnet)

To swap models, set `OPENAI_MODEL` in `.env` (any OpenAI-compatible model works).

---

## Graph Model

Nodes and edges stored in Neo4j (or in-memory):

**Node Types:**
| Type | Description | Example |
|------|-------------|---------|
| `TABLE` | Physical table | `stg_customers` |
| `VIEW` | SQL view | `int_customers` |
| `COLUMN` | Column (belongs to table/view) | `stg_customers.email` |
| `PROCEDURE` | Stored procedure | `sp_refresh_monthly_summary` |
| `PYTHON_FUNCTION` | Python function/pipeline | `compute_customer_summary` |
| `FILE` | Source file | `01_staging_tables.sql` |
| `ADF_PIPELINE` | Azure Data Factory pipeline | `IngestCustomerData` |
| `ADF_DATASET` | Azure Data Factory dataset | `BlobCustomersCSV` |
| `ADF_DATAFLOW` | Azure Data Factory dataflow | `CustomerMetricsFlow` |

**Edge Types:**
| Type | Direction | Meaning |
|------|-----------|----------|
| `DERIVES_FROM` | target_col → source_col | Column-level lineage |
| `READS_FROM` | view/proc → table | Data dependency |
| `WRITES_TO` | proc → table | Write target |
| `HAS_COLUMN` | table → column | Schema relationship |
| `DEFINED_IN` | asset → file | Source code location |
| `COPIES_TO` | dataset → dataset | ADF copy activity |
| `TRIGGERS` | pipeline → pipeline | ADF pipeline execution |

Each edge has a `confidence` score: `1.0` for deterministic (sqlglot), `0.85` for LLM-inferred.

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Lint
ruff check src/

# Run tests
pytest
```

### Analyzing Your Own Repository

Point `REPO_PATH` to any directory containing `.sql` and/or `.py` files:

```bash
REPO_PATH=/path/to/your/dbt/project python main.py
```

The engine recursively scans for `.sql`, `.ddl`, `.dml`, `.hql`, `.py`, `.pyspark` files, and ADF `.json` files (in `pipeline/`, `dataset/`, `dataflow/` folders).

---

## Demo Script (5-minute walkthrough)

Quick steps to demonstrate the system live:

```bash
# 1. Install (one-time)
pip install -e .

# 2. Start the server
python main.py
```

**In the browser at http://localhost:8000:**

1. Click **"Analyze (Local)"** → see "98 nodes, 110 edges" toast notification
2. The graph renders — tables (blue), views (green), columns (purple), ADF components
3. Click any node → sidebar shows details, connections, confidence scores
4. Click **"↑ Upstream"** on a reporting table → highlights the full data origin chain
5. Click **"↓ Downstream"** on a staging table → shows all affected downstream assets
6. Use the **Search** box → type `customer` → highlights all customer-related nodes

**In the terminal (API demo):**

```bash
# Run analysis and see JSON output
python main.py --analyze-local

# Run tests (23 pass)
python -m pytest tests/ -v
```

**Key talking points for the demo:**
- Deterministic SQL parser extracts column-level lineage from 4 SQL files instantly
- ADF parser covers Azure Data Factory pipelines, datasets, and dataflows
- Hybrid engine: LLM fallback handles Python and complex SQL (when API key is set)
- No execution needed — pure static analysis of source code
- CI/CD integration: GitHub Action auto-comments lineage impact on pull requests

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| SQL Parser | [sqlglot](https://github.com/tobymao/sqlglot) | Deterministic column-level lineage from SQL |
| LLM | OpenAI GPT-4.1-mini | Interpret stored procs, dynamic SQL, Python |
| Graph DB | [Neo4j](https://neo4j.com/) | Store and query lineage as a graph |
| API | [FastAPI](https://fastapi.tiangolo.com/) | REST endpoints |
| Web UI | [vis.js Network](https://visjs.github.io/vis-network/) | Interactive graph visualization |
| Config | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Type-safe configuration |
| Git | [GitPython](https://gitpython.readthedocs.io/) | PR diff analysis |
| CI/CD | [GitHub Actions](https://docs.github.com/en/actions) | Automatic PR lineage checks |
| Testing | [pytest](https://docs.pytest.org/) | 23 automated tests |
