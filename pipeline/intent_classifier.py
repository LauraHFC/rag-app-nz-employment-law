"""
pipeline/intent_classifier.py
==============================
Haiku-based two-axis query classifier.

Classifies every incoming question along two orthogonal axes:
  - intent_class : LOOKUP | ADVICE | HIGH_STAKES
  - domain_tier  : H1 | H2 | H3 | M | L

Conservative upgrade rule: if confidence < 0.75, intent is upgraded one
notch (LOOKUP → ADVICE, ADVICE → HIGH_STAKES) to reduce the risk of
treating advice-seeking queries as benign lookups.

On any API error or JSON parse failure, the classifier falls back to a
safe default: ADVICE / M / "other" / confidence=0.5 / upgraded=True.
This ensures the pipeline continues conservatively rather than crashing.

Public API:
    classify(question: str, api_key: str | None = None) -> ClassifierResult
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import anthropic

log = logging.getLogger(__name__)

CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 200   # JSON output is tiny; give a small buffer

# ---------------------------------------------------------------------------
# System prompt (verbatim from §F.1.1 of risk framework)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a routing classifier for a New Zealand legal-information service.

Given a single user message, return a JSON object with exactly four fields:
  - "intent_class": one of "LOOKUP", "ADVICE", "HIGH_STAKES"
  - "domain_tier":  one of "H1", "H2", "H3", "M", "L"
  - "domain_label": one of ["employment", "tenancy", "consumer", "family",
    "immigration", "criminal", "mental_health", "family_violence", "financial",
    "tax", "acc", "traffic", "wills_estates", "neighbour", "te_tiriti",
    "privacy", "other"]
  - "confidence": a float between 0.0 and 1.0

Definitions:
  LOOKUP      — the user is asking what the law / rule / process IS.
                Markers: "what is", "how does", "is X legal", "what are
                the rules for", "what's the deadline for".
  ADVICE      — the user is asking what they SHOULD do, whether they CAN
                do something on their own facts, or for an evaluation of
                their situation.
                Markers: "should I", "can I", "do I have a case",
                "am I entitled to", "is my landlord allowed to",
                "what would happen if".
  HIGH_STAKES — the query carries indicators of imminent irreversible
                action, urgency, or distress.
                Markers: time pressure ("tomorrow", "court date",
                "deadline"), action already taken ("I quit today",
                "I signed"), distress language, money over a threshold,
                or any criminal / immigration / family-violence trigger.

Domain tiers:
  H1 — immigration, mental_health, family_violence, financial
  H2 — family, criminal, wills_estates
  H3 — tax
  M  — employment, tenancy, acc, te_tiriti
  L  — consumer, privacy, traffic, neighbour, other

Return ONLY the JSON object. No prose, no markdown, no explanation.

Message:
"""

# Conservative upgrade table
_UPGRADE: dict[str, str] = {
    "LOOKUP":      "ADVICE",
    "ADVICE":      "HIGH_STAKES",
    "HIGH_STAKES": "HIGH_STAKES",   # already maximum
}

CONFIDENCE_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClassifierResult:
    intent_class: str    # "LOOKUP" | "ADVICE" | "HIGH_STAKES"
    domain_tier: str     # "H1" | "H2" | "H3" | "M" | "L"
    domain_label: str    # "employment" | "tax" | "immigration" | ...
    confidence: float    # 0.0–1.0
    upgraded: bool       # True if conservative upgrade was applied


# Safe fallback used on any error
_FALLBACK = ClassifierResult(
    intent_class="ADVICE",
    domain_tier="M",
    domain_label="other",
    confidence=0.5,
    upgraded=True,
)

_VALID_INTENTS  = {"LOOKUP", "ADVICE", "HIGH_STAKES"}
_VALID_TIERS    = {"H1", "H2", "H3", "M", "L"}
_VALID_LABELS   = {
    "employment", "tenancy", "consumer", "family", "immigration",
    "criminal", "mental_health", "family_violence", "financial",
    "tax", "acc", "traffic", "wills_estates", "neighbour",
    "te_tiriti", "privacy", "other",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(question: str, api_key: str | None = None) -> ClassifierResult:
    """
    Classify a user question by intent and domain tier.

    Args:
        question: The user's question.
        api_key:  Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.

    Returns:
        ClassifierResult. Never raises — falls back to safe defaults on error.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error("[intent_classifier] No API key — returning fallback.")
        return _FALLBACK

    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        raw = response.content[0].text.strip() if response.content else ""
    except Exception as exc:
        log.exception("[intent_classifier] API call failed: %s — returning fallback.", exc)
        return _FALLBACK

    # Parse JSON
    try:
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
    except (json.JSONDecodeError, IndexError) as exc:
        log.warning("[intent_classifier] JSON parse failed (%s). raw=%r — returning fallback.", exc, raw[:200])
        return _FALLBACK

    # Validate and extract fields (with safe defaults for each)
    intent_class = data.get("intent_class", "ADVICE")
    if intent_class not in _VALID_INTENTS:
        log.warning("[intent_classifier] Unknown intent_class %r — defaulting to ADVICE.", intent_class)
        intent_class = "ADVICE"

    domain_tier = data.get("domain_tier", "M")
    if domain_tier not in _VALID_TIERS:
        log.warning("[intent_classifier] Unknown domain_tier %r — defaulting to M.", domain_tier)
        domain_tier = "M"

    domain_label = data.get("domain_label", "other")
    if domain_label not in _VALID_LABELS:
        log.warning("[intent_classifier] Unknown domain_label %r — defaulting to other.", domain_label)
        domain_label = "other"

    try:
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    # Apply conservative upgrade rule
    upgraded = False
    if confidence < CONFIDENCE_THRESHOLD:
        original = intent_class
        intent_class = _UPGRADE[intent_class]
        upgraded = (intent_class != original)
        if upgraded:
            log.info(
                "[intent_classifier] Conservative upgrade: %s → %s (confidence=%.2f)",
                original,
                intent_class,
                confidence,
            )

    result = ClassifierResult(
        intent_class=intent_class,
        domain_tier=domain_tier,
        domain_label=domain_label,
        confidence=confidence,
        upgraded=upgraded,
    )
    log.info(
        "[intent_classifier] %s | tier=%s | label=%s | conf=%.2f | upgraded=%s",
        result.intent_class,
        result.domain_tier,
        result.domain_label,
        result.confidence,
        result.upgraded,
    )
    return result
