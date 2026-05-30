# Demo Script — Data Lineage POC

A 7-10 minute live walkthrough for the thesis defence. Each step has talking points to surface what's interesting about the system.

---

## Pre-flight (do this before the audience arrives)

```bash
# Make sure dependencies are installed and OPENAI_API_KEY is set
pip install -e .
cp .env.example .env  # then edit and add OPENAI_API_KEY=sk-...

# Verify install — analyses the three sibling sample repos (sql / python / adf)
python -c "from src.engine import analyze_multiple_repos as a; g=a(); print(len(g.nodes),'nodes,',len(g.edges),'edges')"
# Prints ~140 nodes / ~280 edges. Note the exact numbers — the Analyze toast shows the same.

# Run the test suite to confirm everything green
pytest -q
# Should print: 75 passed
```

If anything fails, do **not** start the demo. Fix first.

---

## Step 1 — Open the project and frame the problem (1 min)

```bash
# In one terminal:
python main.py
```

Open `http://localhost:8000` in the browser.

**Talking points:**
- "This is a hybrid static lineage tool: I take a repository, never execute the code, and reconstruct the column-level data flow."
- "The sample repo I'm about to analyse simulates a financial data warehouse — five technologies in one pipeline: ADF orchestration, SQL DDL, SQL views, a Python pandas transform, and a stored procedure."

---

## Step 2 — Run the analysis (30 s)

Click **Analyze**.

Watch the toast: "Done: ~140 nodes, ~280 edges".

**Talking points:**
- "Took under 5 seconds. The LLM was invoked for the Python file and the stored procedure — everything else was parsed deterministically with sqlglot."
- "75 automated tests guarantee these numbers are reproducible. The validation report in `docs/validation.md` documents the LLM-edge precision; deterministic edges are correct by construction."

---

## Step 3 — Show the Flow View (1 min)

The Flow View should auto-load with a table selected (e.g. `int_customers`). If not, click any table in the left catalog.

Click **Focus this table →** on `rpt_customer_exposure` (in the catalog).

**Talking points:**
- "This is a Databricks-style three-column layout: upstream, selected, downstream."
- "Each card shows the table's columns. Purple-highlighted columns are the ones that participate in lineage with the selected table."
- "The SVG curves connect specific source columns to target columns. Solid indigo = deterministic SQL parser. Dashed amber = LLM-inferred."

---

## Step 4 — Trace cross-language lineage (2 min)

Stay on `rpt_customer_exposure`. Point at the `python_risk_score` column.

In the right sidebar, click **↑ Upstream** on the column `python_risk_score`.

**Talking points:**
- "`python_risk_score` is computed inside the Python pipeline — there's no SQL that defines it."
- "But the lineage still traces upstream: it goes through `int_python_customer_scores.risk_score` (the Python output), which derives from `balance_volatility_usd`, `high_risk_txn_count`, etc., which in turn derive from `int_daily_balances` and `int_transaction_risk`, which in turn derive from `stg_transactions`."
- "Five tables across SQL views, Python pandas, and the staging layer — all traceable from a single column click."

Click any column in `stg_customers` (e.g. `first_name`) and click **↓ Downstream**.

**Talking points:**
- "A change to `stg_customers.first_name` affects 5 downstream columns: `int_customers.full_name`, `int_python_customer_scores.full_name`, `rpt_customer_exposure.full_name`, `rpt_customer_scorecard.full_name`, and `rpt_monthly_summary.full_name`. The last one is computed inside a stored procedure — captured by the LLM."

---

## Step 5 — Show the full graph view (1 min)

Click **Graph** in the toolbar.

**Talking points:**
- "vis.js network. Useful for the high-level shape of the pipeline."
- "You can see ADF pipelines (blue boxes top-left) orchestrating everything, the Blob source files, the SQL staging/intermediate/reporting layers, and the Python output in the middle."

Click on any column node (small purple dot).

**Talking points:**
- "Even individual columns are first-class nodes in the graph. This is what enables column-level impact analysis, which is the explicit requirement of BCBS 239 for risk reporting."

---

## Step 6 — Download the JSON (1 min)

Click **Download JSON** in the toolbar.

Open the downloaded file in an editor or paste a section.

**Talking points:**
- "The whole graph is downloadable as JSON: nodes, edges, plus enriched per-node info — direct connections, full upstream chain, full downstream chain, and column list for each table."
- "This is the format that integrates with downstream tools — catalogs, BI, dashboards."

---

## Step 7 — PR impact check (the CI/CD contribution, 2 min)

This is the headline contribution. Open the **real PR on GitHub** (have the tab ready):
the demo PR that rounds `daily_net_amount_usd`, where the **github-actions bot** has
posted a *Data Lineage Impact Report* comment. See [`docs/github-pr-flow.md`](docs/github-pr-flow.md).

**Talking points:**
- "A one-line `ROUND(...)` change to a single SQL column. The bot automatically reports
  **15 downstream nodes** that may be affected — and nothing else. No noise."
- "Crucially, the impact is *downstream* and *cross-language*: it reaches
  `rpt_customer_exposure.cumulative_exposure_usd`, the Python-computed
  `int_python_customer_scores.risk_score`, and `rpt_monthly_summary.net_position_usd`
  which is produced inside a **stored procedure**. SQL → Python → SQL, traced automatically."
- "This is the CI/CD contribution: lineage stops being a stale artifact and becomes a live
  signal on every pull request — exactly what BCBS 239 asks for in change management."

You can reproduce the same report locally in a terminal:

```bash
python main.py --pr-check --base main --head HEAD \
  --repo sample_repo_sql --repo sample_repo_python --repo sample_repo_adf
```

**Under the hood (if asked):** the base graph is built from a temporary `git worktree`,
FILE IDs are repo-relative so the base/head diff has zero path noise, and an unchanged
file is analysed once (shared content cache) so the LLM never produces phantom diffs.

---

## Step 8 — Summary and Q&A (30 s)

Quick mental recap:
- Static analysis, no code execution
- Five technologies linked end-to-end (ADF / SQL DDL / SQL views / Python / stored proc)
- Hybrid: deterministic edges correct by construction, LLM adds cross-language coverage (see `docs/validation.md`)
- 75 automated tests guarantee reproducibility
- CI/CD integration: live, accurate PR impact reports (see `docs/github-pr-flow.md`)

---

## Backup plans

- **If the server doesn't start**: open `docs/validation.md` and walk through the metrics tables instead. The numbers are the same.
- **If `Analyze` returns 0 nodes**: check `REPO_PATH` in `.env` (should be `./sample_repo`), then click Analyze again.
- **If LLM is unreachable**: the deterministic part still works. Mention the degradation explicitly and proceed — the SQL/ADF lineage is the bulk of the graph anyway.
- **If a column click crashes**: refresh, click Analyze, retry. Worst case, open the JSON file directly to show the data is there.

---

## Anticipated questions

> **How do you know the lineage is correct?**

"Two ways. One: 100% of the deterministic edges (sqlglot output) are correct by construction — they're derived from the SQL AST directly. Two: for LLM edges, I manually verified all 37 of them on the sample repo and documented the result in `docs/validation.md`: 30 correct, 6 noise from intermediate pandas variables, 1 false-positive direction error. 81% precision."

> **What does the LLM actually contribute?**

"Three things the deterministic parser cannot handle: Python pandas transformations (no AST library can reliably extract column-level lineage from `df.groupby().agg()` calls), stored procedures with PL/pgSQL blocks, and dynamic SQL built via string concatenation. On the sample repo, the LLM adds 21 column-level DERIVES_FROM edges and 36 column nodes that the deterministic parser couldn't extract — a 33% increase in column lineage coverage."

> **What about cost?**

"GPT-4.1-mini at ~$0.0001 per file in this sample. The trigger keywords are regex-tight to avoid spurious calls — empirically, only 2 of the 4 SQL files trigger the LLM, plus the Python file. Total cost per full analysis: under $0.001."

> **Why did you remove Neo4j?**

"The Neo4j-backed version worked but required Docker and an external database for every analysis run. For a POC and for the GitHub Action use case, in-memory analysis is faster, simpler, and stateless. The plan section 8 of Future Work proposes restoring an optional persistent backend for productive deployments."

> **Could this scale to a real organization's repos?**

"Yes, with two caveats. First, the LLM cost scales linearly with the number of Python and stored-procedure files — fine for under 10K files. Second, the in-memory graph would need to be replaced with a persistent backend, as Future Work item 8 describes. Architecturally, the parser/engine separation makes that swap mechanical."
