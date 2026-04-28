# NZ Employment Intelligence Hub

An AI-powered Q&A system for New Zealand employment law and labour market data — combining a full RAG pipeline with a Text-to-SQL engine over Stats NZ data. Live at **[nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)**.

> ⚖️ For informational purposes only — not legal advice. For serious matters, consult a qualified employment lawyer.

---

## What It Does

Ask a question in plain English. The system automatically routes it to the right knowledge source:

| Query type | Example | How it's answered |
|---|---|---|
| **Legal** | *"What is the minimum notice period for dismissal?"* | RAG over NZ employment law documents |
| **Data** | *"Which industry had the fastest wage growth over 5 years?"* | Text-to-SQL over Stats NZ labour market data |
| **Hybrid** | *"My salary is below the industry average — what are my legal options?"* | Both channels, synthesised into one answer |

Every legal answer includes the source document and URL so you can verify it yourself. Data answers include a chart.

---

## Live Demo

**[https://nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)** *(avaliable on both both desktop and mobile)*

Try asking:
- *What is the current minimum wage in New Zealand?*
- *How many sick days am I entitled to?*
- *What is the gender pay gap in the healthcare sector?*
- *Māori unemployment rate vs the national average?*
- *I've been made redundant after 5 years — what am I owed?*
- *What industries have seen the most employment growth since 2020?*

---

## System Architecture

```
User natural language query
        ↓
  Query Router (Claude Haiku)
  intent = legal / data / hybrid
  ┌──────────┴──────────┐
  ↓                     ↓
Legal RAG Channel     Data SQL Channel
Vector search         Haiku selects tables
(ChromaDB)            → Sonnet generates SQL
                      → DuckDB executes
  └──────────┬──────────┘
             ↓
   Answer Generator (Claude Haiku)
   Natural language answer + rule-based chart
             ↓
   HubQueryResponse (JSON)
   answer / chart / sources / intent
             ↓
   Next.js frontend
   Text + Recharts chart + Sources panel
```

---

## Features

- ✅ Dual-channel retrieval: employment law documents + some NZ labour market data
- ✅ Automatic intent routing — legal, data, or hybrid, handled transparently
- ✅ Source citations with URLs for every legal response — fully verifiable
- ✅ Interactive charts (line, bar, grouped bar, pie) for data answers
- ✅ Handles complex hybrid questions combining law and statistics
- ✅ Declines to answer when context is insufficient — no hallucination
- ✅ Privacy-first — no user data collected, sessions are ephemeral

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 / TypeScript |
| Query routing | Claude Haiku (3-class intent classification) |
| SQL generation | Claude Sonnet (schema injection + few-shot) |
| Answer generation | Claude Haiku (unified across all paths) |
| Labour market data | Stats NZ — 11 tables · 25,274 rows · 2015–2025 |
| Analytical database | DuckDB (columnar, embedded) |
| Web crawling | requests, BeautifulSoup4 |
| PDF extraction | pdfplumber |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Vector database | ChromaDB |
| Backend API | FastAPI + uvicorn |
| Frontend | React / Next.js 14 (TypeScript) + Recharts |
| Backend deployment | Railway |
| Frontend deployment | Vercel |

---

## Legal RAG Pipeline

### Data Collection

Automated collection from authoritative NZ government sources:

**Results:**1,227 URLs successfully collected  → 1,233 HTML/PDF files

### Cleaning & Chunking

- HTML parsed with BeautifulSoup4; PDFs extracted with pdfplumber (first 20 pages)
- Content typed as `legislation`, `case`, or `guide` for adaptive chunking

| Content Type | Chunk Size | Overlap |
|---|---|---|
| Legislation | 1,200 tokens | 250 tokens |
| Case law | 1,100 tokens | 220 tokens |
| Guidance | 1,000 tokens | 200 tokens |

**Results:** 1,960 chunks, avg. 1,000 tokens each

### Vectorisation & Retrieval

- **Embedding model:** `sentence-transformers/all-MiniLM-L6-v2` (local, 33M params)
- **Vector store:** ChromaDB with HNSW indexing and cosine similarity
- **1,960 chunks embedded **

### Retrieval Validation

20-question test suite across 3 tiers:

| Tier | Type | Count | Coverage |
|------|------|-------|----------|
| 1 | Basic facts (minimum wage, annual leave, sick leave…) | 10 | 100% |
| 2 | Synthesis (redundancy calculation, multi-doc reasoning…) | 5 | 100% |
| 3 | Edge cases (contractor classification, foreign workers…) | 5 | 100% |

**Overall retrieval coverage: 100% (20/20)**

---

## Text-to-SQL Pipeline

### Data

11 Stats NZ Labour Market tables loaded into **DuckDB**, all following a unified long-format schema (`period | [dimension cols] | metric | value`):

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
| `earnings_by_qualification` | Weekly earnings by industry, gender, and qualification |

**Data quality validation: 50 PASS / 0 FAIL** — including 9 cross-metric identity checks (unemployment rate formula, participation rate, industry summation totals) and real-world semantic checks (COVID shock visible in 2020, gender pay gap confirmed across all industries).

### SQL Generation

```
LLM(Haiku model) selects 1–5 relevant tables from 11
        ↓
Focused schema prompt (60–90% fewer tokens than full schema)
+ 10 few-shot Q→SQL examples
        ↓
LLM(Sonnet model) generates DuckDB SQL
        ↓
Read-only guard (_assert_read_only + duckdb read_only=True)
        ↓
Execute + retry on error (max 2 retries)
        ↓
QueryResult → DataFrame
```

**Validation: 175 PASS / 0 FAIL** across schema metadata checks, few-shot SQL execution tests, and end-to-end pipeline integration tests.

### Key design decisions

**DuckDB over SQLite** — the workload is entirely analytical aggregations (OLAP). Columnar storage is 10–100x faster for this use case. A DB abstraction layer (`pipeline/db_engine.py`) supports switching to Snowflake or Databricks via a single environment variable.

**Pure Prompt over LangChain SQL Agent** — every step is explicit and controllable. The schema metadata and few-shot examples are stored in separate files (`schema_context.py`, `few_shot_examples.py`) and loaded dynamically, mirroring the structure of enterprise RAG-over-SQL systems.

**Rule-based chart engine** — chart type is inferred deterministically from the query result shape, rather than asking the LLM to decide. This proved more reliable in practice (Haiku ignored JSON structure instructions 100% of the time during development).

---

## Repositories

| Repo | Description |
|------|-------------|
| [rag-app-nz-employment-law](https://github.com/LauraHFC/rag-app-nz-employment-law) | Data pipeline + FastAPI backend |
| [nz-employment-law-frontend](https://github.com/LauraHFC/nz-employment-law-frontend) | React / Next.js frontend |

---

## Deployment

| Service | URL | Platform |
|---------|-----|----------|
| Frontend | [nzlaw.linkiwise.com](https://nzlaw.linkiwise.com) | Vercel |
| Backend API | Railway |

### Environment Variables (Backend)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (required) |

---

## Cost Estimate

| Component | Model | Cost per query |
|---|---|---|
| Query routing | Claude Haiku | ~$0.0002 |
| Table selection | Claude Haiku | ~$0.0002 |
| SQL generation | Claude Sonnet | ~$0.012 |
| Answer generation | Claude Haiku | ~$0.002 |
| **Total per query** | | **~$0.015** |

---

## Privacy

No user data is collected. All conversations are temporary and exist only in your browser session — permanently deleted when you close or refresh the page.

---

**Author:** Laura Cai · [LinkedIn](https://www.linkedin.com/in/laurahfc/)

---

*For informational purposes only — not legal advice. All information sourced from official New Zealand government websites and Stats NZ. For serious employment matters, consult a qualified employment lawyer.*
