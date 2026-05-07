# NZ Law Compass

An AI-powered Q&A system for New Zealand employment law, tax law, and labour market data — multi-domain RAG and Text-to-SQL pipelines, governed by a 9-stage risk-control layer with crisis detection, intent classification, output guards, and full audit logging. Live at **[nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)**.

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

Every answer carries a per-message **risk badge** (`general_info` / `high_care` / `please_get_advice` / `refused`) derived from the routing matrix. Every legal and tax answer includes source citations from an allowlisted set of NZ government domains. Every interaction is audit-logged.

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

## v4 Risk-Control Architecture

Every question now passes through a 9-stage pipeline before a response is returned:

```
User question
    │
    ▼
Stage 1: Pre-flight regex refusal      (refusal_router.py)
    │   Out-of-scope / active disputes / form-filling
    │   Fast pattern match, no API call
    ▼
Stage 2: Crisis detector                (crisis_detector.py)
    │   5 lexicons in priority order:
    │   SELF_HARM / FAMILY_VIOLENCE  → override with crisis card (1737, Lifeline, Women's Refuge)
    │   IMMIGRATION / CRIMINAL       → force REFUSE_WITH_REFERRAL
    │   IMMINENT_ACTION              → pin intent to HIGH_STAKES, continue
    ▼
Stage 3: Intent + domain classifier     (intent_classifier.py)
    │   Claude Haiku, structured JSON
    │   intent_class:  LOOKUP / ADVICE / HIGH_STAKES
    │   domain_tier:   H1 / H2 / H3 / M / L
    │   Confidence < 0.75 → conservative one-notch upgrade
    ▼
Stage 4: Routing matrix                 (15 combinations → 4 outcomes)
    │   DIRECT_ANSWER · STRUCTURED_INFORMATIONAL
    │   STRUCTURED_ADVICE_SKELETON · REFUSE_WITH_REFERRAL
    ▼
Stage 5: Tool routing                   (Claude Sonnet, function calling)
    │   search_employment_law · search_tax_law · query_labour_market_stats
    │   System prompt enforces "must call a tool, never answer from prior knowledge"
    ▼
Stage 6: Parallel retrieval             (selected tools execute concurrently)
    ▼
Stage 7: Template-aware synthesis       (Claude Haiku, 4 system prompts)
    │   Each template includes banned-phrase list + verbatim risk footer instruction
    ▼
Stage 8: Output guard                   (output_guard.py)
    │   Checks banned phrases (10 EN + 10 ZH), required headings, footer verbatim
    │   Violation → regenerate (max 2 attempts) → fall back to safe refusal template
    ▼
Stage 9: Citation validation + audit log
    │   citation_validator.py: strips any URL not on allowlist
    │   api/db.py: writes one row to answer_audit (intent, tier, regeneration count, badge)
    │
    ▼
Response returned to frontend (with risk badge)
```

### Routing matrix

| Domain tier | LOOKUP | ADVICE | HIGH_STAKES |
|---|---|---|---|
| H1 (criminal, immigration) | REFUSE_WITH_REFERRAL | REFUSE_WITH_REFERRAL | REFUSE_WITH_REFERRAL |
| H2 (family, personal injury) | STRUCTURED_INFORMATIONAL | STRUCTURED_ADVICE_SKELETON | REFUSE_WITH_REFERRAL |
| H3 (wills, consumer) | STRUCTURED_INFORMATIONAL | STRUCTURED_ADVICE_SKELETON | STRUCTURED_ADVICE_SKELETON |
| **M (employment, tax)** | DIRECT_ANSWER | STRUCTURED_INFORMATIONAL | STRUCTURED_ADVICE_SKELETON |
| L (general info) | DIRECT_ANSWER | DIRECT_ANSWER | STRUCTURED_INFORMATIONAL |

---

## Features

- ✅ Multi-domain knowledge federation: employment law + tax law + labour market data, behind one agent endpoint
- ✅ Claude tool-use routing — automatic, supports cross-domain queries in one turn
- ✅ 9-stage risk control pipeline — pre-flight refusal, crisis detection, intent + domain classification, output guard, citation validation
- ✅ Per-message risk badge (`general_info` / `high_care` / `please_get_advice` / `refused`)
- ✅ Crisis layer — SELF_HARM and FAMILY_VIOLENCE keywords override everything with crisis cards (1737, Lifeline, Women's Refuge, Are You OK)
- ✅ Banned-phrase output guard — 10 EN + 10 ZH patterns, regenerate up to 2× then safe fallback
- ✅ URL allowlist citation validation — only legislation.govt.nz, employment.govt.nz, ird.govt.nz, communitylaw.org.nz, etc. survive
- ✅ Blocking first-message disclaimer modal — 3 mandatory checkboxes, logged to `consent_events` (7-year retention)
- ✅ Full audit trail — every answer written to `answer_audit` (2-year rolling)
- ✅ Compliance-aware Disclaimer / Privacy / Terms drafted against LCA 2006, IALA 2007, FMCA, Privacy Act 2020 (13 IPPs)
- ✅ Interactive Recharts charts (line / bar / grouped_bar / pie) for data answers
- ✅ Privacy-first — IP hashed with monthly rotating salt; raw IP never stored

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 / TypeScript |
| Tool-use routing | Claude Sonnet 4.5 (function calling, 3 tools) |
| Intent + domain classifier | Claude Haiku 4.5 (structured JSON) |
| SQL generation | Claude Sonnet (focused schema + few-shot) |
| Answer synthesis | Claude Haiku (template-aware, 4 system prompts) |
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

- **Tool-Use Agent Routing** — Claude Sonnet 4.5 function calling across 3 tools (`search_employment_law`, `search_tax_law`, `query_labour_market_stats`). Replaces the previous static intent classifier; cross-domain queries are handled naturally because the model can call multiple tools in one turn. Adding a new domain is one tool registration.

- **Defence-in-depth Safety Stack** — five independent layers: pre-flight regex refusal → crisis detector → intent + domain classifier → banned-phrase output guard → citation allowlist. No single layer is load-bearing; failure of any one does not compromise the others.

- **Two-Axis Routing (domain × intent)** — every query mapped to a `(domain_tier, intent_class)` cell in a 15-cell matrix that determines one of four response templates. Conservative-upgrade rule on low-confidence classifications.

- **Cite-or-Refuse** — every legal/tax claim must be backed by a source URL on the allowlist. URLs not on the list are stripped from `sources` before the response leaves the API. No citation → safe fallback.

- **Auditable Consent Architecture** — first-message blocking modal with 3 mandatory checkboxes; accept/decline written to `consent_events` table with hashed IP (monthly rotating salt) and 7-year retention for legal defence.

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

## Tax RAG Pipeline (Sprint 4 — Live)

The tax domain is fully wired into the same v4 agent pipeline as employment law and deployed to production:

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

The v4 system writes structured audit data to `data/audit.db` (SQLite, WAL mode):

| Table | Purpose | Retention |
|---|---|---|
| `consent_events` | Disclaimer accept/decline events with hashed IP | 7 years |
| `answer_audit` | Per-answer metadata (intent, tier, regeneration count, badge, refused) | 2 years rolling |
| `source_registry` | Source freshness tracking | indefinite |
| `banned_phrases` | Mirror of output_guard regex list | versioned |

`docs/monitoring_queries.sql` ships 15 named queries covering daily health, weekly review, monthly trends, incident investigation, and quarterly retention enforcement. Governance procedures (version bumps, review calendar, incident runbooks, retention SQL) are documented in `docs/GOVERNANCE.md`.

---

## Testing & Evaluation

| Component | Tests | Result |
|---|---|---|
| `crisis_detector.py` | 10 | ✅ 10/10 PASS |
| `output_guard.py` | 6 | ✅ 6/6 PASS |
| `api/db.py` | 8 | ✅ 8/8 PASS |
| Frontend TypeScript (`tsc --noEmit`) | full project | ✅ 0 errors |
| RAG retrieval | 20-question 3-tier suite | ✅ 100% coverage |
| Text-to-SQL | 175 automated tests, 3 layers | ✅ 175/175 PASS |
| Stats NZ data quality | 50 cross-metric + semantic checks | ✅ 50/50 PASS |

`test_local_api.sh` ships an 8-scenario API smoke test (health → consent → normal Q → crisis detection → forced refusal → pre-flight refusal → legacy compatibility → audit DB row counts).

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

Railway must mount a persistent volume to `data/` so `audit.db` survives restarts.

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
