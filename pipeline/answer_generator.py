"""
answer_generator.py
===================
Unified answer generation for all three Intelligence Hub paths
(legal, data, hybrid).

Receives a RetrievalContext that may contain legal chunks, a SQL DataFrame,
or both. Makes a single Haiku call to produce a natural-language answer.

Chart visualisation uses the rule-based chart_hint from the SQL pipeline —
the LLM focuses purely on generating good prose.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import anthropic
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RetrievalContext:
    """Unified context assembled by the hub handlers before answer generation."""
    question: str
    legal_chunks: list[str] | None = None
    legal_metadatas: list[dict] | None = None
    sql_dataframe: pd.DataFrame | None = None
    sql_query: str | None = None
    tables_used: list[str] = field(default_factory=list)
    out_of_range_warning: str | None = None
    chart_hint: dict | None = None          # rule-based chart from query_pipeline


@dataclass
class GeneratedAnswer:
    """Output of the unified answer generator."""
    answer: str
    chart: dict | None = None               # final ChartConfig or None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert assistant on New Zealand employment law and labour market statistics.

You will receive context that may include:
  1. Legal document excerpts from NZ government employment resources.
  2. Statistical data from Stats NZ labour market tables.
  3. Both of the above (hybrid).

Your task: write a single, coherent, well-structured answer in natural language.

Rules:
- Be concise and practical (3-5 paragraphs max).
- Cite specific sections or documents when using legal context.
- When presenting data, summarise the key numbers in prose — do not dump raw tables.
- If one source is missing or empty, base the answer on what you have.
- Highlight key requirements, entitlements, and trends.
- Do not invent facts. Only use what is provided.
- If the answer is not in the provided context, say so clearly.
- Lead with the legal context when the user asked about rights/law.
- Lead with the data when the user asked for a number first.

Write your answer directly as plain text. Do NOT wrap it in JSON or any other structure."""


# ---------------------------------------------------------------------------
# Context formatting helpers
# ---------------------------------------------------------------------------

def _format_legal_context(chunks: list[str], metadatas: list[dict]) -> str:
    """Format legal chunks with source attributions."""
    parts = []
    for doc, meta in zip(chunks, metadatas):
        source = meta.get("source_url", "Unknown")
        title = meta.get("document_title", "Unknown")
        parts.append(f"[Source: {title} — {source}]\n{doc}")
    return "\n\n---\n\n".join(parts)


def _format_data_context(df: pd.DataFrame, tables_used: list[str]) -> str:
    """Format DataFrame as a readable text block."""
    lines = [f"Data from Stats NZ ({', '.join(tables_used)}):"]
    lines.append(df.to_string(max_rows=30, max_cols=10))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(ctx: RetrievalContext, api_key: str | None = None) -> GeneratedAnswer:
    """
    Generate a unified answer from a RetrievalContext.

    Makes one Haiku call. Falls back gracefully on errors.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    # ── Build user message ────────────────────────────────────────────────
    sections: list[str] = [f"User question: {ctx.question}"]

    if ctx.legal_chunks:
        legal_text = _format_legal_context(ctx.legal_chunks, ctx.legal_metadatas or [])
        sections.append(f"--- Legal context ---\n{legal_text}")

    if ctx.sql_dataframe is not None and not ctx.sql_dataframe.empty:
        data_text = _format_data_context(ctx.sql_dataframe, ctx.tables_used)
        sections.append(f"--- Statistical data ---\n{data_text}")
        if ctx.out_of_range_warning:
            sections.append(f"Note: {ctx.out_of_range_warning}")

    user_content = "\n\n".join(sections)

    # ── Call Haiku ────────────────────────────────────────────────────────
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        answer = msg.content[0].text.strip()
    except Exception as e:
        log.error("Answer generation LLM call failed: %s", e)
        return _fallback_answer(ctx)

    if not answer:
        return _fallback_answer(ctx)

    # Chart comes from the rule-based pipeline — LLM focuses on prose only
    return GeneratedAnswer(answer=answer, chart=ctx.chart_hint)


def _fallback_answer(ctx: RetrievalContext) -> GeneratedAnswer:
    """Build a basic answer without LLM when the API call fails entirely."""
    parts: list[str] = []

    if ctx.legal_chunks:
        parts.append("Based on the relevant employment law documents:\n")
        for i, chunk in enumerate(ctx.legal_chunks[:3], 1):
            parts.append(f"{i}. {chunk[:300]}...")

    if ctx.sql_dataframe is not None and not ctx.sql_dataframe.empty:
        data_text = _format_data_context(ctx.sql_dataframe, ctx.tables_used)
        parts.append(f"\n{data_text}")

    if not parts:
        parts.append("I was unable to generate an answer for your question.")

    if ctx.out_of_range_warning:
        parts.append(f"\nNote: {ctx.out_of_range_warning}")

    return GeneratedAnswer(
        answer="\n".join(parts),
        chart=ctx.chart_hint,
    )
