# Demo: The Lineage-Impact Bot — ≈4 min

## Before you walk in

Open these two tabs and leave them ready — the comment is **already posted**, so the demo works even if the room's Wi‑Fi is unreliable.

| Tab | URL |
|-----|-----|
| Files changed | https://github.com/ShanonSamora/data-lineage-poc/pull/3/files |
| Bot comment (Conversation) | https://github.com/ShanonSamora/data-lineage-poc/pull/3 |

---

## Step 1 — Frame the problem (20 s)

> "In a real data warehouse, nobody knows what breaks when you change a column.
> Regulators — BCBS 239, SOX — require you to *know your data flow* before you change it.
> Commercial tools cost six figures. I built this to do it automatically, on every pull request."

---

## Step 2 — Show the change (30 s)

Open the **Files changed** tab. Point at the single edit.

> "This is the whole change — I rounded one column, `daily_net_amount_usd`, to two decimal
> places. One line, in one intermediate SQL view."

---

## Step 3 — Show the automatic impact report (90 s) ← the highlight

Switch to the **Conversation** tab, scroll to the `github-actions` bot comment.

> "The moment I pushed, a GitHub Action analysed the entire repo — **no code was executed**,
> it's pure static analysis — and the bot posted this automatically."

**Point at the headline:**

> 🔎 **15 downstream nodes** may be affected by changes to **1 file**.

**Point at the Downstream Impact list and make the key point:**

> "Look at *what* it found. The change is in SQL, but the impact crosses languages:
>
> - `rpt_customer_exposure.cumulative_exposure_usd` — another **SQL** report
> - `int_python_customer_scores.risk_score` — computed in a **Python pandas** pipeline
> - `rpt_monthly_summary.net_position_usd` — produced inside a **stored procedure**
>
> SQL → Python → stored procedure, traced automatically from a single column.
> That's the hybrid engine: deterministic SQL parsing plus an LLM for the parts a
> parser can't reach."

**Point at the Edge Changes table (the `↳ was` rows):**

> "And it shows the exact before/after of the transformation — full auditability for
> the reviewer, right in the pull request."

---

## Step 4 — Watch it update live *(optional — skip if you don't want to wait on CI)*

In GitHub's web editor on the `demo/round-usd-exposure` branch, change `ROUND(..., 2)` → `ROUND(..., 4)` and commit.
After ~1–2 min, refresh — **the same comment updates in place** (no duplicate comments).

> Skip this step if you'd rather stay on-script; the already-posted comment proves the feature.

---

## Step 5 — Close (20 s)

> "So lineage stops being a stale diagram and becomes a **live signal on every pull request** —
> exactly what change-management compliance asks for, at essentially zero cost."

---

## Anticipated questions

**Q: How do you know it's correct?**

> "Two layers. Deterministic edges from sqlglot are correct by construction — derived
> from the SQL AST. LLM edges were manually verified on the sample repo: 100% precision
> after the pruning rules were tightened. The validation report is in `docs/validation.md`."

**Q: What does the LLM actually add?**

> "Three things a SQL parser can't handle: Python/Pandas transformations, stored
> procedures with procedural logic, and dynamic SQL. On this sample, the LLM adds
> the `risk_score` lineage through the Python pipeline and the stored-procedure outputs —
> the nodes that crossed languages in the impact report you just saw."

**Q: What if the LLM key isn't set?**

> "The deterministic SQL and ADF parsing still runs and posts a partial report.
> The system degrades gracefully — it never blocks the pull request."
