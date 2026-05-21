"""
pipeline/refusal_router.py
===========================
Pre-flight refusal check run BEFORE the agent router calls the routing model.

Sprint 6 — scope narrowed to 4 regex-detectable categories.

This is a fast, rule-based (regex) filter for the most obvious refusal
categories. The routing model handles "out of scope" decisions for everything
else; this module only catches narrow, clear-cut cases where regex is more
reliable than letting the LLM decide.

Sprint 6 retains 4 of the original 5 categories and drops the others:

Retained (4):
    ACTIVE_PROCEEDING   — personal grievance filed, ERA/court matter, IRD audit underway
    IMMIGRATION         — regulated activity in NZ (licensed advisers only)
    CRIMINAL            — criminal defence requires a qualified lawyer
    WILLS_ESTATES       — reserved area requiring a qualified lawyer

Dropped:
    OUT_OF_SCOPE_TAX    — let the routing model judge tax scope based on the
                          tool descriptions; regex was over-firing on questions
                          like "is GST a trust tax?" that mention trust keywords.
    OUT_OF_SCOPE_OTHER  — same logic: the routing model already refuses
                          out-of-scope domains with `{"refused": true,
                          "reason": "out of scope"}`.
    INDIVIDUAL_ADVICE   — second-person framing is caught by the banned-phrase
                          guard in the output stage; preempting at the input
                          stage biases conservative and was the Sprint 4
                          over-engineering pattern.
    FILING_SPECIFIC     — these are answerable as general info; only true
                          form-filling cases would refuse, and the unified
                          prompt handles them gracefully.

Note: mental_health and family_violence crises are caught by crisis_detector
before refusal_router runs. They're tracked in Sprint 6's "6 narrow refuse
categories" list but live in a different module.

Usage:
    from pipeline.refusal_router import should_refuse, RefusalResult

    result = should_refuse(question)
    if result.refused:
        return AgentResponse(
            answer=result.message,
            refused=True,
            refusal_reason=result.category.lower(),
        )
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
    category: str = ""        # ACTIVE_PROCEEDING | IMMIGRATION | CRIMINAL | WILLS_ESTATES
    message: str = ""         # human-readable response to show the user


# ---------------------------------------------------------------------------
# Pattern groups (4 narrow categories)
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

_IMMIGRATION_PATTERNS = [
    r"\b(immigration\s+visa|work\s+visa|residency\s+application|residence\s+visa)\b",
    r"\b(visa\s+application|visa\s+status|visa\s+sponsorship)\b",
    r"\b(skilled\s+migrant|accredited\s+employer)\b.{0,20}\b(visa|application)\b",
    r"\b(deportation|removal\s+order|immigration\s+detention)\b",
    r"\b(INZ|Immigration\s+New\s+Zealand)\b.{0,30}\b(application|appeal|decision)\b",
]

_CRIMINAL_PATTERNS = [
    r"\b(criminal\s+charge|criminal\s+case|criminal\s+prosecution)\b",
    r"\b(arrested|charged\s+with|prosecuted\s+for|convicted\s+of)\b",
    r"\b(bail|remand|sentencing|plea)\b.{0,30}\b(hearing|application|advice)\b",
    r"\b(police\s+interview|police\s+questioning|cautioned\s+by\s+police)\b",
    r"\b(criminal\s+defence|criminal\s+defense|criminal\s+lawyer)\b",
]

_WILLS_ESTATES_PATTERNS = [
    r"\b(will\s+drafting|drafting\s+a\s+will|write\s+a\s+will|writing\s+my\s+will)\b",
    r"\b(estate\s+planning|estate\s+administration|estate\s+distribution)\b",
    r"\b(probate|letters\s+of\s+administration|grant\s+of\s+probate)\b",
    r"\b(executor|trustee\s+of\s+estate|administrator\s+of\s+estate)\b",
    r"\b(family\s+protection\s+act|testamentary\s+(promises|claim))\b",
]


# ---------------------------------------------------------------------------
# Compiled patterns (compiled once at import)
# ---------------------------------------------------------------------------

def _compile_group(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]


_COMPILED = {
    "ACTIVE_PROCEEDING":  _compile_group(_ACTIVE_PROCEEDING_PATTERNS),
    "IMMIGRATION":        _compile_group(_IMMIGRATION_PATTERNS),
    "CRIMINAL":           _compile_group(_CRIMINAL_PATTERNS),
    "WILLS_ESTATES":      _compile_group(_WILLS_ESTATES_PATTERNS),
}

# Human-readable messages per category. (Note: agent_router rebuilds the actual
# refusal text via Haiku + REFERRAL_BANK; these messages are only used if a
# caller invokes refusal_router directly without going through agent_router.)
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
    "IMMIGRATION": (
        "Immigration advice is a regulated activity in New Zealand — only licensed "
        "immigration advisers and lawyers can provide it. For help with your visa or "
        "residency application, please contact:\n"
        "• Immigration NZ: immigration.govt.nz\n"
        "• IAA register of licensed advisers: iaa.govt.nz"
    ),
    "CRIMINAL": (
        "Advice on criminal-defence matters requires a qualified lawyer who can act for you. "
        "Please contact:\n"
        "• Police Detention Legal Assistance scheme\n"
        "• Legal Aid: legalaid.govt.nz\n"
        "• Community Law: communitylaw.org.nz"
    ),
    "WILLS_ESTATES": (
        "Will drafting and estate planning are reserved areas of legal work that require a "
        "qualified lawyer. For tailored help, please contact:\n"
        "• A NZ-qualified lawyer\n"
        "• Public Trust: publictrust.co.nz\n"
        "• Community Law: communitylaw.org.nz"
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
        RefusalResult with refused=True if the question matches one of the 4
        narrow refusal categories, or refused=False if it should proceed.
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
