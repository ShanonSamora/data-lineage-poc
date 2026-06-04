# Demo Explanation: Sample Repos + Deterministic Parsing

This file is a short, demo-ready explanation of what the sample repos contain and how the lineage engine works.

## What the sample repos represent

The POC ships three sibling repos — `sample_repo_sql/`, `sample_repo_python/`, `sample_repo_adf/` — that simulate the multi-repo case of a small data warehouse with realistic layers and tooling:

1) Staging layer (raw ingestion)
- SQL file: sample_repo_sql/01_staging_tables.sql
- Tables like stg_customers, stg_accounts, stg_transactions, stg_exchange_rates, stg_branches
- These represent raw data arriving from upstream systems

2) Intermediate layer (business logic)
- SQL file: sample_repo_sql/02_intermediate_views.sql
- Views like int_customers, int_daily_balances, int_transaction_risk
- These apply joins, filters, aggregations, and rules

3) Reporting layer (final outputs)
- SQL file: sample_repo_sql/03_reporting_tables.sql
- Outputs like rpt_customer_exposure and rpt_regional_risk_summary
- These are the tables/views used by dashboards and reports

4) Stored procedures (procedural SQL)
- SQL file: sample_repo_sql/04_stored_procedures.sql
- Example procedure with variables, control flow, and multi-step logic
- These are hard to parse deterministically and demonstrate LLM fallback

5) ADF pipelines and datasets (JSON)
- Pipelines: sample_repo_adf/pipeline/*
- Datasets: sample_repo_adf/dataset/*
- These model ingestion and orchestration in Azure Data Factory

6) Python transformations
- Python file: sample_repo_python/transform_pipeline.py
- Pandas transforms that read the int_* views and write int_python_customer_scores
- This shows why LLM fallback is needed for non-SQL logic

## What the demo graph shows

- Nodes = tables, views, columns, files, and ADF assets
- Edges = relationships like READS_FROM, WRITES_TO, HAS_COLUMN, DEFINED_IN, DERIVES_FROM
- The graph answers:
  - Where does this column come from?
  - What depends on this table or column?

## How deterministic parsing works

Deterministic parsing uses sqlglot to parse SQL into an AST and then extracts lineage rules.

Key behaviors:
- CREATE TABLE ... AS SELECT and CREATE VIEW ... AS SELECT
  - The target table/view becomes a node
  - Source tables from FROM/JOIN become upstream nodes
  - Output columns are mapped to source columns referenced in each SELECT expression
- INSERT INTO ... SELECT
  - Similar lineage extraction from the SELECT clause
- Plain CREATE TABLE (column definitions)
  - Creates table + column nodes and HAS_COLUMN edges

Because this is deterministic, the results are confidence = 1.0.

## When it falls back to the LLM

The engine switches to LLM inference when deterministic parsing cannot reliably recover lineage.

Triggers include:
- Stored procedures or functions with procedural SQL (BEGIN/DECLARE/END)
- Dynamic SQL (string-built queries)
- Python/Pandas transformations
- SQL files where deterministic parsing yields no column-level lineage

LLM results are tagged with confidence < 1.0 (default 0.85) and marked as LLM-derived.

## Simple demo script (60-90 seconds)

1) Click Analyze
2) Search for a report field (example: rpt_customer_exposure.risk_level)
3) Show upstream lineage to explain where it comes from
4) Mention the hybrid approach: deterministic parsing for standard SQL, LLM for complex cases
5) Explain the value: column-level lineage without running pipelines

## One-line value statement

This tool builds column-level lineage directly from source code so we can explain where each metric comes from and what changes will impact it, without executing any data pipelines.
