# GitHub PR Lineage Impact Check

This is the CI/CD contribution of the project: every pull request that touches the
data pipeline is automatically analysed, and a bot posts (and keeps updated) a
**Data Lineage Impact Report** comment listing exactly which tables, views, columns,
and stored-procedure outputs change — and which existing downstream nodes may be
affected.

Lineage stops being a stale document and becomes a live signal in code review. This
is the mechanism that operationalises BCBS 239 / SOX-style "know your data flow"
requirements: a reviewer sees the blast radius of a change *before* merging.

---

## What it does

On every PR that modifies `sample_repo_sql/`, `sample_repo_python/`, `sample_repo_adf/`,
or the analysis code itself, the workflow [`.github/workflows/lineage-check.yml`](../.github/workflows/lineage-check.yml):

1. Builds the **full** multi-repo lineage graph at the PR **base** (from a temporary
   `git worktree`) and at the PR **head** (from the checked-out tree).
2. Diffs the two graphs (added / removed / modified nodes and edges).
3. Traces every changed node **downstream** to the consumers that may be affected —
   across SQL → Python → SQL and through stored procedures.
4. Renders a Markdown report and posts it as a single PR comment, updating that same
   comment on every push (via `peter-evans/find-comment` + `create-or-update-comment`).

The check is **informational only** (`continue-on-error: true`) — it never blocks a
merge. A POC should surface risk, not gate work on a tool that can have false positives.

---

## One-time setup

The repository is already on GitHub. The only thing the maintainer must do is add the
OpenAI key as a repository secret so the LLM fallback (Python + stored procedures) works
in CI:

```bash
# With the GitHub CLI (recommended — the key never lands in shell history files):
gh secret set OPENAI_API_KEY --repo ShanonSamora/data-lineage-poc
# …then paste the key when prompted.
```

Or via the web UI: **Settings → Secrets and variables → Actions → New repository secret**,
name `OPENAI_API_KEY`.

No other secret is needed — the workflow authenticates the comment with the built-in
`GITHUB_TOKEN`, which is granted `pull-requests: write` in the workflow's `permissions`
block.

> **Without the key** the workflow still runs: deterministic SQL and ADF lineage are
> produced normally, and only Python / stored-procedure lineage (the LLM part) is
> skipped. The report is still posted.

---

## How to trigger it (the demo)

1. Create a branch and make a small, meaningful change to one data file, e.g. round a
   currency column in [`sample_repo_sql/02_intermediate_views.sql`](../sample_repo_sql/02_intermediate_views.sql):

   ```sql
   -- daily_net_amount_usd: wrap the SUM in ROUND(..., 2)
   ROUND(SUM(CASE WHEN t.transaction_type = 'CREDIT' THEN t.amount * er.rate ... END), 2)
   ```
2. Push the branch and open a PR (`gh pr create --fill`).
3. Within ~1–2 minutes the **github-actions** bot posts the report. Push another commit
   and watch it **update the same comment** instead of adding a new one.

### Example report

A one-line `ROUND(...)` change to `daily_net_amount_usd` produces:

```markdown
## Data Lineage Impact Report

🔎 **15 downstream nodes** may be affected by changes to **1 file**.

| Metric | Count |
|--------|------:|
| Edges modified | 3 |
| Downstream nodes impacted | 15 |

### Downstream Impact
- rpt_customer_exposure.daily_net_amount_usd
- rpt_customer_exposure.cumulative_exposure_usd
- int_python_customer_scores.risk_score        ← Python pipeline
- rpt_customer_scorecard.risk_score
- rpt_monthly_summary.net_position_usd          ← stored procedure
- … (15 total)
```

The impact list correctly follows data **downstream** (the consumers of the changed
column) and crosses language boundaries — SQL view → Python pandas → SQL report → stored
procedure.

---

## Reproduce locally

The CI step is just the CLI; you can run the exact same analysis on your machine:

```bash
python main.py --pr-check \
  --base main --head HEAD \
  --repo sample_repo_sql --repo sample_repo_python --repo sample_repo_adf \
  --output-file lineage-report.md
```

`--base`/`--head` accept any Git ref or SHA. The base graph is materialised from a
temporary `git worktree`, so your working tree is never touched.

---

## How correctness is kept (design notes)

Three properties make the diff trustworthy rather than noisy:

- **Repo-relative FILE IDs.** FILE nodes and `DEFINED_IN` edges are identified by their
  path *relative to the repo root* (`sample_repo_sql/02_intermediate_views.sql`), not an
  absolute path. The base graph is built from a temp worktree with a different absolute
  prefix; without normalisation **every** file node would differ between base and head and
  flood the report with phantom add/remove rows. See [`src/paths.py`](../src/paths.py).
- **Content-hash analysis cache.** During a PR check the base and head graphs share a
  per-run cache keyed by file content. An unchanged file is analysed once and reused, so
  the LLM is not re-run on it — eliminating phantom "modified" edges that would otherwise
  appear purely because the model paraphrases a transformation differently on a second
  call. See `FileCache` in [`src/engine.py`](../src/engine.py).
- **Correct downstream direction.** "Impact" follows edges to the *consumers* of a changed
  node (what derives from it), mirroring the `/impact` API traversal — not its inputs. See
  `collect_impacted_nodes` in [`src/diff.py`](../src/diff.py).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Comment says *"⚠️ Analysis failed"* | A parser/LLM exception or OpenAI timeout. The workflow posts a degraded report instead of failing. Re-run the job; deterministic SQL/ADF lineage doesn't need the LLM, so the failure usually points to a Python file or stored procedure. |
| No Python/stored-proc lineage in the report | `OPENAI_API_KEY` secret is missing or a placeholder. Add it (see setup). |
| No comment appears at all | The PR didn't touch a watched path, **or** it's from a **fork** — `pull_request` from forks gets a read-only token, so the bot can't comment. For the thesis demo, use a branch in this repo (not a fork). |
| Want to re-run | Push an empty commit (`git commit --allow-empty`) or use **Re-run jobs** in the Actions tab. |

---

## Limitations (honest scope)

- Fork PRs can't be commented on (read-only token) — same-repo branches only.
- The LLM step is the only non-deterministic part; the content-hash cache removes its
  effect on *unchanged* files, but a genuinely changed Python file is re-analysed and its
  transformation prose may vary slightly run-to-run. The column-level edges themselves are
  stable; only the free-text description can differ.
- The report lists impacted node IDs; it does not (yet) render the visual graph in the PR.
