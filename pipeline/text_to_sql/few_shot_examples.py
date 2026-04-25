"""
few_shot_examples.py
====================
10 representative Q&A pairs for the NZ Labour Market Text-to-SQL pipeline.

Each example is a dict with:
  question      : str  — natural-language question a user might ask
  sql           : str  — correct DuckDB SQL to answer it
  expected_desc : str  — what the result should look like (for verification/prompting)
  tables_used   : list[str]  — tables referenced in the SQL
  scenario      : str  — category label (for coverage reporting)

Coverage:
  #1  Single-table · latest snapshot          → labour_force_status
  #2  Single-table · time trend / COVID shock → labour_force_status
  #3  Single-table · regional filter          → underutilisation
  #4  Single-table · industry ranking         → employed_by_industry
  #5  Single-table · ethnicity comparison     → ethnicity_status
  #6  Cross-table · gender pay gap by industry → avg_hourly_earnings (self-join via pivot)
  #7  Single-table · age group comparison     → age_group_status
  #8  Cross-table · employment + LCI trend    → employed_by_industry + labour_cost_index
  #9  Single-table · qualification wage premium → earnings_by_qualification
  #10 Cross-table · full-time/part-time + hours → employed_ft_pt_status + filled_jobs_hours

All SQL is DuckDB-compatible (read-only SELECT statements only).
"""

from __future__ import annotations

FEW_SHOT_EXAMPLES: list[dict] = [

    # -------------------------------------------------------------------------
    # Example 1: Single-table · latest national snapshot
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · latest snapshot",
        "question": (
            "What is the current unemployment rate and labour force participation rate in New Zealand?"
        ),
        "sql": """
SELECT
    period,
    MAX(CASE WHEN metric = 'Unemployment Rate'                THEN value END) AS unemployment_rate_pct,
    MAX(CASE WHEN metric = 'Labour Force Participation Rate'  THEN value END) AS participation_rate_pct,
    MAX(CASE WHEN metric = 'Persons Employed in Labour Force' THEN value END) AS employed_thousands,
    MAX(CASE WHEN metric = 'Persons Unemployed in Labour Force' THEN value END) AS unemployed_thousands
FROM labour_force_status
WHERE period = (SELECT MAX(period) FROM labour_force_status)
GROUP BY period
""".strip(),
        "expected_desc": (
            "Single row for the most recent quarter (e.g. 2025.Q4). "
            "Unemployment rate should be a low single-digit percentage (3–7%). "
            "Participation rate typically 67–72%. "
            "Employed headcount ~2,700–2,900 (thousands)."
        ),
        "tables_used": ["labour_force_status"],
    },

    # -------------------------------------------------------------------------
    # Example 2: Single-table · time trend (COVID shock visibility)
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · time trend / event",
        "question": (
            "How did the New Zealand unemployment rate change from 2019 to 2022? "
            "Show the quarterly trend."
        ),
        "sql": """
SELECT
    period,
    value AS unemployment_rate_pct
FROM labour_force_status
WHERE metric = 'Unemployment Rate'
  AND period >= '2019.Q1'
  AND period <= '2022.Q4'
ORDER BY period
""".strip(),
        "expected_desc": (
            "16 rows (Q1 2019 → Q4 2022), one per quarter. "
            "Rate rises sharply in 2020 (COVID impact), peaking around 5–6% in 2020.Q3/Q4, "
            "then recovering toward 3–4% by late 2021/2022."
        ),
        "tables_used": ["labour_force_status"],
    },

    # -------------------------------------------------------------------------
    # Example 3: Single-table · regional dimension filter
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · regional filter",
        "question": (
            "What is the underutilisation rate in Auckland compared to the national average "
            "for the most recent quarter?"
        ),
        "sql": """
SELECT
    region,
    value AS underutilisation_rate_pct
FROM underutilisation
WHERE metric = 'Underutilisation rate'
  AND period = (SELECT MAX(period) FROM underutilisation)
  AND region IN ('Auckland', 'Total All Regional Councils')
ORDER BY region
""".strip(),
        "expected_desc": (
            "Two rows: one for Auckland and one for 'Total All Regional Councils'. "
            "Both values should be single-digit percentages (typically 8–15%). "
            "Auckland may be above or below the national average."
        ),
        "tables_used": ["underutilisation"],
    },

    # -------------------------------------------------------------------------
    # Example 4: Single-table · industry ranking
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · industry ranking",
        "question": (
            "Which industries employ the most people in New Zealand? "
            "Show the top 5 industries for the most recent quarter."
        ),
        "sql": """
SELECT
    industry,
    value AS employed_thousands
FROM employed_by_industry
WHERE metric = 'Persons Employed'
  AND period = (SELECT MAX(period) FROM employed_by_industry)
  AND industry != 'Total All Industries'
  AND industry != 'Not Specified'
ORDER BY value DESC
LIMIT 5
""".strip(),
        "expected_desc": (
            "5 rows, each with an industry name and employed headcount in thousands. "
            "Typically led by Health Care and Social Assistance, Professional Services, "
            "and Retail Trade and Accommodation. Values should be in the range 100–400 thousand."
        ),
        "tables_used": ["employed_by_industry"],
    },

    # -------------------------------------------------------------------------
    # Example 5: Single-table · ethnicity comparison
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · demographic comparison",
        "question": (
            "How does the unemployment rate for Māori compare to the national average "
            "and European workers? Show the latest quarter."
        ),
        "sql": """
SELECT
    ethnicity,
    value AS unemployment_rate_pct
FROM ethnicity_status
WHERE metric = 'Unemployment Rate'
  AND period = (SELECT MAX(period) FROM ethnicity_status)
  AND ethnicity IN ('Maori', 'European', 'Total All Ethnic Groups')
ORDER BY value DESC
""".strip(),
        "expected_desc": (
            "3 rows. Maori unemployment rate is typically 2–3x higher than European "
            "and above the national average. Values are percentages."
        ),
        "tables_used": ["ethnicity_status"],
    },

    # -------------------------------------------------------------------------
    # Example 6: Cross-table (self-join / pivot) · gender pay gap by industry
    # -------------------------------------------------------------------------
    {
        "scenario": "Cross-table · gender pay gap",
        "question": (
            "Which industry has the largest gender pay gap in average hourly earnings? "
            "Show all industries ranked by the gap for the most recent quarter."
        ),
        "sql": """
SELECT
    industry,
    MAX(CASE WHEN sex = 'Male'   THEN value END) AS male_hourly_earnings,
    MAX(CASE WHEN sex = 'Female' THEN value END) AS female_hourly_earnings,
    MAX(CASE WHEN sex = 'Male'   THEN value END)
        - MAX(CASE WHEN sex = 'Female' THEN value END) AS pay_gap_nzd,
    ROUND(
        100.0 * (
            MAX(CASE WHEN sex = 'Male' THEN value END)
            - MAX(CASE WHEN sex = 'Female' THEN value END)
        ) / NULLIF(MAX(CASE WHEN sex = 'Male' THEN value END), 0),
        1
    ) AS pay_gap_pct
FROM avg_hourly_earnings
WHERE period = (SELECT MAX(period) FROM avg_hourly_earnings)
  AND industry != 'Total All Industries'
  AND sex IN ('Male', 'Female')
GROUP BY industry
HAVING MAX(CASE WHEN sex = 'Male' THEN value END) IS NOT NULL
   AND MAX(CASE WHEN sex = 'Female' THEN value END) IS NOT NULL
ORDER BY pay_gap_nzd DESC
""".strip(),
        "expected_desc": (
            "~16 rows, one per industry (excluding 'Total All Industries'). "
            "Columns: industry, male_hourly_earnings, female_hourly_earnings, pay_gap_nzd, pay_gap_pct. "
            "Construction and Financial Services typically show large gaps. "
            "pay_gap_pct should be positive (male > female) for most industries."
        ),
        "tables_used": ["avg_hourly_earnings"],
    },

    # -------------------------------------------------------------------------
    # Example 7: Single-table · age group comparison
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · age group comparison",
        "question": (
            "How does the youth unemployment rate (15–24 years) compare to the total "
            "population unemployment rate over the last 2 years?"
        ),
        "sql": """
SELECT
    period,
    MAX(CASE WHEN age_group = 'Aged 15-24 Years' THEN value END) AS youth_unemployment_rate_pct,
    MAX(CASE WHEN age_group = 'Total All Ages'   THEN value END) AS total_unemployment_rate_pct
FROM age_group_status
WHERE metric = 'Unemployment Rate'
  AND period >= '2023.Q1'
  AND age_group IN ('Aged 15-24 Years', 'Total All Ages')
GROUP BY period
ORDER BY period
""".strip(),
        "expected_desc": (
            "~8 rows (8 quarters). Youth rate is consistently higher than total rate "
            "(typically 2–3x). Both should be percentages. "
            "Expect youth rate in the 10–18% range, total rate in 3–6%."
        ),
        "tables_used": ["age_group_status"],
    },

    # -------------------------------------------------------------------------
    # Example 8: Cross-table · employment + wage cost + gross earnings trend
    # -------------------------------------------------------------------------
    {
        "scenario": "Cross-table · employment, wage cost, and gross earnings trend",
        "question": (
            "For the Health Care and Social Assistance industry, how have employment numbers, "
            "labour costs, and total gross earnings changed since 2020? "
            "Show quarterly figures."
        ),
        "sql": """
WITH employment AS (
    SELECT period, value AS employed_thousands
    FROM employed_by_industry
    WHERE metric = 'Persons Employed'
      AND industry = 'Health Care and Social Assistance'
      AND period >= '2020.Q1'
),
lci AS (
    SELECT period, value AS labour_cost_index
    FROM labour_cost_index
    WHERE metric = 'Labour Cost Index (All Salary and Wage Rates)'
      AND industry = 'Health Care and Social Assistance'
      AND period >= '2020.Q1'
),
earnings AS (
    SELECT period, value AS gross_earnings_millions_nzd
    FROM gross_earnings
    WHERE metric = 'Total Gross Earnings'
      AND industry = 'Health Care and Social Assistance'
      AND period >= '2020.Q1'
)
SELECT
    e.period,
    e.employed_thousands,
    l.labour_cost_index,
    g.gross_earnings_millions_nzd
FROM employment e
LEFT JOIN lci     l ON e.period = l.period
LEFT JOIN earnings g ON e.period = g.period
ORDER BY e.period
""".strip(),
        "expected_desc": (
            "~24 rows (Q1 2020 → Q4 2025). "
            "employed_thousands trends upward (health sector grew significantly post-COVID). "
            "labour_cost_index increases from ~1000 baseline (2017.Q2) — likely 1100–1300 by 2025. "
            "gross_earnings_millions_nzd also trends upward (~$1,500–2,500M range). "
            "Note: employed_by_industry uses 'Health Care and Social Assistance' — "
            "confirms industry name matches across these three tables on `period` JOIN."
        ),
        "tables_used": ["employed_by_industry", "labour_cost_index", "gross_earnings"],
    },

    # -------------------------------------------------------------------------
    # Example 9: Single-table · qualification wage premium
    # -------------------------------------------------------------------------
    {
        "scenario": "Single-table · qualification wage premium",
        "question": (
            "What is the average weekly earnings difference between workers with a postgraduate "
            "qualification versus no qualification, across all industries, for the most recent year?"
        ),
        "sql": """
SELECT
    qualification,
    value AS avg_weekly_earnings_nzd
FROM earnings_by_qualification
WHERE metric = 'Average Weekly Earnings'
  AND industry = 'Total All Industry Groups'
  AND sex = 'Total Both Sexes'
  AND period = (SELECT MAX(period) FROM earnings_by_qualification)
  AND qualification IN ('Postgraduate qualification', 'No qualification')
ORDER BY value DESC
""".strip(),
        "expected_desc": (
            "2 rows: 'Postgraduate qualification' and 'No qualification'. "
            "Postgraduate earnings should be substantially higher (likely 1.5–2x). "
            "Both values in NZD per week — typically No qualification ~$500–700, "
            "Postgraduate ~$1,200–1,600."
        ),
        "tables_used": ["earnings_by_qualification"],
    },

    # -------------------------------------------------------------------------
    # Example 10: Cross-table · full-time share + paid hours (national level)
    # -------------------------------------------------------------------------
    {
        "scenario": "Cross-table · FT/PT composition + paid hours",
        "question": (
            "How has the share of full-time employment and total national paid hours changed "
            "over the last 3 years? Show annual Q4 snapshots."
        ),
        "sql": """
WITH ft_pt AS (
    SELECT
        period,
        MAX(CASE WHEN metric = 'Employed Full-Time' THEN value END) AS ft_employed_thousands,
        MAX(CASE WHEN metric = 'Employed Part-Time' THEN value END) AS pt_employed_thousands,
        MAX(CASE WHEN metric = 'Employed Total'     THEN value END) AS total_employed_thousands
    FROM employed_ft_pt_status
    WHERE period LIKE '%.Q4'
      AND period >= '2022.Q4'
    GROUP BY period
),
hours AS (
    SELECT period, value AS total_paid_hours_millions
    FROM filled_jobs_hours
    WHERE metric = 'Paid Hours (Total)'
      AND industry = 'Total All Industries'
      AND period LIKE '%.Q4'
      AND period >= '2022.Q4'
)
SELECT
    f.period,
    f.ft_employed_thousands,
    f.pt_employed_thousands,
    ROUND(100.0 * f.ft_employed_thousands / NULLIF(f.total_employed_thousands, 0), 1) AS ft_share_pct,
    h.total_paid_hours_millions
FROM ft_pt f
LEFT JOIN hours h ON f.period = h.period
ORDER BY f.period
""".strip(),
        "expected_desc": (
            "3–4 rows (Q4 2022, 2023, 2024, and possibly 2025). "
            "ft_share_pct typically 70–75%. "
            "total_paid_hours_millions ~300–450M per quarter nationally. "
            "Safe cross-table JOIN on `period` only (no industry join needed — "
            "employed_ft_pt_status has no industry dimension)."
        ),
        "tables_used": ["employed_ft_pt_status", "filled_jobs_hours"],
    },
]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_few_shot_prompt_block() -> str:
    """
    Return a formatted string of all examples for insertion into an LLM prompt.
    Format: one block per example with Question / SQL / Expected Result.
    """
    lines = ["## Few-Shot SQL Examples\n"]
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, 1):
        lines.append(f"### Example {i}: {ex['scenario']}")
        lines.append(f"**Question:** {ex['question']}")
        lines.append(f"```sql\n{ex['sql']}\n```")
        lines.append(f"**Expected result:** {ex['expected_desc']}")
        lines.append("")
    return "\n".join(lines)


def get_tables_coverage() -> dict[str, list[int]]:
    """Return {table_name: [example_numbers]} for coverage reporting."""
    coverage: dict[str, list[int]] = {}
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, 1):
        for tbl in ex["tables_used"]:
            coverage.setdefault(tbl, []).append(i)
    return coverage


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"FEW_SHOT_EXAMPLES: {len(FEW_SHOT_EXAMPLES)} examples defined")
    print()
    coverage = get_tables_coverage()
    print("Table coverage:")
    for tbl, examples in sorted(coverage.items()):
        print(f"  {tbl}: examples {examples}")
    print()
    uncovered = [
        t for t in [
            "labour_force_status", "underutilisation", "employed_ft_pt_status",
            "employed_by_industry", "filled_jobs_hours", "gross_earnings",
            "labour_cost_index", "avg_hourly_earnings", "ethnicity_status",
            "age_group_status", "earnings_by_qualification",
        ]
        if t not in coverage
    ]
    if uncovered:
        print(f"⚠️  Tables with no coverage: {uncovered}")
    else:
        print("✅ All 11 tables referenced in at least one example")
    print()
    prompt = get_few_shot_prompt_block()
    print(f"get_few_shot_prompt_block() → {len(prompt):,} characters")
