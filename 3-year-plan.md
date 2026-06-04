Framing: B2B vs SaaS isn't the choice
You're conflating two axes. B2B = who you sell to. SaaS = how you deliver. The right model for you is B2B SaaS with a self-hosted option. The real fork is:

Pure cloud SaaS → fast to ship, painful to sell into banks (your beachhead).
Cloud + on-prem ("BYOC" / self-hosted) → slower to engineer, but it's the only thing your beachhead will sign.
This single decision drives the technical roadmap below. Banks will not let you send their source code to api.openai.com. Year 1 has to solve this or the beachhead can't buy.

Year 1 (May 2026 – May 2027): Bridge year — services-led, product-hardened
The business doc has this right: services first. The refinement is to make every consulting engagement also deploy and validate the product. Don't run two parallel tracks.

Revenue goal: USD 40–80k (consulting) + 1–2 paid product pilots by month 12.

Sales:

5–8 discovery calls/month from your network (banca/fintech LATAM, 50–300 employees, near auditoría).
Close 3–5 consulting engagements (3–6 weeks, USD 8–15k each). Each one requires deploying the lineage tool on their stack as part of the deliverable.
Convert 1–2 of those into paid product pilots ($500–$1200/mo) by month 9.
Product investments (in order of business urgency, not technical elegance):

Self-hosted LLM path. Customer's own Azure OpenAI key, AWS Bedrock, or local model (Qwen/Llama). Without this, the beachhead is dead. This is the single most important technical bet of Year 1.
Persistent backend. The in-memory _graph is a POC liability the moment a customer restarts the container. Postgres + recursive CTE is the lowest-effort path; Neo4j is overkill until you have a paying customer who needs Cypher.
Multi-tenancy / workspaces. Even for self-hosted, customers want repo-level isolation.
Auth + RBAC. Currently "no API auth (POC scope)" per your architecture notes. Auditors will flag this in week 1.
On-prem packaging. Docker Compose with a 30-minute install guide. Helm chart later if anyone asks.
Kill criteria at month 12:

<3 paying entities (consulting or pilot) → wrong beachhead, pause and re-validate.
0 conversions from consulting → product clearly: free tool to attract consulting work. Lean into that.
Year 2 (May 2027 – May 2028): Product-led growth, services as accelerator
Flip the ratio. Services become a paid onboarding offering ($5–10k), not a separate revenue line.

Revenue goal: USD 5–15k MRR by month 24 — ~5–10 paying product customers.

Sales:

Reduce consulting to 1 engagement per quarter, used as case study fuel.
2–3 product demos per week.
First inbound from technical content + open-sourcing pieces of the parser engine (the deterministic SQL/ADF parsers, not the full product).
Land first 1–2 customers outside your direct network — this is the real PMF signal.
Product investments:

Self-serve onboarding. Signup → connect a repo → first lineage in under 10 minutes. This is the gate for non-network customers.
Integration depth. Slack notifications on PR impact, Jira tickets for high-risk changes, ServiceNow if anyone asks.
Coverage expansion. dbt models + Airflow DAGs are the highest-ROI parsers to add — both have huge install bases and existing lineage gaps your hybrid approach handles.
SOC 2 Type I. Required to sell up-market in LATAM banks past tier-2.
First hire (month 18–24): depends on the bottleneck. If you're losing deals on features → senior engineer. If deals close but churn → solutions/CS person who can also do implementation.

Kill criteria at month 24:

<$5k MRR → product isn't pulling. Two options: (a) return to consulting full-time and treat product as a calling card, (b) sell/open-source what you have.
Year 3 (May 2028 – May 2029): Scale + first geographic move
This is where the business doc's Phase 2/3 starts kicking in. By now you should know whether you're a $500/mo-many-customers business or a $2500/mo-few-customers business — they require different motions.

Revenue goal: USD 15–30k MRR — at or just past break-even on the $3–5k/mo cost base.

Sales:

15–25 paid customers.
First enterprise deal ($2500+/mo with SSO + on-prem + SLA).
First customer outside LATAM — most likely Spain (regulatory similarity, language) or US (if a referral pulls you).
Referenceable customer logos: 3–5 named case studies on the site.
Product investments:

SSO/SAML, audit logs, SOC 2 Type II. Gating for the Enterprise tier.
A defensible moat. Pick one and commit:
A domain-tuned small model (cheaper than GPT-4.1-mini, fine-tuned on your accumulated lineage corpus).
Deep PR impact UI that becomes the daily tool, not just the audit tool — turns lineage from compliance into developer experience.
A community-contributed parser marketplace (every customer adds parsers for their stack).
Compliance templates. BCBS 239 / SOX / MiFID II evidence packs auto-generated from the graph. This is what your beachhead is actually buying — sell it as the headline feature.
Strategic fork at month 30:

Bootstrapped (15+ customers, healthy MRR): keep going, hire #2 and #3, stay close to customers.
Stalled (<$15k MRR, slow growth): consider acquisition by a catalog vendor (Atlan, Collibra, DataHub) — your parsers + GitHub integration are the asset.
Inflection (rapid growth, can't keep up): raise a small seed to hire ahead of demand. Avoid raising before this point — your TAM doesn't tell a venture story yet.
The three decisions that matter most
Month 3 (Aug 2026): Self-hosted LLM path — commit or pick the cloud-only segment instead (mid-market SaaS companies, not banks). Don't try to serve both.
Month 12 (May 2027): Services → product handoff. If consulting is profitable but no pilots converted, the product doesn't have PMF in this segment — pick a new one or stay a consultancy.
Month 24 (May 2028): Bootstrap vs raise vs sell. The MRR number at this date determines which of the three is realistic.
Want me to drill into any one year, or sketch the technical work for the on-prem/self-hosted LLM transition (the Year 1 critical path)?