"""
pipeline/crisis_detector.py
============================
Pre-classifier crisis layer — runs on every incoming message BEFORE intent
classification and BEFORE retrieval.

Five lexicons, two action types:

  OVERRIDE_RESPONSE  — replace the entire response with a crisis card.
                       No retrieval, no classification, no synthesis.
  FORCE_HIGH_STAKES  — pin intent_class to HIGH_STAKES, then continue
                       normal pipeline.
  FORCE_REFUSE       — skip classifier; jump directly to
                       REFUSE_WITH_REFERRAL with the appropriate referral.

Priority order (first match wins):
  1. SELF_HARM          → OVERRIDE_RESPONSE (mental-health crisis card)
  2. FAMILY_VIOLENCE    → OVERRIDE_RESPONSE (family-violence crisis card)
  3. IMMIGRATION_ADVICE → FORCE_REFUSE
  4. CRIMINAL_DEFENCE   → FORCE_REFUSE
  5. IMMINENT_ACTION    → FORCE_HIGH_STAKES

Public API:
    detect(question: str) -> CrisisResult
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Crisis card texts (verbatim from §D.4 of risk framework)
# ---------------------------------------------------------------------------

MENTAL_HEALTH_CRISIS_CARD = """\
It sounds like you might be going through a really hard time right now.
I'm an AI assistant for general legal information — I'm not the right \
help for this, and I don't want to give you the wrong thing when you \
need real support.

Please consider reaching out — these services are free, confidential, \
and available right now:

  • 1737 — Need to talk? Free call or text, any time.
  • Lifeline Aotearoa — 0800 543 354, any time.
  • Suicide Crisis Helpline — 0508 828 865, any time.
  • In immediate danger — call 111.

If you'd like, you can come back any time and ask a general question \
about the law. I'll be here.

---
General information only — not legal advice.
Free help: Community Law (https://communitylaw.org.nz) · \
Citizens Advice Bureau 0800 367 222"""

FAMILY_VIOLENCE_CRISIS_CARD = """\
What you're describing sounds serious. Please be safe.

  • In immediate danger — call 111.
  • Women's Refuge — 0800 REFUGE (0800 733 843), any time.
  • Shine — 0508 744 633, 9am–11pm, 7 days.
  • Are You OK family violence helpline — 0800 456 450, any time.
  • Safe-to-talk (sexual harm) — 0800 044 334 or text 4334.

If it's safe to do so, you can also speak with a lawyer. Family Legal \
Aid is available, and Community Law (communitylaw.org.nz) gives free \
help. The Family Court has information on protection orders at \
justice.govt.nz/family.

---
General information only — not legal advice.
Free help: Community Law (https://communitylaw.org.nz) · \
Citizens Advice Bureau 0800 367 222"""


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
# Each entry: (action, compiled_regex)
# Evaluated in the order listed — first match wins.

_LEXICONS: list[tuple[str, str, re.Pattern]] = [
    (
        "SELF_HARM",
        "OVERRIDE_RESPONSE",
        re.compile(
            # English terms use word boundaries; Chinese terms do not (no \b support for CJK)
            r"(?:"
            r"\b(?:"
            r"kill\s+my\s*self"
            r"|end\s+it(?:\s+all)?"
            r"|want\s+it\s+to\s+end"
            r"|don.?t\s+want\s+to\s+(?:be\s+here|live|exist)"
            r"|don.?t\s+see\s+the\s+point(?:\s+anymore)?"
            r"|suicid(?:e|al)"
            r"|hurt\s+my\s*self"
            r"|harm\s+my\s*self"
            r"|take\s+my\s+(?:own\s+)?life"
            r"|end(?:ing)?\s+my\s+life"
            r"|thinking\s+about\s+(?:ending|killing)"
            r"|want\s+to\s+die"
            r"|not\s+worth\s+living"
            r")\b"
            r"|自杀|想死|不想活|伤害自己|不想继续"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "FAMILY_VIOLENCE",
        "OVERRIDE_RESPONSE",
        re.compile(
            r"\b("
            r"he.?s\s+going\s+to\s+hurt(\s+me)?"
            r"|she.?s\s+going\s+to\s+hurt(\s+me)?"
            r"|scared\s+(at\s+home|of\s+(him|her|them|my\s+partner))"
            r"|can.?t\s+leave(\s+him|\s+her|\s+them|\s+home)?"
            r"|he\s+hit\s+me"
            r"|she\s+hit\s+me"
            r"|hit\s+me\s+again"
            r"|(?:my\s+)?(?:husband|wife|spouse|boyfriend|girlfriend|ex)\s+"
            r"(?:hit|hurt|choked|threatened|strangled|beat|punched|slapped)\s+me"
            r"|my\s+partner\s+(hit|hurt|choked|threatened|strangled)\s+me"
            r"|(?:he|she).?ll\s+find\s+me"
            r"|if\s+I\s+leave\s+he.?ll"
            r"|if\s+I\s+leave\s+she.?ll"
            r"|domestic\s+violence"
            r"|family\s+violence"
            r"|protection\s+order.*urgent"
            r"|restraining\s+order.*urgent"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "IMMIGRATION_ADVICE",
        "FORCE_REFUSE",
        re.compile(
            r"\b(?:"
            r"(?:what|which)\s+visa\s+(?:should|do)\s+I"
            r"|visa\s+(?:application\s+strategy|eligibility\s+for\s+me)"
            # "how do I get/apply for/qualify for a(n)? <type> [migrant] visa"
            r"|how\s+do\s+I\s+(?:get|apply\s+for|qualify\s+for)\s+a[n]?\s+"
            r"(?:work|student|skilled|resident|visitor|partnership|investor)"
            r"(?:\s+migrant)?\s+visa"
            r"|will\s+I\s+(?:get|be\s+granted|be\s+approved\s+for)\s+a[n]?\s+visa"
            r"|immigration\s+advice\s+for\s+(?:me|my\s+(?:case|situation))"
            r"|skilled\s+migrant\s+(?:category|application|points|visa)"
            r"|residence\s+visa\s+(?:strategy|advice|chances)"
            r"|can\s+I\s+get\s+permanent\s+residency"
            r"|how\s+to\s+(?:stay|remain)\s+in\s+(?:NZ|New\s+Zealand)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "CRIMINAL_DEFENCE",
        "FORCE_REFUSE",
        re.compile(
            r"\b("
            r"how\s+do\s+I\s+(avoid|beat|get\s+out\s+of|fight)\s+(the\s+)?(charge|charges|conviction|arrest)"
            r"|what\s+(should|do)\s+I\s+(say\s+to|tell)\s+the\s+police"
            r"|how\s+to\s+plead(\s+(guilty|not\s+guilty))?"
            r"|get\s+away\s+with\s+(it|the)"
            r"|how\s+to\s+avoid\s+being\s+(charged|arrested|convicted)"
            r"|defence\s+strategy\s+for\s+my\s+(case|charge)"
            r"|should\s+I\s+(stay\s+silent|refuse\s+to\s+talk\s+to\s+police)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "IMMINENT_ACTION",
        "FORCE_HIGH_STAKES",
        re.compile(
            r"\b("
            r"(signing|sign)\s+(it\s+)?(today|tomorrow|tonight|this\s+(morning|afternoon|evening))"
            r"|(court|hearing|tribunal)\s+is\s+(today|tomorrow|on\s+monday|on\s+tuesday"
            r"|on\s+wednesday|on\s+thursday|on\s+friday|this\s+week)"
            r"|I\s+(quit|resigned|was\s+fired|was\s+dismissed|was\s+made\s+redundant)\s+today"
            r"|deadline\s+is\s+(today|tomorrow)"
            r"|I\s+have\s+to\s+(decide|sign|respond)\s+by\s+tomorrow"
            r"|court\s+date\s+(is\s+)?(today|tomorrow|this\s+week)"
            r")\b",
            re.IGNORECASE,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CrisisResult:
    triggered: bool
    category: str | None     # "SELF_HARM" | "FAMILY_VIOLENCE" | "IMMIGRATION_ADVICE" | ...
    action: str | None       # "OVERRIDE_RESPONSE" | "FORCE_HIGH_STAKES" | "FORCE_REFUSE"
    card_text: str | None    # populated for OVERRIDE_RESPONSE categories only
    domain_label: str | None # populated for FORCE_REFUSE (drives referral selection)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_NO_CRISIS = CrisisResult(
    triggered=False,
    category=None,
    action=None,
    card_text=None,
    domain_label=None,
)

_CRISIS_CARDS = {
    "SELF_HARM":       MENTAL_HEALTH_CRISIS_CARD,
    "FAMILY_VIOLENCE": FAMILY_VIOLENCE_CRISIS_CARD,
}

_FORCE_REFUSE_DOMAIN = {
    "IMMIGRATION_ADVICE": "immigration",
    "CRIMINAL_DEFENCE":   "criminal",
}


def detect(question: str) -> CrisisResult:
    """
    Scan the question against all crisis lexicons.

    Returns a CrisisResult. If `triggered` is False, continue the normal
    pipeline. Otherwise honour `action`:

      OVERRIDE_RESPONSE  → return card_text immediately, skip everything else
      FORCE_HIGH_STAKES  → pass question to classifier with intent pinned
      FORCE_REFUSE       → jump to REFUSE_WITH_REFERRAL for domain_label
    """
    for category, action, pattern in _LEXICONS:
        match = pattern.search(question)
        if match:
            log.info(
                "[crisis_detector] triggered: category=%s action=%s match=%r",
                category,
                action,
                match.group(0),
            )
            return CrisisResult(
                triggered=True,
                category=category,
                action=action,
                card_text=_CRISIS_CARDS.get(category),
                domain_label=_FORCE_REFUSE_DOMAIN.get(category),
            )

    return _NO_CRISIS
