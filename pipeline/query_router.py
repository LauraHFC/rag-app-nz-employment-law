"""
query_router.py
===============
Intent classifier for the NZ Employment Intelligence Hub.

Classifies a user query as one of:
  "legal"  — answered by the RAG pipeline (NZ employment law)
  "data"   — answered by the Text-to-SQL pipeline (Stats NZ labour market data)
  "hybrid" — needs both pipelines; answer must be synthesised

Public API:
    classify_query(query: str, api_key: str | None = None) -> RouteResult

    RouteResult:
        .intent    : str   — "legal" | "data" | "hybrid"
        .confidence: str   — "high" | "medium" | "low"
        .reasoning : str   — one-sentence explanation
        .query     : str   — original question (passed through)

Design notes:
  - Uses claude-haiku-4-5-20251001 (fast, cheap — same as table selector)
  - Returns structured JSON parsed from Haiku's response
  - Never raises: returns RouteResult(intent="legal") as safe fallback on any error
    (RAG is safer than SQL for unknown failures)
  - Classification signals:
      data   : statistics, rates, trends, numbers, comparisons across time/industry/
               region/ethnicity/age, "how many", "what percent", "which industry",
               "average earnings", "unemployment rate", "labour cost"
      legal  : rights, obligations, dismissal, redundancy, leave entitlements,
               minimum wage law, ERA, Employment Relations Act, employment agreements,
               personal grievance, "can my employer", "am I entitled", "what does
               the law say", case law, tribunal
      hybrid : explicitly combines a legal concept WITH a statistical fact,
               e.g. "what is the legal minimum wage AND what is NZ's current
               average wage?", "what does the law say about redundancy pay and
               how common is redundancy?"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal, Optional

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROUTER_MODEL = "claude-haiku-4-5-20251001"

INTENT_TYPE = Literal["legal", "data", "hybrid"]
CONFIDENCE_TYPE = Literal["high", "medium", "low"]

_SYSTEM_PROMPT = """You are an intent classifier for the NZ Employment Intelligence Hub.

The hub has two knowledge sources:
1. RAG (legal) — New Zealand employment law: rights, obligations, ERA, dismissal,
   leave entitlements, employment agreements, personal grievance, minimum wage law,
   case law, tribunal decisions.
2. Text-to-SQL (data) — Stats NZ labour market statistics: unemployment rate,
   employment rate, participation rate, underutilisation, earnings by industry/
   gender/ethnicity/age/qualification, labour cost index, filled jobs, paid hours.
   Data covers 2015.Q1 to 2025.Q4 in quarterly snapshots.

Classify the user's query as exactly ONE of:
  "legal"  — purely about employment law, rights, obligations, or legal process
  "data"   — purely about labour market statistics, numbers, trends, or comparisons
  "hybrid" — explicitly requires BOTH a legal answer AND a statistical fact

Rules:
- Default to "legal" if unsure between legal and hybrid.
- Questions about legally defined entitlements — even when they involve a specific
  number — are "legal", NOT "data". Examples: minimum wage rate, sick leave days,
  notice period length, trial period duration, holiday entitlements. These numbers
  come from legislation, not from the Stats NZ database.
- "data" is ONLY for Stats NZ labour market statistics: unemployment rate trends,
  earnings comparisons by industry/gender/region, employment rates, labour cost
  index, filled jobs, paid hours — aggregate population-level data.
- Only use "hybrid" when the query clearly asks for BOTH legal interpretation AND
  a statistical figure in the same question (e.g. "What are the minimum wage laws
  and how do current earnings compare across industries?").
- Out-of-scope queries (e.g. housing prices, GDP) → classify as "legal" with
  low confidence so the RAG system can politely decline.

Respond with valid JSON only — no markdown, no explanation outside the JSON:
{
  "intent": "legal" | "data" | "hybrid",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence>"
}"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    query: str
    intent: INTENT_TYPE
    confidence: CONFIDENCE_TYPE
    reasoning: str
    error: Optional[str] = None  # set if classification fell back to default

    def __repr__(self) -> str:
        err = f" [fallback: {self.error}]" if self.error else ""
        return (
            f"RouteResult(intent={self.intent!r}, confidence={self.confidence!r}, "
            f"query={self.query!r:.60s}{err})"
        )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_query(
    query: str,
    api_key: str | None = None,
) -> RouteResult:
    """
    Classify a natural-language query as "legal", "data", or "hybrid".

    Args:
        query:   User's natural-language question.
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.

    Returns:
        RouteResult — always returns (never raises).
        On any error, falls back to intent="legal", confidence="low".
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    # ------------------------------------------------------------------
    # Fast-path: empty query
    # ------------------------------------------------------------------
    if not query or not query.strip():
        return RouteResult(
            query=query,
            intent="legal",
            confidence="low",
            reasoning="Empty query — defaulting to legal.",
            error="empty_query",
        )

    # ------------------------------------------------------------------
    # Call Haiku
    # ------------------------------------------------------------------
    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=ROUTER_MODEL,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        raw = message.content[0].text.strip()
    except Exception as e:
        return RouteResult(
            query=query,
            intent="legal",
            confidence="low",
            reasoning="Classification failed — defaulting to legal pipeline.",
            error=f"api_error: {e}",
        )

    # ------------------------------------------------------------------
    # Parse JSON response
    # ------------------------------------------------------------------
    try:
        # Strip markdown code fences if Haiku wraps it anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)

        intent = parsed["intent"]
        confidence = parsed["confidence"]
        reasoning = parsed["reasoning"]

        # Validate values
        if intent not in ("legal", "data", "hybrid"):
            raise ValueError(f"Unknown intent: {intent!r}")
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"  # soft fix

        return RouteResult(
            query=query,
            intent=intent,
            confidence=confidence,
            reasoning=reasoning,
        )

    except Exception as e:
        return RouteResult(
            query=query,
            intent="legal",
            confidence="low",
            reasoning="Could not parse classification — defaulting to legal pipeline.",
            error=f"parse_error: {e} | raw={raw[:200]}",
        )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    test_queries = [
        # data
        "What is the current unemployment rate in New Zealand?",
        "Which industry has the highest average hourly earnings?",
        "How has youth unemployment changed over the last 3 years?",
        # legal
        "Can my employer dismiss me without notice?",
        "How many weeks of annual leave am I entitled to in NZ?",
        "What is a personal grievance and how do I raise one?",
        # hybrid
        "What does NZ law say about minimum wage, and what is the current minimum wage rate?",
        "How common is redundancy in NZ and what are my legal rights if I'm made redundant?",
        # out-of-scope
        "What is the average house price in Auckland?",
    ]

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None

    if query:
        result = classify_query(query)
        print(result)
        print(f"  Reasoning: {result.reasoning}")
    else:
        print(f"Running {len(test_queries)} smoke test queries...\n")
        for q in test_queries:
            r = classify_query(q)
            err = f"  ⚠ {r.error}" if r.error else ""
            print(f"[{r.intent:6s} / {r.confidence:6s}] {q[:70]}{err}")
            print(f"                   → {r.reasoning}")
