# NZ Law Compass

An AI-powered Q&A system for New Zealand employment law, tax law, and labour market data — multi-domain RAG and Text-to-SQL pipelines, governed by a focused risk-control pipeline with crisis detection, a narrow refusal set, an output guard, and full audit logging. Answer quality is governed by a dedicated evaluation system: Langfuse tracing and span-level observability, a frozen test set scored by an LLM-as-judge, and a hard acceptance gate. Live at **[nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)**.

> ⚖️ For informational purposes only — not legal or tax advice. For serious matters, consult a qualified professional.

---

## What It Does

Ask a question in plain English. A Claude tool-use agent automatically routes it to the right knowledge source — and a parallel risk-control pipeline decides *whether* and *how* to answer:

| Query type | Example | How it's answered |
|---|---|---|
| **Employment law** | *"What is the minimum notice period for dismissal?"* | RAG over NZ employment law documents |
| **Tax law** | *"What is the GST registration threshold?"* | RAG over IRD + Income Tax Act 2007 + GST Act 1985 |
| **Labour market data** | *"Which industry had the fastest wage growth over 5 years?"* | Text-to-SQL over Stats NZ labour market data |
| **Cross-domain** | *"How are redundancy payments taxed?"* | Tool-use agent calls both `search_employment_law` and `search_tax_law` in one turn |
| **High-stakes / out-of-scope** | *"Should I sue my employer?"* / *"Help me apply for a visa"* | Refused with structured referrals (Community Law, IRD, licensed adviser) |

Answer shape follows question complexity — a simple lookup gets a short direct answer; a complex question gets a fuller treatment. When a question crosses into licensed-adviser territory or a personal-safety crisis, the answer is **refused** with a warm referral and a `refused` badge. Every legal and tax answer includes source citations from an allowlisted set of NZ government domains. Every interaction is audit-logged.

---

## Live Demo

**[https://nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)** *(desktop recommended)*

Try asking:
- *What is the current minimum wage in New Zealand?*
- *How many sick days am I entitled to?*
- *What is the gender pay gap in the healthcare sector?*
- *I've been made redundant after 5 years — what am I owed?*
- *What industries have seen the most employment growth since 2020?*

---

## Features

- ✅ Multi-domain knowledge federation: employment law + tax law + labour market data, behind one agent endpoint
- ✅ Claude tool-use routing — automatic, supports cross-domain queries in one turn
- ✅ Focused 6-step risk-control pipeline — crisis detection, narrow regex refusal, output guard, citation validation
- ✅ Complexity-adaptive answers — one unified synthesis prompt; simple questions get short answers, complex ones get fuller treatment
- ✅ Narrow refusal set — 6 categories only; the routing model judges out-of-scope, regex catches the clear-cut cases
- ✅ Crisis layer — SELF_HARM and FAMILY_VIOLENCE keywords override everything with crisis cards (1737, Lifeline, Women's Refuge, Are You OK)
- ✅ Banned-phrase output guard — EN + ZH patterns, regenerate once then safe fallback
- ✅ URL allowlist citation validation — only legislation.govt.nz, employment.govt.nz, ird.govt.nz, communitylaw.org.nz, etc. survive
- ✅ `refused` badge — rendered only on refused answers, not on every message
- ✅ Blocking first-message disclaimer modal — single mandatory checkbox, logged to `consent_events` (7-year retention)
- ✅ Full audit trail — every answer written to `answer_audit` (2-year rolling)
- ✅ Evaluation system — Langfuse observability + frozen 36-question test set + LLM-as-judge scoring + hard acceptance gate
- ✅ Compliance-aware Disclaimer / Privacy / Terms drafted against LCA 2006, IALA 2007, FMCA, Privacy Act 2020 (13 IPPs)
- ✅ Interactive Recharts charts (line / bar / grouped_bar / pie) for data answers
- ✅ Privacy-first — IP hashed with monthly rotating salt; raw IP never stored

---

## Risk-Control Architecture

Every question passes through a focused 6-step pipeline before a response is returned. Answer shape follows question complexity — one unified synthesis prompt, no forced templates — and a narrow refusal set handles the cases that cross into licensed-adviser territory or a personal-safety crisis.

```
User question
    │
    ▼
Step 1: Crisis detector                 (crisis_detector.py)
    │   Lexicon scan, no API call:
    │   SELF_HARM / FAMILY_VIOLENCE  → override with crisis card (1737, Lifeline, Women's Refuge)
    │   IMMIGRATION / CRIMINAL       → force refuse with referral
    ▼
Step 2: Pre-flight regex refusal        (refusal_router.py)
    │   4 clear-cut categories: active proceeding · immigration ·
    │   criminal defence · wills & estates
    │   Everything else is left for the routing model to judge
    ▼
Step 3: Tool routing                    (Claude Sonnet, function calling)
    │   search_employment_law · search_tax_law · query_labour_market_stats
    │   System prompt enforces "must call a tool, never answer from prior knowledge"
    │   Out-of-scope domains refused here by the model itself
    ▼
Step 4: Parallel retrieval              (selected tools execute concurrently)
    ▼
Step 5: Unified synthesis               (Claude Haiku, ONE system prompt)
    │   Answer shape follows question complexity — no forced headings.
    │   Word-count cap + substance requirement; banned-phrase list inline.
    ▼
Step 6: Output guard                    (output_guard.py)
    │   Banned-phrase scan (EN + ZH) + verbatim LIGHT footer check
    │   Violation → regenerate (max 1) → fall back to safe referral block
    ▼
Citation validation + audit log
    │   citation_validator.py: strips any URL not on the allowlist
    │   api/db.py: writes one slim row to answer_audit
    ▼
Response returned to frontend
```

The narrow refusal set is **6 categories**: `self_harm` and `family_violence` (caught by `crisis_detector`), plus `active_proceeding`, `immigration`, `criminal`, and `wills_estates` (caught by `refusal_router`). A refused answer returns a warm referral and carries a `refused` badge; non-refused answers carry no badge.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 / TypeScript |
| Tool-use routing | Claude Sonnet 4.5 (function calling, 3 tools) |
| SQL generation | Claude Sonnet (focused schema + few-shot) |
| Answer synthesis | Claude Haiku (one unified system prompt) |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 (33M params, local) |
| Vector database | ChromaDB (HNSW, cosine) — separate collections for employment + tax |
| Analytical database | DuckDB (columnar OLAP) — Stats NZ labour data |
| Audit + consent storage | SQLite (WAL, idempotent migrations) |
| Web crawling | requests, BeautifulSoup4, pdfplumber |
| Backend API | FastAPI + uvicorn |
| Frontend | React / Next.js 14 (TypeScript) + Recharts |
| Backend deployment | Railway |
| Frontend deployment | Vercel |

---

## AI Practices Implemented

- **Tool-Use Agent Routing** — Claude Sonnet 4.5 function calling across 3 tools (`search_employment_law`, `search_tax_law`, `query_labour_market_stats`). Cross-domain queries are handled naturally because the model can call multiple tools in one turn. Adding a new domain is one tool registration.

- **Focused Risk Control** — crisis detector → narrow regex refusal → output guard → citation allowlist. The routing model judges scope directly; there is no separate intent classifier and no routing matrix. Answer shape follows question complexity, so simple questions are not forced into legal-memo templates.

- **Cite-or-Refuse** — every legal/tax claim must be backed by a source URL on the allowlist. URLs not on the list are stripped from `sources` before the response leaves the API. No citation → safe fallback.

- **Complexity-Adaptive Synthesis** — one unified Haiku system prompt with a word-count cap and a substance requirement. A simple lookup gets a short direct answer; a complex question gets a fuller treatment — without forced section headings.

- **Observability & Evaluation** — full Langfuse tracing instruments every request as nested spans (`route`, `retrieve`, `generate`). Answer quality is governed by a dedicated eval system: a frozen 36-question test set, an LLM-as-judge that scores each answer against a frozen rubric, and a hard acceptance gate run on every iteration. Eval traffic is tagged `env=eval` so it never pollutes production observability dashboards. See the [Evaluation System](#evaluation-system) section below.

- **Auditable Consent Architecture** — first-message blocking modal with a mandatory checkbox; accept/decline written to `consent_events` table with hashed IP (monthly rotating salt) and 7-year retention for legal defence.

- **Multi-Source Retrieval Architecture** — RAG over employment documents, RAG over tax documents, and Text-to-SQL over Stats NZ DuckDB, all federated behind a single agent endpoint.

- **Text-to-SQL with Dynamic Schema Injection** — Haiku selects 1–5 relevant tables from 11 (60–90% fewer tokens than full schema), then Sonnet generates DuckDB SQL with focused schema + 10 few-shot Q→SQL examples.

- **Task-Based Model Routing** — Haiku for classification / table selection / answer synthesis; Sonnet for tool routing and SQL generation. Standard production pattern for cost/latency balance.

- **Rule-Based Chart Type Inference** — chart type derived deterministically from result shape (date axis → line, category axis → bar). Tested empirically more reliable than LLM-decided charts.

- **Deterministic Read-Only SQL Enforcement** — prompt-level keyword guard + `duckdb.connect(read_only=True)` — two independent layers.

---

## Legal RAG Pipeline (Employment)

### Data Collection

Automated collection from authoritative NZ government sources.

**Results:** 1,283 URLs discovered → 1,227 successfully collected (95.6%) → 1,233 HTML/PDF files

### Cleaning & Chunking

| Content Type | Chunk Size | Overlap |
|---|---|---|
| Legislation | 1,200 tokens | 250 tokens |
| Case law | 1,100 tokens | 220 tokens |
| Guidance | 1,000 tokens | 200 tokens |

**Results:** 1,960 chunks, avg. 1,000 tokens each

### Vectorisation & Retrieval

- **Embedding model:** `sentence-transformers/all-MiniLM-L6-v2`
- **Vector store:** ChromaDB (collection `nz_employment_law`), HNSW + cosine
- **1,960 chunks embedded in 17.8 seconds**

### Retrieval Validation

| Tier | Type | Count | Coverage |
|------|------|-------|----------|
| 1 | Basic facts (minimum wage, leave, sick leave…) | 10 | 100% |
| 2 | Synthesis (redundancy calculation, multi-doc reasoning) | 5 | 100% |
| 3 | Edge cases (contractor classification, foreign workers) | 5 | 100% |

**Overall retrieval coverage: 100% (20/20)**

---

## Tax RAG Pipeline (Live)

The tax domain is fully wired into the same agent pipeline as employment law and deployed to production:

- ✅ `pipeline/tools/tax_search.py` — ChromaDB tool wrapper for collection `nz_tax_law` at `data/vectorstore_tax`
- ✅ `pipeline/refusal_router.py` — pre-flight refusal categories: trusts / international tax / FBT / bright-line / crypto tax / active IRD disputes / form-filling assistance
- ✅ `pipeline/citation_validator.py` — allowlist extended for `ird.govt.nz`, `taxtechnical.ird.govt.nz`, `taxpolicy.ird.govt.nz`
- ✅ Tool registered in `agent_router.py` — Sonnet routes between employment and tax tools, calling both for cross-domain questions ("how are redundancy payments taxed?")

### Tax sources

IRD guidance pages, Income Tax Act 2007, GST Act 1985, KiwiSaver Act 2006, and Tax Information Bulletins — all chunked, embedded into the `nz_tax_law` ChromaDB collection.

### Tax scope

**In scope:** Income tax (PAYE, brackets, deductions), GST (registration, returns, basics), KiwiSaver (employer/employee contributions), employer tax obligations.

**Out of scope (force-refused):** trusts, FBT, international tax, double-tax treaties, bright-line, crypto tax, estate duty, individual tax planning, active IRD disputes, form-filling guidance.

---

## Text-to-SQL Pipeline (Labour Market Data)

### Data

11 Stats NZ Labour Market tables in DuckDB, unified long-format schema (`period | [dimensions] | metric | value`):

| Table | Coverage |
|---|---|
| `labour_force_status` | National employment / unemployment / participation rates |
| `underutilisation` | Underutilisation by region |
| `employed_by_industry` | Employment count by industry |
| `employed_ft_pt_status` | Full-time vs part-time employment |
| `filled_jobs_hours` | Filled jobs and paid hours by industry |
| `gross_earnings` | Total gross earnings by industry |
| `labour_cost_index` | Wage cost growth index by industry |
| `avg_hourly_earnings` | Average hourly earnings by industry and gender |
| `ethnicity_status` | Employment status by ethnicity |
| `age_group_status` | Employment status by age group |
| `earnings_by_qualification` | Weekly earnings by industry, gender, qualification |

**Data quality validation: 50 PASS / 0 FAIL** — including 9 cross-metric identity checks (unemployment rate formula, participation rate, industry summation totals) and real-world semantic checks (COVID shock visible 2020, gender pay gap confirmed across industries).

### SQL Generation

```
Haiku selects 1–5 relevant tables from 11
        ↓
Focused schema (60–90% fewer tokens) + 10 few-shot Q→SQL examples
        ↓
Sonnet generates DuckDB SQL
        ↓
Read-only guard (_assert_read_only + duckdb read_only=True)
        ↓
Execute + retry on error (max 2 retries)
        ↓
QueryResult → DataFrame → rule-based chart inference
```

**Validation: 175 PASS / 0 FAIL** across schema metadata, few-shot SQL execution, and end-to-end pipeline integration.

---

## Audit & Governance

The system writes structured audit data to `data/audit.db` (SQLite, WAL mode):

| Table | Purpose | Retention |
|---|---|---|
| `consent_events` | Disclaimer accept/decline events with hashed IP | 7 years |
| `answer_audit` | Per-answer metadata (domain, model, citations, regeneration count, refused, refusal reason, crisis route) | 2 years rolling |
| `source_registry` | Source freshness tracking | indefinite |
| `banned_phrases` | Mirror of output_guard regex list | versioned |

`docs/monitoring_queries.sql` ships 15 named queries covering daily health, weekly review, monthly trends, incident investigation, and quarterly retention enforcement. Governance procedures (version bumps, review calendar, incident runbooks, retention SQL) are documented in `docs/GOVERNANCE.md`.

---

## Evaluation System

Answer quality is governed by a dedicated evaluation system, not by spot-checking. It has two halves: **observability** — knowing what the system did on every request — and a **scored eval loop** — measuring whether answers are good and gating changes on that measurement.

### Observability — Langfuse tracing

Every request to `/api/agent/query` is instrumented with Langfuse. The `@observe` decorator captures the pipeline as a tree of nested spans — `route` (tool selection), `retrieve` (parallel retrieval), `generate` (synthesis) — so any answer can be traced end to end: which tools were called, what was retrieved, how many times the output guard regenerated, and the final latency. Tracing fails open — if Langfuse is unconfigured or unreachable, a no-op decorator is substituted and production answers are never affected.

Eval traffic is isolated from production. The eval runner sends `X-Eval-*` request headers; the server attaches an `env=eval` tag plus per-question metadata (`eval_run_id`, `question_id`, `pipeline`, `difficulty`) to the trace. A saved "Production only (NOT eval)" filter view keeps eval runs from inflating production dashboards.

### Evaluation loop — a four-stage pipeline

```
test_set (frozen)  ──run_eval.py──▶  raw run  ──llm_judge.py──▶  scored run  ──compare──▶  gate verdict
   36 questions        (real HTTP)   answers +    (LLM-as-judge)   1–5 scores +   (vs baseline)  pass / fail
                                     routing                      verdict
```

**Stage 1 — frozen test set.** Thirty-six questions, deliberately constructed as a matrix: all six pipelines (`employment`, `tax`, `labour_market`, `cross`, `out_of_scope`, `crisis`), every routing outcome, and every difficulty level (`easy` → `adversarial`). Each question carries `expected_points` describing what a good answer should cover. The test set is **frozen before it is run** — editing it after seeing outputs would quietly delete the questions the system handles worst, which is exactly the signal the eval exists to catch. A genuinely broken question triggers a version bump to a new test set, never an in-place edit.

**Stage 2 — the run.** `run_eval.py` POSTs every question to the live endpoint over real HTTP — the same network path a real user takes. It records the answer, sources, the *observed* routing, the regeneration count, latency, and the Langfuse trace id.

**Stage 3 — LLM-as-judge.** `llm_judge.py` scores each answer with an LLM judge (Opus by default). Scoring is delegated to a model because the rubric's legal-reasoning and risk-discipline dimensions require legal expertise to apply consistently. The judge receives a frozen rubric, the question, `expected_points`, the answer, and slim audit metadata, and returns four dimension scores (1–5), a `pass`/`partial`/`fail` verdict, and a single dominant `failure_mode` from a fixed enum. Two rubrics are routed by pipeline — a legal-domain rubric and a labour-market rubric. The large rubric block is sent with prompt caching, cutting input cost ~80%.

**Stage 4 — compare and gate.** A comparison script diffs a new scored run against a baseline and emits a **hard acceptance gate**: fixed thresholds on the key dimension averages plus zero tolerance for specific failure modes. The gate exits non-zero on any breach, so a quality regression blocks the change. Gate thresholds ratchet upward each iteration — the last passing run becomes the new floor.

### Methodology

Four principles make the mechanism trustworthy: **freeze before running** (test set and rubrics are locked before any run); **one change at a time** (each fix is measured individually, so a gate pass attributes to a specific change); **read a real bad answer before trusting a metric** (a score says *something* is wrong, not *what* — reading the worst answers once caught a missing dependency that a score alone would have misdiagnosed as a prompt regression); and **LLM-as-judge over manual scoring** for consistency on the expertise-heavy dimensions. The full methodology is documented in [`docs/Eval_Methodology.md`](docs/Eval_Methodology.md).

---

## Repositories

| Repo | Description |
|------|-------------|
| [rag-app-nz-employment-law](https://github.com/LauraHFC/rag-app-nz-employment-law) | Data pipeline + FastAPI backend + risk-control modules |
| [nz-employment-law-frontend](https://github.com/LauraHFC/nz-employment-law-frontend) | React / Next.js frontend (DisclaimerModal, RiskBadge, policy pages) |

---



### Environment Variables (Backend)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (required) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Langfuse observability (optional — tracing runs in no-op mode if absent) |

Railway must mount a persistent volume to `data/` so `audit.db` survives restarts.

> The Anthropic API key is read only from the `ANTHROPIC_API_KEY` environment variable. It is never passed as a function argument, so it is not captured in Langfuse trace spans.

---

## Cost Estimate

| Component | Model | Cost per query |
|---|---|---|
| Crisis detector | regex (no API) | $0 |
| Intent classifier | Claude Haiku | ~$0.0002 |
| Tool routing | Claude Sonnet | ~$0.005 |
| Table selection (data path only) | Claude Haiku | ~$0.0002 |
| SQL generation (data path only) | Claude Sonnet | ~$0.012 |
| Template-aware synthesis | Claude Haiku | ~$0.002 |
| **Typical legal query** | | **~$0.007** |
| **Typical data query** | | **~$0.020** |

---

## Privacy

- Conversations are ephemeral — held only in browser session, deleted on refresh.
- Disclaimer accept/decline logged to `consent_events` (server-side IP hash with monthly rotating salt; raw IP never stored).
- Per-answer audit metadata logged to `answer_audit` (no question text by default; `question_hash` only).
- Retention: consent 7 years, audit 2 years rolling, aggregated metrics indefinite.

Full Privacy Act 2020 alignment (13 IPPs) documented at `/privacy`.

---

**Author:** Laura Cai · [LinkedIn](https://www.linkedin.com/in/laurahfc/)

---

*For informational purposes only — not legal or tax advice. All information sourced from official New Zealand government websites (legislation.govt.nz, employment.govt.nz, ird.govt.nz, Stats NZ). Compliance posture is conservative-by-default: the system refuses cleanly when a question crosses into licensed-adviser territory (LCA 2006, IALA 2007, FMCA). For serious matters, consult a qualified professional.*
