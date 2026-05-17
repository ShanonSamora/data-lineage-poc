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
│              Source Code Repositories (multi-repo)              │
│   .sql files    .py files    ADF JSON    stored procs           │
└──────────────────────┬──────────────────────────────────────────┘
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
              │   (in-memory)   │
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

### 2. Verify install

```bash
# Should print ~152 nodes, ~248 edges
python -c "from src.engine import analyze_directory; g = analyze_directory('sample_repo'); print(len(g.nodes), 'nodes,', len(g.edges), 'edges')"

# Run the test suite (~63 tests, all should pass)
pytest -q
```

### 3. Run the server

```bash
# Optional: enable LLM fallback for Python and stored procedures
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

python main.py
```

Open **http://localhost:8000** → click **"Analyze"** → explore the graph.

No external database required — the graph lives in memory. Without `OPENAI_API_KEY` set, deterministic SQL/ADF lineage still works fully, but Python files and stored procedures are skipped. The server logs a warning at startup if the key is missing.

### 4. CLI Mode (no server)

```bash
# Print full lineage as JSON to stdout
python main.py --analyze

# Multi-repo analysis
python main.py --analyze-multi

# PR impact check (requires Git repo)
python main.py --pr-check --base origin/main --head HEAD
```

### 5. Live demo

See [DEMO.md](DEMO.md) for a 7-10 minute walkthrough script.

---

## Project Structure

```
data-lineage-poc/
├── main.py                  # Entry point (server or CLI)
├── pyproject.toml           # Dependencies and project metadata
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
│   ├── diff.py              # Graph diff engine (before/after comparison)
│   ├── pr_analyzer.py       # PR lineage impact analyzer
│   └── templates/
│       └── index.html       # Web UI (single-page, Flow View + vis.js)
│
├── sample_repo/             # Example data warehouse to analyze
│   ├── sql/
│   │   ├── 01_staging_tables.sql       # Raw source tables (stg_*)
│   │   ├── 02_intermediate_views.sql   # Business logic layer (int_*)
│   │   ├── 03_reporting_tables.sql     # Reporting mart (rpt_*)
│   │   └── 04_stored_procedures.sql    # Stored proc (LLM-only)
│   ├── python/
│   │   └── transform_pipeline.py       # Pandas pipeline (reads stg_*, writes rpt_*)
│   └── adf/                            # Azure Data Factory artifacts
│       ├── pipeline/                   # ADF pipeline definitions
│       ├── dataset/                    # ADF dataset definitions
│       └── dataflow/                   # ADF dataflow definitions
│
├── tests/                   # Automated test suite
│   ├── test_diff.py         # Graph diff engine
│   ├── test_pr_analyzer.py  # PR impact analysis
│   └── test_parser_adf.py   # ADF parser
│
├── .github/workflows/
│   └── lineage-check.yml    # GitHub Action: auto PR lineage check
│
└── docs/
    └── data-lineage.md      # Research paper
```

---

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/analyze` | Scan repo → extract lineage → store in memory |
| `POST` | `/analyze-multi` | Multi-repo analysis |
| `POST` | `/pr-check` | PR lineage impact analysis (Git diff) |
| `GET` | `/graph` | Full lineage graph |
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
| `OPENAI_API_KEY` | *(empty)* | OpenAI API key for LLM fallback |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Model for code analysis |
| `REPO_PATH` | `./sample_repo` | Directory to analyze |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |

### LLM Model Choice

The project uses **GPT-4.1-mini** by default. Rationale:

- Best code comprehension in its pricing tier for structured extraction tasks
- Native JSON output adherence — critical for parsing code into lineage graphs
- ~$0.40/1M input tokens (vs $2.50 for GPT-4.1, $10 for Claude Sonnet)

To swap models, set `OPENAI_MODEL` in `.env` (any OpenAI-compatible model works).

---

## Graph Model

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

The engine recursively scans for `.sql`, `.ddl`, `.dml`, `.hql`, `.py`, `.pyspark` files, and ADF `.json` files.

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| SQL Parser | [sqlglot](https://github.com/tobymao/sqlglot) | Deterministic column-level lineage from SQL |
| LLM | OpenAI GPT-4.1-mini | Interpret stored procs, dynamic SQL, Python |
| API | [FastAPI](https://fastapi.tiangolo.com/) | REST endpoints |
| Web UI | Custom Flow View + [vis.js Network](https://visjs.github.io/vis-network/) | Interactive lineage visualization |
| Config | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Type-safe configuration |
| Git | [GitPython](https://gitpython.readthedocs.io/) | PR diff analysis |
| CI/CD | [GitHub Actions](https://docs.github.com/en/actions) | Automatic PR lineage checks |
| Testing | [pytest](https://docs.pytest.org/) | Automated tests |
