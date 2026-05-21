"""
pipeline/output_guard.py
=========================
Post-generation output guard (Sprint 6 — simplified).

Sits between the synthesis model and the user. Inspects every generated
answer and rejects + regenerates non-compliant answers up to MAX_REGEN
times before falling back to a safe referral block.

Sprint 6 simplification:
  - Tear out tiered footers (LIGHT/MEDIUM/FULL) → ONE light footer for
    all non-refuse answers.
  - Tear out TEMPLATE_HEADINGS check (forced 7-section structure was the
    over-engineering smoking gun).
  - Drop the second-person catch-all (the banned-phrase set already covers
    the worst offenders and the unified prompt steers third-person framing).
  - Regen max = 1 (was 2).

Checks (in order):
  1. Banned-phrase scan (English + Chinese)
  2. LIGHT risk footer verbatim presence

Public API:
    output_guard(
        answer_text,
        domain_label,
        generate_fn,          # Callable[[str], str] violation_note → new answer
        max_regenerations=1,
    ) -> GuardResult
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LIGHT footer (the only footer Sprint 6 uses)
# ---------------------------------------------------------------------------

RISK_FOOTER_LIGHT = (
    "AI-generated · verify with the source cited above before acting on it."
)

# Identifier substring used to detect the footer's presence in answer text.
_FOOTER_IDENTIFIER = "AI-generated · verify with the source"

# ---------------------------------------------------------------------------
# Banned phrases — compiled once at module load
# ---------------------------------------------------------------------------

_BANNED_EN: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\byou\s+should\b", re.I),
     "you_should"),
    (re.compile(r"\bthe\s+best\s+thing\s+to\s+do\b", re.I),
     "best_thing_to_do"),
    (re.compile(r"\bin\s+your\s+(case|situation|circumstances)\b", re.I),
     "in_your_case"),
    (re.compile(r"\bthis\s+is\s+(a\s+)?personal\s+grievance\b", re.I),
     "characterise_pg"),
    (re.compile(r"\bthis\s+is\s+unfair\s+dismissal\b", re.I),
     "characterise_ud"),
    (re.compile(r"\bthis\s+is\s+a\s+breach\s+of\s+contract\b", re.I),
     "characterise_breach"),
    (re.compile(r"\byou\s+are\s+entitled\s+to\b", re.I),
     "entitled_to"),
    (re.compile(r"\byou\s+have\s+a\s+(strong|weak|good|poor)\s+case\b", re.I),
     "merits_opinion"),
    (re.compile(r"\bI\s+(recommend|suggest|would\s+advise|'d\s+advise)\b", re.I),
     "direct_advice"),
    (re.compile(r"\b(definitely|certainly|guaranteed|without\s+a\s+doubt)\b", re.I),
     "false_precision"),
    (re.compile(r"\bas\s+your\s+lawyer\b", re.I),
     "holding_out"),
    # Categorical legal conclusion without a nearby citation marker
    (re.compile(r"\bthis\s+is\s+(illegal|legal)\b(?![^.]{0,60}\[)", re.I),
     "uncited_legal_conclusion"),
]

_BANNED_ZH: list[tuple[re.Pattern, str]] = [
    (re.compile(r"你应该"),       "zh_you_should"),
    (re.compile(r"你需要"),       "zh_you_need"),
    (re.compile(r"你必须"),       "zh_you_must"),
    (re.compile(r"最好的办法是"), "zh_best_thing"),
    (re.compile(r"在你的情况下"), "zh_in_your_case"),
    (re.compile(r"你这种情况"),   "zh_your_situation"),
    (re.compile(r"这属于"),       "zh_characterise"),
    (re.compile(r"这就是"),       "zh_this_is"),
    (re.compile(r"你有权"),       "zh_entitled"),
    (re.compile(r"我建议"),       "zh_i_recommend"),
    (re.compile(r"我推荐"),       "zh_i_suggest"),
    (re.compile(r"一定|肯定|必然"), "zh_false_precision"),
]

BANNED_PATTERNS: list[tuple[re.Pattern, str]] = _BANNED_EN + _BANNED_ZH

# ---------------------------------------------------------------------------
# Fallback referral text (used when max regenerations are exhausted)
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATE = (
    "I wasn't able to produce a response that meets our quality standards for this question. "
    "This sometimes happens with complex or sensitive topics.\n\n"
    "For accurate, tailored information please contact:\n"
    "  • Community Law (communitylaw.org.nz) — free legal information\n"
    "  • Citizens Advice Bureau — 0800 367 222\n"
    "  • The relevant government agency for your area (IRD, Employment NZ, Tenancy Services)"
)
# No footer appended — the fallback text is itself a referral block.

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    ok: bool
    text: str
    regeneration_count: int = 0
    banned_phrase_hits: list[str] = field(default_factory=list)
    fell_back: bool = False
    violations: list[tuple[str, str | None]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check(answer_text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """
    Run all checks. Return (violations, banned_hits).

    Sprint 6: only banned-phrase + footer-presence remain.
    """
    violations: list[tuple[str, str | None]] = []
    banned_hits: list[str] = []

    # (1) Banned phrases
    for pattern, label in BANNED_PATTERNS:
        if pattern.search(answer_text):
            violations.append(("banned_phrase", label))
            banned_hits.append(label)

    # (2) LIGHT footer presence
    if _FOOTER_IDENTIFIER not in answer_text:
        violations.append(("missing_risk_footer", None))

    return violations, banned_hits


def _build_regen_instruction(violations: list[tuple[str, str | None]]) -> str:
    """Build a clear violation note to prepend to the regeneration request."""
    lines = ["Your previous answer violated the following rules. Rewrite it fixing EVERY violation:"]
    for check_name, detail in violations:
        if check_name == "banned_phrase":
            lines.append(f"  - BANNED PHRASE used: '{detail}' — replace with hedged third-person language.")
        elif check_name == "missing_risk_footer":
            lines.append(
                "  - RISK FOOTER MISSING — end your answer with this single line verbatim, "
                "preceded by a blank line and '---' on its own line:\n"
                f"    {RISK_FOOTER_LIGHT!r}\n"
                "    The single line above is the ONLY disclaimer-shaped content the answer "
                "may contain. REMOVE any 'General information disclaimer', 'Risk and limitations', "
                "or similar disclaimer paragraph you previously wrote."
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def output_guard(
    answer_text: str,
    domain_label: str,
    generate_fn: Callable[[str], str],
    max_regenerations: int = 1,
    *,
    _regen_count: int = 0,
    _cumulative_hits: list[str] | None = None,
) -> GuardResult:
    """
    Inspect answer_text and regenerate if violations are found.

    Args:
        answer_text:       The model-generated answer to inspect.
        domain_label:      e.g. "employment", "tax" — kept for the audit log.
        generate_fn:       Callable that takes a violation_note string and
                           returns a new answer string. Typically a closure
                           over the Anthropic client and synthesis prompt.
        max_regenerations: How many times to retry before falling back. Default 1.

    Returns:
        GuardResult with the final (clean or fallback) text and metadata.
    """
    if _cumulative_hits is None:
        _cumulative_hits = []

    violations, banned_hits = _check(answer_text)
    _cumulative_hits.extend(banned_hits)

    if not violations:
        # Clean — ensure the LIGHT footer is appended if the model omitted it.
        if RISK_FOOTER_LIGHT not in answer_text:
            answer_text = answer_text.rstrip() + "\n\n---\n" + RISK_FOOTER_LIGHT
        log.info(
            "[output_guard] Clean. regen_count=%d banned_hits=%s",
            _regen_count,
            _cumulative_hits,
        )
        return GuardResult(
            ok=True,
            text=answer_text,
            regeneration_count=_regen_count,
            banned_phrase_hits=_cumulative_hits,
            fell_back=False,
            violations=[],
        )

    log.warning(
        "[output_guard] Violations found (attempt %d/%d): %s",
        _regen_count + 1,
        max_regenerations + 1,
        violations,
    )

    if max_regenerations > 0:
        regen_note = _build_regen_instruction(violations)
        try:
            new_answer = generate_fn(regen_note)
        except Exception as exc:
            log.exception("[output_guard] generate_fn raised: %s", exc)
            return GuardResult(
                ok=True,
                text=_FALLBACK_TEMPLATE,
                regeneration_count=_regen_count + 1,
                banned_phrase_hits=_cumulative_hits,
                fell_back=True,
                violations=violations,
            )
        return output_guard(
            new_answer,
            domain_label,
            generate_fn,
            max_regenerations - 1,
            _regen_count=_regen_count + 1,
            _cumulative_hits=_cumulative_hits,
        )

    # Max regenerations exhausted — return safe fallback
    log.error(
        "[output_guard] Fallback triggered after %d regenerations. violations=%s",
        _regen_count,
        violations,
    )
    return GuardResult(
        ok=True,
        text=_FALLBACK_TEMPLATE,
        regeneration_count=_regen_count,
        banned_phrase_hits=_cumulative_hits,
        fell_back=True,
        violations=violations,
    )
