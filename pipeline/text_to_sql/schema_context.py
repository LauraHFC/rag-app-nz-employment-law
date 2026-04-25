"""
schema_context.py
=================
Prompt-ready schema metadata for all 11 DuckDB tables in data/stats_nz.db.

Two public interfaces:
  - TABLE_METADATA: dict  — structured metadata, used by the Phase 2.2 dynamic table selector
  - get_schema_context_prompt() -> str  — Markdown string for LLM system prompts

Period convention: "YYYY.QN"  (e.g. "2025.Q4")
  Exception: earnings_by_qualification uses annual data tagged as June quarter (.Q2)

JOIN WARNINGS (respect these in every SQL generation step):
  1. earnings_by_qualification (NZ.Stat source) MUST NOT be hard-JOINed to any other
     table on the `industry` column. Industry naming granularity differs from Infoshare tables.
  2. Industry names are NOT consistent across all tables. Do NOT join tables on industry names
     without aliasing / CASE mapping. Safe cross-table joins: only on `period`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Structured metadata — consumed by the Phase 2.2 dynamic table selector
# ---------------------------------------------------------------------------

TABLE_METADATA: dict[str, dict] = {
    "labour_force_status": {
        "description": (
            "Aggregate NZ labour force headline figures with no demographic breakdown. "
            "Best table for a single national-level snapshot or trend of employment, "
            "unemployment, participation, and working-age population."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 352,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {},   # no dimension columns beyond period
        "metrics": [
            "Persons Employed in Labour Force",
            "Persons Unemployed in Labour Force",
            "Working Age Population",
            "Labour Force Participation Rate",
            "Unemployment Rate",
            "Employment Rate",
            "Total Labour Force",
            "Not in Labour Force",
        ],
        "notes": (
            "Rates are expressed as percentages (e.g. 4.2 means 4.2%). "
            "Headcounts are in thousands of persons."
        ),
        "join_safe_with": "All tables on `period`",
    },

    "underutilisation": {
        "description": (
            "Labour underutilisation metrics broken down by NZ regional council. "
            "Covers broader underemployment beyond official unemployment: underemployed, "
            "available potential jobseekers, unavailable jobseekers, and underutilisation rate."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 5280,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "region": [
                "Total All Regional Councils",
                "Auckland",
                "Bay of Plenty",
                "Canterbury",
                "Gisborne / Hawke's Bay",
                "Manawatu - Whanganui",
                "North Island",
                "Northland",
                "Otago",
                "South Island",
                "Southland",
                "Taranaki",
                "Tasman / Nelson / Marlborough / West Coast",
                "Waikato",
                "Wellington",
            ],
        },
        "metrics": [
            "Total potential labour force",
            "Persons underemployed",
            "Persons officially unemployed",
            "Unavailable jobseekers",
            "Extended labour force",
            "Total underutilisation",
            "Available potential jobseekers",
            "Underutilisation rate",
        ],
        "notes": (
            "Underutilisation rate is a percentage. Headcounts are in thousands. "
            "Use region = 'Total All Regional Councils' for national figures."
        ),
        "join_safe_with": "All tables on `period`",
    },

    "employed_ft_pt_status": {
        "description": (
            "Full-time vs part-time employment and unemployment split — no industry or demographic breakdown. "
            "Useful for questions about the full-time/part-time composition of the workforce."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 484,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {},
        "metrics": [
            "Employed Full-Time",
            "Employed Part-Time",
            "Employed Total",
            "Unemployed Full-Time",
            "Unemployed Part-Time",
            "Unemployed Total",
            "Total Labour Force",
            "Total Labour Force Full-Time",
            "Total Labour Force Part-Time",
            "Full-Time Unemployment Rate",
            "Part-Time Unemployment Rate",
        ],
        "notes": "Headcounts in thousands. Rates are percentages.",
        "join_safe_with": "All tables on `period`",
    },

    "employed_by_industry": {
        "description": (
            "Number of persons employed, broken down by industry (ANZSIC06). "
            "Best table for employment headcount comparisons across industries."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 792,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "industry": [
                "Total All Industries",
                "Agriculture, Forestry and Fishing",
                "Arts, Recreation and Other Services",
                "Construction",
                "Education and Training",
                "Electricity, Gas, Water and Waste Services",
                "Financial and Insurance Services",
                "Health Care and Social Assistance",
                "Information Media and Telecommunications",
                "Manufacturing",
                "Mining",
                "Not Specified",
                "Professional, Scientific, Technical, Administrative and Support Services",
                "Public Administration and Safety",
                "Rental, Hiring and Real Estate Services",
                "Retail Trade and Accommodation",
                "Transport, Postal and Warehousing",
                "Wholesale Trade",
            ],
        },
        "metrics": ["Persons Employed"],
        "notes": (
            "Headcounts in thousands. Use industry = 'Total All Industries' for national total. "
            "Note: 'Retail Trade and Accommodation' is a combined category in this table — "
            "it does NOT match 'Retail Trade' or 'Accommodation and Food Services' in other tables."
        ),
        "join_safe_with": "All tables on `period` only — industry names differ from avg_hourly_earnings and labour_cost_index",
    },

    "filled_jobs_hours": {
        "description": (
            "Filled jobs (full-time, part-time, total) and paid hours (ordinary, overtime, total) "
            "by industry. Covers the demand side of the labour market. "
            "Source: QEM (Quarterly Employment Survey) — based on employer payrolls, not household survey."
        ),
        "source": "Stats NZ QEM (Infoshare) — Tables 4a (Filled Jobs) + 4b (Paid Hours), merged long-format",
        "rows": 4488,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "industry": [
                "Total All Industries",
                "Accommodation and Food Services",
                "Arts, Recreation and Other Services",
                "Construction",
                "Education and Training",
                "Electricity, Gas, Water and Waste Services",
                "Financial and Insurance Services",
                "Forestry and Mining",
                "Health Care and Social Assistance",
                "Information Media and Telecommunications",
                "Manufacturing",
                "Professional, Scientific, Technical, Administrative and Support Services",
                "Public Administration and Safety",
                "Rental, Hiring and Real Estate Services",
                "Retail Trade",
                "Transport, Postal and Warehousing",
                "Wholesale Trade",
            ],
        },
        "metrics": [
            "Filled Jobs (Full-Time)",
            "Filled Jobs (Part-Time)",
            "Filled Jobs (Total)",
            "Paid Hours (Ordinary)",
            "Paid Hours (Overtime)",
            "Paid Hours (Total)",
        ],
        "notes": (
            "Filled Jobs are in units of jobs (not thousands). "
            "Paid Hours are in millions of hours. "
            "Filled Jobs (Full-Time) + Filled Jobs (Part-Time) = Filled Jobs (Total). "
            "Paid Hours (Ordinary) + Paid Hours (Overtime) = Paid Hours (Total). "
            "Note: uses 'Forestry and Mining' (combined) and separate 'Retail Trade' + "
            "'Accommodation and Food Services' — different from employed_by_industry."
        ),
        "join_safe_with": "All tables on `period` only — industry names differ from employed_by_industry",
    },

    "gross_earnings": {
        "description": (
            "Total gross earnings by industry. Source: QEM employer payroll survey. "
            "Useful for comparing the wage bill across industries or tracking wage growth over time."
        ),
        "source": "Stats NZ QEM (Infoshare)",
        "rows": 748,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "industry": [
                "Total All Industries",
                "Accommodation and Food Services",
                "Arts, Recreation and Other Services",
                "Construction",
                "Education and Training",
                "Electricity, Gas, Water and Waste Services",
                "Financial and Insurance Services",
                "Forestry and Mining",
                "Health Care and Social Assistance",
                "Information Media and Telecommunications",
                "Manufacturing",
                "Professional, Scientific, Technical, Administrative and Support Services",
                "Public Administration and Safety",
                "Rental, Hiring and Real Estate Services",
                "Retail Trade",
                "Transport, Postal and Warehousing",
                "Wholesale Trade",
            ],
        },
        "metrics": ["Total Gross Earnings"],
        "notes": (
            "Gross earnings are in millions of NZD. "
            "Industry names match filled_jobs_hours exactly — safe to JOIN on period + industry between these two."
        ),
        "join_safe_with": "All tables on `period`; industry names match filled_jobs_hours",
    },

    "labour_cost_index": {
        "description": (
            "Labour Cost Index (LCI) by industry — measures changes in the cost of labour "
            "independent of quality/quantity changes. Base period is June 2017 quarter (index = 1000). "
            "Best for tracking wage inflation over time."
        ),
        "source": "Stats NZ LCI (Infoshare)",
        "rows": 836,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "industry": [
                "All Industries Combined",
                "Accommodation and Food Services",
                "Agriculture, Forestry and Fishing",
                "Arts, Recreation and Other Services",
                "Construction",
                "Education and Training",
                "Electricity, Gas, Water and Waste Services",
                "Financial and Insurance Services",
                "Health Care and Social Assistance",
                "Information Media and Telecommunications",
                "Manufacturing",
                "Mining",
                "Professional, Scientific, Technical, Administrative and Support Services",
                "Public Administration and Safety",
                "Rental, Hiring and Real Estate Services",
                "Retail Trade",
                "Retail Trade and Accommodation",
                "Transport, Postal and Warehousing",
                "Wholesale Trade",
            ],
        },
        "metrics": ["Labour Cost Index (All Salary and Wage Rates)"],
        "notes": (
            "Index value, base = 1000 at 2017.Q2. Higher = labour costs have risen more. "
            "Use 'All Industries Combined' for the national aggregate. "
            "WARNING: industry naming in this table differs from employed_by_industry and filled_jobs_hours — "
            "e.g. 'Accommodation and Food Services' vs 'Retail Trade and Accommodation'. Do not JOIN on industry."
        ),
        "join_safe_with": "All tables on `period` only — industry names NOT consistent with other tables",
    },

    "avg_hourly_earnings": {
        "description": (
            "Average hourly earnings by industry and sex (Male / Female / Total Both Sexes). "
            "Source: QEM. Best table for gender pay gap analysis and industry wage comparisons."
        ),
        "source": "Stats NZ QEM (Infoshare)",
        "rows": 2244,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "industry": [
                "Total All Industries",
                "Accommodation and Food Services",
                "Arts, Recreation and Other Services",
                "Construction",
                "Education and Training",
                "Electricity, Gas, Water and Waste Services",
                "Financial and Insurance Services",
                "Forestry and Mining",
                "Health Care and Social Assistance",
                "Information Media and Telecommunications",
                "Manufacturing",
                "Professional, Scientific, Technical, Administrative and Support Services",
                "Public Administration and Safety",
                "Rental, Hiring and Real Estate Services",
                "Retail Trade",
                "Transport, Postal and Warehousing",
                "Wholesale Trade",
            ],
            "sex": ["Male", "Female", "Total Both Sexes"],
        },
        "metrics": ["Average Hourly Earnings"],
        "notes": (
            "Earnings in NZD per hour. "
            "Use sex = 'Total Both Sexes' for combined figure, 'Male'/'Female' for gender split. "
            "Gender pay gap = Male earnings minus Female earnings (or as % of Male). "
            "WARNING: industry list differs from employed_by_industry — e.g. 'Forestry and Mining' "
            "vs separate 'Agriculture, Forestry and Fishing' / 'Mining' in other tables."
        ),
        "join_safe_with": "All tables on `period` only — industry names NOT consistent with other tables",
    },

    "ethnicity_status": {
        "description": (
            "Labour force status (employed, unemployed, participation rate, etc.) broken down by ethnicity. "
            "Useful for equity analysis across Māori, Pacific Peoples, Asian, European, and other groups."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 2816,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "ethnicity": [
                "Total All Ethnic Groups",
                "Asian",
                "European",
                "MELAA",
                "Maori",
                "Other Ethnicity",
                "Pacific Peoples",
                "Residual Categories",
            ],
        },
        "metrics": [
            "Persons Employed in Labour Force",
            "Persons Unemployed in Labour Force",
            "Working Age Population",
            "Labour Force Participation Rate",
            "Unemployment Rate",
            "Employment Rate",
            "Total Labour Force",
            "Not in Labour Force",
        ],
        "notes": (
            "Rates as percentages; headcounts in thousands. "
            "Note: individuals may belong to multiple ethnicities (NZ total population counts people once; "
            "ethnicity totals may exceed national aggregate). "
            "Use ethnicity = 'Total All Ethnic Groups' for national comparison."
        ),
        "join_safe_with": "All tables on `period`",
    },

    "age_group_status": {
        "description": (
            "Labour force status broken down by age group. Covers youth (15–24), prime-age (25–54), "
            "and older workers (55+). Useful for youth unemployment, aging workforce analysis."
        ),
        "source": "Stats NZ HLFS (Infoshare)",
        "rows": 6688,
        "period_range": ("2015.Q1", "2025.Q4"),
        "period_count": 44,
        "dimensions": {
            "age_group": [
                "Total All Ages",
                "Aged 15-19 Years",
                "Aged 15-24 Years",
                "Aged 20-24 Years",
                "Aged 25-29 Years",
                "Aged 25-34 Years",
                "Aged 30-34 Years",
                "Aged 35-39 Years",
                "Aged 35-44 Years",
                "Aged 40-44 Years",
                "Aged 45-49 Years",
                "Aged 45-54 Years",
                "Aged 50-54 Years",
                "Aged 55-59 Years",
                "Aged 55-64 Years",
                "Aged 60-64 Years",
                "Aged 65 Years and Over",
                "Aged 65-69 Years",
                "Aged 70 Years and Over",
            ],
        },
        "metrics": [
            "Persons Employed in Labour Force",
            "Persons Unemployed in Labour Force",
            "Working Age Population",
            "Labour Force Participation Rate",
            "Unemployment Rate",
            "Employment Rate",
            "Total Labour Force",
            "Not in Labour Force",
        ],
        "notes": (
            "Rates as percentages; headcounts in thousands. "
            "Some age groups overlap (e.g. 'Aged 15-24 Years' = '15-19' + '20-24'). "
            "Avoid double-counting by selecting only non-overlapping groups. "
            "Use age_group = 'Total All Ages' for national totals."
        ),
        "join_safe_with": "All tables on `period`",
    },

    "earnings_by_qualification": {
        "description": (
            "Average weekly earnings by industry and qualification level. "
            "Annual data (June quarter each year, tagged as .Q2). Sex is not broken down "
            "(only 'Total Both Sexes' available). "
            "Best table for education premium / qualification wage gap analysis."
        ),
        "source": "Stats NZ NZ.Stat (annual survey)",
        "rows": 548,
        "period_range": ("2015.Q2", "2025.Q2"),
        "period_count": 11,
        "dimensions": {
            "industry": [
                # NZ.Stat industry groupings — much coarser than Infoshare tables
                # Do NOT JOIN on industry with any other table
                "Total All Industry Groups",
                "Agriculture, Forestry and Fishing",
                "Construction",
                "Electricity, Gas, Water and Waste Services",
                "Manufacturing",
                "Mining",
                "Retail Trade and Accommodation",
                "Wholesale Trade",
            ],
            "sex": ["Total Both Sexes"],
            "qualification": [
                "No qualification",
                "Lower secondary school qualification",
                "Upper secondary school qualification",
                "Level 1-3 post-school certificate",
                "Level 4-6 certificate or diploma",
                "Bachelors degree and level 7 qualification",
                "Postgraduate qualification",
            ],
        },
        "metrics": ["Average Weekly Earnings"],
        "notes": (
            "Earnings in NZD per week. Annual data only — do NOT join on `period` with quarterly tables "
            "unless you filter the other table to .Q2 periods only. "
            "CRITICAL: Industry names and groupings are incompatible with all Infoshare tables — never JOIN on industry. "
            "Sex breakdown not available — only 'Total Both Sexes'. "
            "Only 8 broad industry groups available (vs 17+ in Infoshare tables). "
            "Some industry × qualification combos have incomplete period coverage (NZ.Stat source gaps)."
        ),
        "join_safe_with": "Only self; if joining to other tables on `period`, filter others to LIKE '%.Q2'",
    },
}


# ---------------------------------------------------------------------------
# Prompt builder — returns a Markdown string for LLM system prompts
# ---------------------------------------------------------------------------

def get_schema_context_prompt() -> str:
    """
    Return a structured Markdown string describing all 11 tables.
    Intended use: insert into the system prompt of the SQL-generating LLM.

    Format per table:
      ## <table_name>
      <description>
      | Column | Type | Values / Notes |
      ...
      > Notes: ...
    """
    lines: list[str] = []

    lines.append("# NZ Labour Market Database — Schema Reference")
    lines.append("")
    lines.append(
        "Database: `data/stats_nz.db` (DuckDB, read-only)  "
        "Period format: `YYYY.QN` (e.g. `2025.Q4`)  "
        "All tables have columns: `period`, [dimension cols], `metric`, `value`, `source_file`"
    )
    lines.append("")
    lines.append("## ⚠️ Critical JOIN Rules")
    lines.append("")
    lines.append(
        "1. **Never JOIN tables on `industry`** — industry naming is inconsistent across tables.  \n"
        "   Safe cross-table join key: **`period` only**.  \n"
        "2. **`earnings_by_qualification` is annual** (`.Q2` periods). If joining to a quarterly table,  \n"
        "   filter the quarterly table with `WHERE period LIKE '%.Q2'`.  \n"
        "3. **Never JOIN `earnings_by_qualification` on `industry`** — NZ.Stat groupings differ from all "
        "Infoshare tables."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for table_name, meta in TABLE_METADATA.items():
        lines.append(f"## `{table_name}`")
        lines.append("")
        lines.append(f"**{meta['description']}**")
        lines.append("")
        lines.append(
            f"Source: {meta['source']} | "
            f"Rows: {meta['rows']:,} | "
            f"Periods: {meta['period_range'][0]} → {meta['period_range'][1]} "
            f"({meta['period_count']} quarters)"
        )
        lines.append("")

        # Dimensions table
        lines.append("| Column | Distinct Values |")
        lines.append("|--------|----------------|")
        lines.append(f"| `period` | `{meta['period_range'][0]}` … `{meta['period_range'][1]}` ({meta['period_count']} values) |")

        for dim, values in meta["dimensions"].items():
            val_str = ", ".join(f"`{v}`" for v in values[:6])
            if len(values) > 6:
                val_str += f" … (+{len(values)-6} more)"
            lines.append(f"| `{dim}` | {val_str} |")

        # Metrics
        metric_str = ", ".join(f"`{m}`" for m in meta["metrics"])
        lines.append(f"| `metric` | {metric_str} |")
        lines.append(f"| `value` | numeric |")
        lines.append("")

        if meta.get("notes"):
            lines.append(f"> **Notes:** {meta['notes']}")
            lines.append("")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quick smoke test — run this file directly: python -m pipeline.text_to_sql.schema_context
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"TABLE_METADATA: {len(TABLE_METADATA)} tables defined")
    for name in TABLE_METADATA:
        print(f"  ✓ {name}")
    print()
    prompt = get_schema_context_prompt()
    print(f"get_schema_context_prompt() → {len(prompt):,} characters")
    print()
    print("--- First 500 chars ---")
    print(prompt[:500])
