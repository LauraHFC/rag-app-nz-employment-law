"""
pipeline/refusal_router.py
===========================
Pre-flight refusal check run BEFORE the agent router is called.

This is a fast, rule-based (regex) filter for the most obvious refusal
categories.  The routing model's system prompt handles nuanced refusals;
this module catches clear-cut cases without spending a Sonnet API call.

Usage:
    from pipeline.refusal_router import should_refuse, RefusalResult

    result = should_refuse(question)
    if result.refused:
        return AgentResponse(
            answer=result.message,
            refused=True,
            refusal_reason=result.reason,
        )

Refusal categories:
    ACTIVE_PROCEEDING   — personal grievance filed, ERA/court matter, IRD audit underway
    OUT_OF_SCOPE_TAX    — trusts, international tax, double-tax treaties, FBT
    OUT_OF_SCOPE_OTHER  — criminal law, immigration (non-employment), property, insurance
    INDIVIDUAL_ADVICE   — "should I", "will I win", outcome prediction requests
    FILING_SPECIFIC     — "which box on IR3", form-line questions (IRD form-filling)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RefusalResult:
    refused: bool
    reason: str = ""          # short machine-readable reason
    category: str = ""        # ACTIVE_PROCEEDING | OUT_OF_SCOPE_TAX | etc.
    message: str = ""         # human-readable response to show the user


# ---------------------------------------------------------------------------
# Pattern groups
# ---------------------------------------------------------------------------

_ACTIVE_PROCEEDING_PATTERNS = [
    # Employment proceedings
    r"\b(personal\s+grievance|PG)\b.{0,30}\b(filed|raised|lodged|submitted|already)\b",
    r"\b(already\s+filed|have\s+filed|have\s+raised)\b.{0,40}\b(grievance|ERA|mediation)\b",
    r"\bmediation\s+(is\s+)?(scheduled|happening|in\s+progress|underway|booked)\b",
    r"\b(my|our|the)\s+ERA\s+(case|matter|hearing|proceeding)\b",
    r"\bERA\s+(case|matter|hearing|proceeding)\b.{0,30}\b(filed|lodged|underway|active|ongoing|scheduled)\b",
    r"\bemployment\s+court\b.{0,20}\b(case|matter|proceeding|hearing)\b.{0,20}\b(filed|underway|active|scheduled)\b",
    r"\bmy\s+(case|matter)\b.{0,30}\b(ERA|court|mediation|tribunal)\b",
    # IRD/tax proceedings
    r"\bIRD\s+(audit|review|investigation)\b.{0,20}\b(underway|started|opened|ongoing|received)\b",
    r"\b(received|got|issued).{0,20}\b(NOPA|NORP|notice\s+of\s+proposed\s+adjustment)\b",
    r"\btax\s+dispute\b.{0,20}\b(filed|lodged|underway|in\s+progress)\b",
    r"\bin\s+(dispute|litigation)\s+with\s+(IRD|Inland\s+Revenue)\b",
]

_OUT_OF_SCOPE_TAX_PATTERNS = [
    r"\b(trusts?|family\s+trusts?|discretionary\s+trusts?|bare\s+trusts?)\b",
    r"\b(double[\s-]tax\s+agreement|DTA|double[\s-]tax\s+treat(y|ies))\b",
    r"\b(international\s+tax|cross[\s-]border\s+tax|transfer\s+pricing)\b",
    r"\b(foreign\s+tax\s+credit|FTC)\b",
    r"\b(thin\s+capitalisation|interest\s+deductibility\s+limit)\b",
    r"\b(FBT|fringe\s+benefit\s+tax)\b",
    r"\b(estate\s+duty|gift\s+duty|land\s+transfer\s+tax|brightline)\b",
    r"\b(crypto(currency)?|bitcoin|NFT).{0,20}\b(tax|GST|return)\b",
]

_OUT_OF_SCOPE_OTHER_PATTERNS = [
    r"\b(criminal|criminal\s+law|criminal\s+charge|prosecution)\b",
    r"\b(family\s+court|custody|divorce|separation\s+agreement)\b",
    r"\b(resource\s+consent|RMA|resource\s+management)\b",
    r"\b(property\s+law|conveyancing|title\s+search)\b",
    r"\b(immigration\s+visa|work\s+visa|residency\s+application)\b",
    r"\b(insurance\s+claim|ACC\s+dispute|personal\s+injury\s+claim)\b",
    r"\b(company\s+law|shareholders?\s+agreement|constitution)\b",
]

_INDIVIDUAL_ADVICE_PATTERNS = [
    r"\bwill\s+I\s+win\b",
    r"\bdo\s+I\s+have\s+a\s+(good\s+)?case\b",
    r"\bshould\s+I\s+(sue|take\s+them\s+to|file|raise)\b",
    r"\bwhat\s+are\s+my\s+chances\b",
    r"\bwill\s+the\s+(ERA|court|IRD)\s+(rule|decide|find)\s+in\s+my\s+fav(ou?r)?\b",
    r"\bhow\s+much\s+(compensation|damages|money)\s+will\s+I\s+get\b",
]

_FILING_SPECIFIC_PATTERNS = [
    r"\bwhich\s+(box|field|line|section)\s+on\s+\b(IR\d+|my\s+tax\s+return)\b",
    r"\bhow\s+do\s+I\s+fill\s+(in|out)\s+(IR\d+|the\s+IR\d+|my\s+IR\d+)\b",
    r"\bIR\d{1,4}\s+(box|field|question)\s+\d+\b",
]


# ---------------------------------------------------------------------------
# Compiled patterns (compiled once at import)
# ---------------------------------------------------------------------------

def _compile_group(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]


_COMPILED = {
    # Check outcome-prediction first — "will I win my ERA case?" is INDIVIDUAL_ADVICE,
    # not ACTIVE_PROCEEDING (even though it mentions ERA).
    "INDIVIDUAL_ADVICE":  _compile_group(_INDIVIDUAL_ADVICE_PATTERNS),
    "FILING_SPECIFIC":    _compile_group(_FILING_SPECIFIC_PATTERNS),
    "ACTIVE_PROCEEDING":  _compile_group(_ACTIVE_PROCEEDING_PATTERNS),
    "OUT_OF_SCOPE_TAX":   _compile_group(_OUT_OF_SCOPE_TAX_PATTERNS),
    "OUT_OF_SCOPE_OTHER": _compile_group(_OUT_OF_SCOPE_OTHER_PATTERNS),
}

# Human-readable messages per category
_MESSAGES = {
    "ACTIVE_PROCEEDING": (
        "This service provides general information about NZ employment law and tax law. "
        "It looks like your question relates to an active proceeding or dispute. "
        "For matters already before the ERA, Employment Court, or IRD, please seek "
        "advice from a NZ-qualified lawyer or tax advisor, or contact:\n"
        "• Employment NZ: 0800 20 90 20\n"
        "• IRD: 0800 775 247\n"
        "• Citizens Advice Bureau: 0800 367 222"
    ),
    "OUT_OF_SCOPE_TAX": (
        "This service covers NZ income tax, GST, KiwiSaver, and employer tax obligations. "
        "The topic you've asked about (such as trusts, international tax, or FBT) is outside "
        "our current scope. For specialist tax advice, please consult a chartered accountant "
        "or tax advisor, or contact IRD on 0800 775 247."
    ),
    "OUT_OF_SCOPE_OTHER": (
        "This service covers NZ employment law and NZ tax law. "
        "The topic you've asked about appears to be outside these areas. "
        "For other legal matters, please contact a NZ-qualified lawyer or "
        "Citizens Advice Bureau (0800 367 222)."
    ),
    "INDIVIDUAL_ADVICE": (
        "This service provides general information about NZ employment law and tax law — "
        "it cannot predict outcomes for specific situations or provide legal/tax advice. "
        "For advice about your specific situation, please contact:\n"
        "• Employment NZ: 0800 20 90 20\n"
        "• IRD: 0800 775 247\n"
        "• A NZ-qualified employment lawyer or tax advisor"
    ),
    "FILING_SPECIFIC": (
        "This service provides general guidance on NZ tax law, but cannot assist with "
        "specific form fields or tax return line items. For help completing your tax return, "
        "please contact IRD on 0800 775 247 or visit ird.govt.nz."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_refuse(question: str) -> RefusalResult:
    """
    Check whether a question should be refused before calling the agent router.

    Args:
        question: The user's raw question string.

    Returns:
        RefusalResult with refused=True if the question matches a refusal category,
        or refused=False if it should proceed to the agent router.
    """
    for category, patterns in _COMPILED.items():
        for pattern in patterns:
            if pattern.search(question):
                return RefusalResult(
                    refused=True,
                    reason=category.lower().replace("_", " "),
                    category=category,
                    message=_MESSAGES[category],
                )

    return RefusalResult(refused=False)
