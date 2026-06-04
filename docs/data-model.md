# Data Model — Nodes, Edges, and Types Explained

This is a plain-English walkthrough of the four building blocks the whole system is made of:
`LineageNode`, `LineageEdge`, `NodeType`, and `EdgeType`. They're defined in
[`src/models.py`](../src/models.py) and pictured in
[`docs/diagrams/modelo-datos.png`](diagrams/modelo-datos.png).

> The README has the quick **reference tables** for the types. This doc explains *what they
> mean* and *why* the model is shaped this way.

---

## The core idea: everything is a graph

The tool's only job is to build one **graph**, and a graph is made of exactly two things:

- **nodes** — the "things" (a table, a column, a file…)
- **edges** — the directed "relationships" between things (this column comes from that column)

Think of it like a map:

| Concept | Map analogy | In the code |
|---|---|---|
| `LineageNode` | a place (a city, a town) | a data asset |
| `LineageEdge` | a one-way road between two places | a relationship, with a direction |
| `NodeType` | what *kind* of place (city? town?) | the category of a node (9 options) |
| `EdgeType` | what *kind* of road (highway? path?) | the category of an edge (7 options) |

A `LineageGraph` is simply the bag holding all the nodes and all the edges together.

---

## `LineageNode` — a single data asset

One node = one thing in your data world: the table `stg_customers`, the single column
`stg_customers.first_name`, or the Python file `transform_pipeline.py`.

| Field | Meaning | Example |
|---|---|---|
| `id` | unique identifier (the key) | `"int_customers.full_name"` |
| `name` | human-readable name | `"full_name"` |
| `node_type` | what kind of asset it is (a `NodeType`) | `COLUMN` |
| `source_repo` | which repo it came from (for multi-repo analysis) | `"sample_repo_sql"` |
| `metadata` | extras: file path, line number, `source: "llm"`, etc. | `{ ... }` |

The `id` is the important part: edges reference nodes by `id`, and it's how the same asset
discovered by two different parsers gets recognized as **one** node. That deduplication is
what `LineageGraph.merge()` does.

---

## `LineageEdge` — a relationship between two nodes

One edge = one relationship, always **from** a source node **to** a target node. Example:
"the column `int_customers.full_name` **derives from** `stg_customers.first_name`."

| Field | Meaning | Example |
|---|---|---|
| `source_id` | `id` of the "from" node | `"int_customers.full_name"` |
| `target_id` | `id` of the "to" node | `"stg_customers.first_name"` |
| `edge_type` | what kind of relationship (an `EdgeType`) | `DERIVES_FROM` |
| `transformation` | the logic applied, if any | `"CONCAT(first_name, ' ', last_name)"` |
| `confidence` | `1.0` = deterministic parser, `<1.0` (≈`0.85`) = LLM-inferred | `1.0` |
| `metadata` | extras | `{ ... }` |

`confidence` is the "how sure are we" flag. It's what lets the system distinguish the
deterministic edges (100% precise by construction) from the LLM-inferred ones.

---

## `NodeType` — the 9 kinds of node

A fixed list (a Python `enum`) of the categories a node is allowed to be:

| `NodeType` | What it is | Example |
|---|---|---|
| `TABLE` | a SQL table | `stg_customers` |
| `VIEW` | a SQL view | `int_customers` |
| `COLUMN` | a single column | `stg_customers.first_name` |
| `PROCEDURE` | a stored procedure | `rpt_monthly_summary` proc |
| `PYTHON_FUNCTION` | a function in a Python script | `compute_customer_summary` |
| `FILE` | a source file (e.g. a notebook path) | `01_staging_tables.sql` |
| `ADF_PIPELINE` | an Azure Data Factory pipeline | `IngestRawData` |
| `ADF_DATASET` | an ADF dataset | `SqlStgCustomers` |
| `ADF_DATAFLOW` | an ADF mapping data flow | `CustomerMetricsFlow` |

---

## `EdgeType` — the 7 kinds of relationship

The categories an edge is allowed to be. Each describes *how* two assets relate, and in
which direction:

| `EdgeType` | Direction | Meaning |
|---|---|---|
| `HAS_COLUMN` | table → column | schema: a table owns a column |
| `DERIVES_FROM` | output column → input column | **column-level lineage** |
| `READS_FROM` | view/proc → table | a view or proc reads a table |
| `WRITES_TO` | proc/pipeline → table | a proc or pipeline writes a table |
| `DEFINED_IN` | asset → file | where the asset is defined in code |
| `COPIES_TO` | dataset → dataset | an ADF Copy activity moves data |
| `TRIGGERS` | pipeline/proc → pipeline/proc | orchestration: one thing runs another |

---

## A worked example

When the system analyzes `full_name = CONCAT(first_name, last_name)`, it emits:

```
NODES (things):
  stg_customers              node_type = TABLE
  stg_customers.first_name   node_type = COLUMN
  int_customers.full_name    node_type = COLUMN

EDGES (relationships):
  stg_customers            --HAS_COLUMN-->     stg_customers.first_name     confidence 1.0
  int_customers.full_name  --DERIVES_FROM-->   stg_customers.first_name     confidence 1.0
                                               transformation = "CONCAT(first_name, last_name)"
```

That's the entire model in action: a few typed nodes, joined by a few typed, directed edges.

---

## Why a graph? Upstream, downstream, and impact

Because every relationship is a **directed edge**, you can *walk* the graph:

- Follow `DERIVES_FROM` edges **backwards** from a column → you get its full origin
  (**upstream** / "where does this number come from?").
- Follow them **forwards** → you get everything that would break if you changed it
  (**downstream** / **impact analysis**).

That walk is a breadth-first search in [`src/api.py`](../src/api.py) (`_build_adjacency` +
the `/upstream`, `/downstream`, `/impact` endpoints). It's the same traversal that powers the
Flow View in the web UI and the impact report posted on each pull request.

So, in one sentence:

> **`NodeType` and `EdgeType` are the fixed vocabularies; `LineageNode` and `LineageEdge` are
> the actual instances using that vocabulary; and a `LineageGraph` is the full set of nodes and
> edges — which, because the edges are directed, you can traverse to answer lineage and impact
> questions.**

---

## Where this lives in code

- Definitions: [`src/models.py`](../src/models.py)
- Merge / dedup logic: `LineageGraph.merge()` in the same file
- Graph traversal (upstream/downstream/impact): [`src/api.py`](../src/api.py)
- Visual: [`docs/diagrams/modelo-datos.png`](diagrams/modelo-datos.png)
