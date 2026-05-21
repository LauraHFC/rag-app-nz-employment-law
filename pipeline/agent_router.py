"""
pipeline/agent_router.py
========================
Top-level Claude tool-use agent router for the NZ Employment & Tax Law service.
v3 (Sprint 6) — risk-control architecture torn down and rebuilt.

Sprint 4's 9-layer pipeline forced every (M/L, ADVICE) question through a
7-section legal-memo template that turned simple questions into walls of
text. Sprint 6 replaces it with: one unified synthesis prompt that lets
answer shape follow question complexity + a narrow refuse set + a single
LIGHT footer.

Execution order:
    1. crisis_detector.detect()         — pre-classifier crisis layer
       ├─ OVERRIDE_RESPONSE → return crisis card immediately
       ├─ FORCE_REFUSE      → jump to REFUSE (with referrals)
       └─ (FORCE_HIGH_STAKES removed — no longer needed without intent classifier)
    2. refusal_router.should_refuse()   — narrow regex refuse (6 categories)
    3. _route()                         → Sonnet picks which tool(s) to call
    4. _execute_tools()                 → parallel retrieval
    5. _synthesise()                    → Haiku generates answer via UNIFIED_PROMPT
    6. output_guard.output_guard()      → banned-phrase + footer checks

Public API:
    run(question: str, api_key: str | None = None) -> AgentResponse

    AgentResponse fields (Sprint 6 — slimmed):
        answer              str
        sources             list[dict]
        domains_used        list[str]
        tool_calls          list[dict]
        refused             bool
        refusal_reason      str | None
        chart               dict | None
        domain_label        str           "employment" | "tax" | ...
        crisis_route_fired  bool
        regeneration_count  int
        banned_phrase_hits  list[str]

    Removed in Sprint 6: intent_class, domain_tier, routing_outcome,
    classifier_confidence, risk_badge.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import anthropic

from api.observability import observe

from pipeline.crisis_detector import detect as crisis_detect, CrisisResult
from pipeline.output_guard import output_guard

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

ROUTING_MODEL   = "claude-sonnet-4-6"
SYNTHESIS_MODEL = "claude-haiku-4-5-20251001"

MAX_TOKENS_ROUTING   = 1024
MAX_TOKENS_SYNTHESIS = 2000

# ---------------------------------------------------------------------------
# Domain referral bank (used by REFUSE branches only)
# ---------------------------------------------------------------------------

REFERRAL_BANK: dict[str, dict[str, list[str]]] = {
    "employment": {
        "free": ["MBIE Employment Service 0800 20 90 20", "Community Law (communitylaw.org.nz)"],
        "paid": ["Employment lawyer", "Union representative"],
    },
    "tax": {
        "free": ["IRD (ird.govt.nz) — free helpline 0800 775 247"],
        "paid": ["Chartered accountant", "Tax lawyer"],
    },
    "immigration": {
        "free": ["Immigration NZ (immigration.govt.nz)", "IAA register of licensed advisers (iaa.govt.nz)"],
        "paid": ["Licensed immigration adviser", "Immigration lawyer"],
    },
    "criminal": {
        "free": ["Police Detention Legal Assistance scheme", "Legal Aid (legalaid.govt.nz)", "Community Law"],
        "paid": ["Criminal lawyer", "Public Defence Service"],
    },
    "wills_estates": {
        "free": ["Community Law (communitylaw.org.nz)", "Citizens Advice Bureau 0800 367 222"],
        "paid": ["Lawyer", "Public Trust (publictrust.co.nz)"],
    },
    "mental_health": {
        "crisis": ["1737 — free call or text, any time", "Lifeline Aotearoa 0800 543 354", "Suicide Crisis Helpline 0508 828 865", "111 in immediate danger"],
        "paid": ["GP", "Psychologist", "Psychiatrist"],
    },
    "family_violence": {
        "crisis": ["111 in immediate danger", "Women's Refuge 0800 REFUGE (0800 733 843)", "Shine 0508 744 633", "Are You OK 0800 456 450"],
        "paid": ["Family violence lawyer", "Community Law"],
    },
    "other": {
        "free": ["Community Law (communitylaw.org.nz)", "Citizens Advice Bureau 0800 367 222"],
        "paid": ["NZ Law Society 'Find a Lawyer' (lawsociety.org.nz)"],
    },
}

# Refuse reasons keyed by the narrow-set categories. Sprint 6 collapses the old
# H1/H2/H3 tier reasons into 6 explicit categories (see refusal_router for the
# matching regex set).
_REFUSE_REASONS: dict[str, str] = {
    "active_proceeding": "this question relates to an active proceeding (ERA, court, mediation, or IRD audit) — that requires advice from someone who can act for you",
    "immigration":       "immigration advice is a regulated activity in New Zealand — only licensed immigration advisers can provide it",
    "criminal":          "advice on criminal-defence strategy requires a qualified lawyer",
    "wills_estates":     "will drafting and estate planning are reserved areas that require a qualified lawyer",
    "mental_health":     "this service is not equipped to help with mental-health crises",
    "family_violence":   "your safety is what matters most right now",
}

# ---------------------------------------------------------------------------
# Tool schemas (unchanged from v2)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "search_employment_law",
        "description": (
            "Search the NZ employment law knowledge base. Use for questions about: "
            "employment agreements, dismissals, redundancy, leave entitlements (annual, sick, parental), "
            "trial periods, minimum wage, restraints of trade, personal grievances, "
            "health & safety obligations, contractor vs employee distinction, "
            "and other employment-relations topics governed by the Employment Relations Act 2000 "
            "and related NZ employment legislation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query rephrased for retrieval — not the user's verbatim question. "
                        "Focus on the legal concept or entitlement being asked about."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_tax_law",
        "description": (
            "Search the NZ tax law knowledge base. Use for questions about: "
            "income tax (PAYE, tax brackets, deductions), GST (registration, filing, rates), "
            "KiwiSaver (employer/employee contributions, opt-out, enrolment), "
            "employer tax obligations (payday filing, deduction codes), "
            "property income tax including the bright-line test on residential property sales, "
            "and other tax topics governed by the Income Tax Act 2007, "
            "GST Act 1985, and Tax Administration Act 1994. "
            "OUT OF SCOPE: trusts, cross-border/double-tax treaties, FBT (fringe benefit tax), "
            "international tax planning, and active IRD audits or disputes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query rephrased for retrieval — focus on the tax rule, "
                        "rate, or obligation being asked about."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_labour_market_stats",
        "description": (
            "Query NZ labour market statistics from Stats NZ and MBIE databases. "
            "Use ONLY when the user asks for quantitative labour market data, for example: "
            "average wages by industry or region, unemployment or employment rates, "
            "hours worked trends, demographic breakdowns of the workforce. "
            "Do NOT use for legal or tax questions — use the search tools instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "Natural-language question describing the statistical query. "
                        "Be specific about the metric, industry, region, and time period if relevant."
                    ),
                }
            },
            "required": ["question"],
        },
    },
]

# ---------------------------------------------------------------------------
# Routing system prompt (light edits to match Sprint 6 — no tier vocabulary)
# ---------------------------------------------------------------------------

ROUTING_SYSTEM_PROMPT = """\
You are a routing agent for an NZ employment law and tax law information service.
You have three tools available. Your ONLY job is to decide which tool(s) to call.

ABSOLUTE RULES — follow these without exception:
1. You MUST call at least one tool. You are not permitted to answer from prior knowledge.
2. If the question can be answered with a single tool, call only that tool.
3. If the question genuinely spans two domains (e.g. the tax treatment of a redundancy payment,
   or employer obligations that involve both employment law and PAYE), call both relevant tools.
4. If the question is entirely outside NZ employment law, NZ tax law, and NZ labour market
   statistics, do NOT call any tool. Instead, your ENTIRE response must be exactly this one
   JSON line and NOTHING else — no sentence before it, no explanation after it:
     {"refused": true, "reason": "out of scope"}
   Any prose around the JSON will break routing.
5. Never use the words "legal advice", "tax advice", "your case", or predict outcomes.
6. Do not ask the user for clarification — make the best routing decision you can.
"""

# ---------------------------------------------------------------------------
# Synthesis — ONE unified prompt + ONE refuse prompt (Sprint 6)
# ---------------------------------------------------------------------------
# Design notes:
# * Sprint 4's 4 templates were collapsed into ONE prompt that lets answer
#   shape follow question complexity. The model decides length; we don't.
# * LIGHT footer (single line) is the only disclaimer for non-refuse answers.
# * No mandatory section headings. Light sub-sections allowed for genuinely
#   complex questions only.

_BANNED_LIST_STR = (
    "you should, the best thing to do is, in your case, in your situation, "
    "this is a personal grievance, this is unfair dismissal, you are entitled to, "
    "you have a strong/weak case, I recommend, I suggest, definitely, certainly, guaranteed, "
    "as your lawyer, 你应该, 你有权, 我建议"
)

_PREFERRED_LIST_STR = (
    "The relevant law is set out in [Act] section [X] which provides that... | "
    "A tribunal/court considering a matter of this kind would typically weigh factors such as... | "
    "Options that exist under the relevant Act include... | "
    "The general position is ... although the outcome in any specific case depends on the facts. | "
    "There is a strict statutory deadline of [X days] in this area."
)

_LIGHT_FOOTER_INSTR = (
    "End your answer with this single line VERBATIM, on its own line, "
    "preceded by a blank line and a horizontal rule:\n\n"
    "---\n"
    "AI-generated · verify with the source cited above before acting on it."
)

UNIFIED_SYNTHESIS_PROMPT = f"""\
You are a writing assistant for a New Zealand legal-information service.
You are NOT a lawyer. You provide general information only.

CORE PRINCIPLE — answer shape follows question complexity:
  • Simple lookup question (e.g. "what is the GST rate?", "how much annual leave do I get?") →
    answer in 1–3 sentences of plain prose. No headings. No bullet lists.
    HARD LIMIT: 120 words. Do not exceed.
  • Mid-complexity question (e.g. "my employer wants to change my start time — what are my rights?") →
    2–4 prose paragraphs. NO mandatory section headings. Cover what the law generally provides
    + the most important practical point (e.g. a key statutory deadline) + where to go for tailored help.
    Inline references are fine; section headings are NOT.
    HARD LIMIT: 300 words. Do not exceed.
  • Genuinely complex question (multi-issue, multi-statute, or high-stakes decision-making) →
    longer prose. Do NOT impose a fixed 6- or 7-section template. You may use a small number of
    sub-headings ONLY if each heading names content specific to THIS question. Generic, reusable
    template headings ("Employment status", "Tax obligations", "Overview", "Background") are
    BANNED — they signal a boilerplate memo, not an answer. If you cannot write a heading that
    is specific to this question, use a prose transition instead.
    HARD LIMIT: 500 words. Do not exceed.

Use the MINIMUM prose necessary to answer the question accurately. Padding is a failure mode.

CITING:
- Cite the source ONCE at the end of the relevant sentence in the form (source: <Act / section>)
  or as an inline reference: "the Employment Relations Act 2000 s103 provides that..."
- Do NOT quote source paragraphs verbatim. Do NOT include block quotes.
- Do NOT introduce the answer with "According to the retrieved sources..." or similar.

TONE & FRAMING — strict:
1. Use ONLY the retrieved context. If it does not support the answer, say so in one sentence
   and point the user to the right service.
2. Use third-person framing throughout: "an employee in this position", "the relevant Act
   provides", "a tribunal would typically weigh...". Avoid second-person speculation about
   the user's specific facts.
3. Do NOT use banned phrases: {_BANNED_LIST_STR}
4. Preferred phrasing patterns include: {_PREFERRED_LIST_STR}
5. Do NOT add a "General information disclaimer", "Risk and limitations", or similar
   labelled disclaimer paragraph anywhere in the answer. The single-line footer below is the
   ONLY disclaimer the answer carries.

SUBSTANCE — what a complete answer must do:
6. If the question raises MULTIPLE distinct legal sources or issues, name each one explicitly
   in prose (e.g. "two things are in play here: the Holidays Act 2003 governs the leave
   balance, and the Employment Relations Act 2000 governs the notice period"). Before writing,
   internally list every sub-issue the question raises and make sure each is addressed — a
   missed sub-issue is the most common failure of these answers.
7. If the question describes a situation with real emotional weight (job loss, harassment,
   serious illness, a dispute that has escalated), acknowledge that weight in ONE plain
   sentence before moving to the legal substance. Do not dwell on it; do not reflect it back
   repeatedly.
8. Do NOT tell the user what they "should" or "must" do, or what is "important" for them
   personally to do. Frame practically instead: "options at this point include...",
   "one common next step is...", "the Act allows...". You give information; the user decides.
9. For any answer that reports a statistic or data point, state the time period it covers,
   the source, the unit, and what is being measured (e.g. "median, not mean"). A number
   without these four things is not a usable answer.
10. If the retrieved context only PARTIALLY covers the question, report what IS supported,
    then name the specific gap in one sentence and point the user to the right service for
    that part. Do NOT refuse or punt on the whole question because one part is missing.

FOOTER:
{_LIGHT_FOOTER_INSTR}
"""

REFUSE_PROMPT = f"""\
You are a writing assistant for a New Zealand legal-information service.
You cannot help with this question — it falls into a narrow category that
this service is not safe to answer.

Task: Write a warm, brief refusal with referrals, using this shape:

[1] Acknowledge the question briefly and warmly (one sentence).
[2] Explain in one sentence WHY this service cannot answer it. If the question
    clearly falls into an identifiable area of law (criminal, immigration,
    tenancy/residential-tenancy, or family law), name that area in this sentence —
    it helps the user understand why the referral fits and where to look next.
[3] Provide the referrals supplied below.
[4] Offer one factual, non-advisory thing the service CAN do
    (e.g. "I can explain what the relevant Act covers in general terms — would that help?").

RULES:
1. Be warm, not cold. The user may be in distress.
2. Do NOT attempt to answer the underlying question.
3. Do NOT use banned phrases: {_BANNED_LIST_STR}
4. Do NOT append a separate disclaimer footer — your refusal text + the
   referrals you list ARE the disclaimer.
"""

# ---------------------------------------------------------------------------
# Result dataclass (Sprint 6 — slimmed)
# ---------------------------------------------------------------------------

@dataclass
class AgentResponse:
    """Structured response from the agent router (Sprint 6)."""
    # Core answer
    answer: str
    sources: list[dict] = field(default_factory=list)
    domains_used: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    chart: dict | None = None
    # Slim risk-control metadata (no intent_class / domain_tier / routing_outcome /
    # classifier_confidence / risk_badge — torn out in Sprint 6).
    domain_label: str = "other"
    crisis_route_fired: bool = False
    regeneration_count: int = 0
    banned_phrase_hits: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Refusal builder
# ---------------------------------------------------------------------------

def _build_refusal_text(
    client: anthropic.Anthropic,
    domain_label: str,
    reason: str,
) -> str:
    """Generate a refusal response via Haiku with domain referrals injected."""
    refs = REFERRAL_BANK.get(domain_label, REFERRAL_BANK["other"])
    crisis_block = ""
    free_block = ""
    paid_block = ""
    if refs.get("crisis"):
        crisis_block = "Urgent / crisis support:\n" + "\n".join(f"  • {r}" for r in refs["crisis"])
    if refs.get("free"):
        free_block = "Free services:\n" + "\n".join(f"  • {r}" for r in refs["free"])
    if refs.get("paid"):
        paid_block = "Professional services:\n" + "\n".join(f"  • {r}" for r in refs["paid"])

    referral_text = "\n\n".join(filter(None, [crisis_block, free_block, paid_block]))

    user_msg = (
        f"The user asked about: {domain_label}.\n"
        f"Reason this service cannot help: {reason}\n\n"
        f"Referrals to include:\n{referral_text}\n\n"
        "Write the refusal now."
    )

    try:
        resp = client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=600,
            system=REFUSE_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text if resp.content else ""
    except Exception as exc:
        log.exception("[agent_router] Refusal generation failed: %s", exc)
        text = (
            f"I'm not able to help with that question — {reason}.\n\n"
            f"{referral_text}\n\n"
        )

    return text


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_tool(tool_name: str, tool_input: dict) -> Any:
    if tool_name == "search_employment_law":
        from pipeline.tools.employment_search import execute
        return execute(tool_input)
    elif tool_name == "search_tax_law":
        from pipeline.tools.tax_search import execute
        return execute(tool_input)
    elif tool_name == "query_labour_market_stats":
        from pipeline.tools.labour_stats import execute
        return execute(tool_input)
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


# ---------------------------------------------------------------------------
# Routing step (Sonnet picks tools)
# ---------------------------------------------------------------------------

@observe(name="route")
def _route(
    client: anthropic.Anthropic,
    question: str,
) -> tuple[list[dict], bool, str | None]:
    response = client.messages.create(
        model=ROUTING_MODEL,
        max_tokens=MAX_TOKENS_ROUTING,
        system=ROUTING_SYSTEM_PROMPT,
        tools=TOOLS,
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": question}],
    )

    tool_calls = []
    refused = False
    refusal_reason = None

    for block in response.content:
        if block.type == "tool_use":
            tool_calls.append({"name": block.name, "input": block.input})
        elif block.type == "text":
            text = block.text.strip()
            # The routing model is instructed to emit ONLY a JSON line for refusals,
            # but it sometimes wraps the JSON in a sentence ("This is out of scope: {...}").
            # A strict json.loads() on the whole block then fails and the refusal is
            # silently lost. Extract the first {...} object and parse that instead.
            parsed = None
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                m = re.search(r"\{.*?\}", text, re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
            if isinstance(parsed, dict) and parsed.get("refused"):
                refused = True
                refusal_reason = parsed.get("reason", "out of scope")

    if not tool_calls and not refused:
        log.warning("[agent_router] Routing model returned no tool calls and no refusal — defaulting to refusal.")
        refused = True
        refusal_reason = "Could not determine which knowledge source to use."

    return tool_calls, refused, refusal_reason


# ---------------------------------------------------------------------------
# Tool execution (parallel)
# ---------------------------------------------------------------------------

@observe(name="retrieve")
def _execute_tools(tool_calls: list[dict]) -> list[Any]:
    from pipeline.tools.employment_search import ToolResult

    if not tool_calls:
        return []

    results: list[Any] = [None] * len(tool_calls)
    with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
        future_to_idx = {
            executor.submit(_dispatch_tool, tc["name"], tc["input"]): i
            for i, tc in enumerate(tool_calls)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                log.exception("Tool execution failed for %s: %s", tool_calls[idx]["name"], exc)
                results[idx] = ToolResult(
                    tool_name=tool_calls[idx]["name"],
                    success=False,
                    error=str(exc),
                )
    return results


# ---------------------------------------------------------------------------
# Synthesis step
# ---------------------------------------------------------------------------

@observe(as_type="generation", name="generate")
def _synthesise(
    client: anthropic.Anthropic,
    question: str,
    tool_results: list,
    domain_label: str,
) -> tuple[str, list[dict], list[str], dict | None, int, list[str]]:
    """
    Synthesise a cited answer from tool results using the UNIFIED prompt.

    Returns: (answer_text, sources, domains_used, chart, regen_count, banned_hits)
    """
    all_chunks: list[str] = []
    all_sources: list[dict] = []
    domains_used: list[str] = []
    chart = None
    failed_tools: list[Any] = []  # ToolResults that errored — kept for diagnostics

    for tr in tool_results:
        if not tr:
            continue
        if not tr.success:
            # A failed tool is NOT the same as a tool that returned no rows.
            # Keep it so that, if we end up with zero chunks, we can tell a
            # systemic failure (duckdb missing, SQL error) apart from a
            # genuine "no data" result and surface the real error instead of
            # the misleading generic fallback.
            failed_tools.append(tr)
            continue
        all_chunks.extend(tr.chunks)
        all_sources.extend(tr.sources)
        if tr.domain and tr.domain not in domains_used:
            domains_used.append(tr.domain)
        if tr.domain == "labour_stats" and tr.raw and tr.raw.get("chart"):
            chart = tr.raw["chart"]

    # Deduplicate sources
    seen_urls: set[str] = set()
    deduped_sources: list[dict] = []
    for s in all_sources:
        url = s.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            deduped_sources.append(s)

    if not all_chunks:
        # Zero usable chunks. Two very different situations land here:
        #
        #   (a) Every tool ran fine but genuinely retrieved nothing — the
        #       knowledge base really does not cover the question.
        #   (b) A tool FAILED (missing dependency, SQL error, etc.). This is a
        #       system fault, not an absence of knowledge, and must not be
        #       disguised as one — doing so hides outages and sends users a
        #       false "we don't know" when the real fix is operational.
        #
        # Case (b): log every tool error loudly and tell the user the service
        # hit a problem (truthful) rather than that the knowledge base is empty.
        if failed_tools:
            for tr in failed_tools:
                log.error(
                    "[agent_router] Tool '%s' failed during synthesis — %s",
                    getattr(tr, "tool_name", "?"),
                    getattr(tr, "error", None),
                )
            tool_error = (
                "This service hit a problem while looking up the information "
                "needed to answer your question. This is a temporary system "
                "issue, not a limit of what the service covers.\n\n"
                "Please try again shortly. If it keeps happening, you can also "
                "contact:\n"
                "  • Employment NZ 0800 20 90 20\n"
                "  • IRD 0800 775 247\n"
                "  • Community Law (communitylaw.org.nz)"
            )
            return tool_error, deduped_sources, domains_used, chart, 0, []

        # Case (a): genuine no-coverage — treat as refuse-equivalent. No footer
        # (the referral block IS the disclaimer surface).
        no_info = (
            "The knowledge base does not contain enough information to answer this question.\n\n"
            "For accurate information please contact:\n"
            "  • Employment NZ 0800 20 90 20\n"
            "  • IRD 0800 775 247\n"
            "  • Community Law (communitylaw.org.nz)"
        )
        return no_info, deduped_sources, domains_used, chart, 0, []

    # Format context
    context_parts = [f"[Source {i}]\n{chunk}" for i, chunk in enumerate(all_chunks, 1)]
    context_text = "\n\n".join(context_parts)

    user_message = (
        f"Question: {question}\n\n"
        f"Retrieved context:\n{context_text}\n\n"
        "Write your answer now. Remember: shape follows question complexity, "
        "use the minimum prose necessary, end with the single-line footer."
    )

    def _call_model(extra_instruction: str = "") -> str:
        msg = user_message
        if extra_instruction:
            msg = extra_instruction + "\n\n" + msg
        resp = client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=MAX_TOKENS_SYNTHESIS,
            system=UNIFIED_SYNTHESIS_PROMPT,
            messages=[{"role": "user", "content": msg}],
        )
        return resp.content[0].text if resp.content else ""

    initial_answer = _call_model()

    # Run output guard (banned phrases + LIGHT footer presence only, regen max=1)
    guard_result = output_guard(
        answer_text=initial_answer,
        domain_label=domain_label,
        generate_fn=_call_model,
        max_regenerations=1,
    )

    # Surface guard-loop signals onto the generate span
    try:
        from api.observability import tag_current_span
        tag_current_span(
            regeneration_count=guard_result.regeneration_count,
            banned_phrase_hits=guard_result.banned_phrase_hits,
            domain_label=domain_label,
        )
    except Exception:
        pass

    return (
        guard_result.text,
        deduped_sources,
        domains_used,
        chart,
        guard_result.regeneration_count,
        guard_result.banned_phrase_hits,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@observe(name="agent_query")
def run(question: str, api_key: str | None = None) -> AgentResponse:
    """
    Run the full Sprint 6 agent pipeline for a user question.

    Args:
        question: The user's question.
        api_key:  Anthropic API key (falls back to ANTHROPIC_API_KEY env var).

    Returns:
        AgentResponse with slim risk-control metadata.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is required.")

    client = anthropic.Anthropic(api_key=key)

    # ── Step 1: Crisis detection ───────────────────────────────────────────
    log.info("[agent_router] Question: %s", question[:120])
    crisis: CrisisResult = crisis_detect(question)

    if crisis.triggered:
        if crisis.action == "OVERRIDE_RESPONSE":
            log.info("[agent_router] Crisis override: %s", crisis.category)
            return AgentResponse(
                answer=crisis.card_text or "",
                refused=True,
                refusal_reason=f"crisis: {crisis.category}",
                domain_label=crisis.domain_label or "mental_health",
                crisis_route_fired=True,
            )

        if crisis.action == "FORCE_REFUSE":
            log.info("[agent_router] Crisis force-refuse: %s", crisis.category)
            domain = crisis.domain_label or "other"
            reason = _REFUSE_REASONS.get(domain, "this falls outside what this service can safely answer")
            answer = _build_refusal_text(client, domain, reason)
            return AgentResponse(
                answer=answer,
                refused=True,
                refusal_reason=f"crisis: {crisis.category}",
                domain_label=domain,
                crisis_route_fired=True,
            )
        # Any other crisis action: fall through to the normal pipeline.

    # ── Step 2: Narrow regex refuse (6 categories) ────────────────────────
    # The refusal_router handles active_proceeding / immigration / criminal /
    # wills_estates. mental_health and family_violence are caught earlier by
    # crisis_detector. Everything else proceeds to retrieval + synthesis.
    from pipeline.refusal_router import should_refuse
    refusal = should_refuse(question)
    if refusal.refused:
        domain = refusal.category.lower()
        reason = _REFUSE_REASONS.get(domain, "this falls outside what this service can safely answer")
        log.info("[agent_router] Refuse (regex): %s", domain)
        answer = _build_refusal_text(client, domain, reason)
        return AgentResponse(
            answer=answer,
            refused=True,
            refusal_reason=domain,
            domain_label=domain,
            crisis_route_fired=crisis.triggered,
        )

    # ── Step 3: Tool routing (Sonnet) ─────────────────────────────────────
    tool_calls, routing_refused, routing_refusal_reason = _route(client, question)

    if routing_refused:
        log.info("[agent_router] Routing model refused: %s", routing_refusal_reason)
        answer = _build_refusal_text(
            client, "other",
            routing_refusal_reason or "this question is outside the scope of this service",
        )
        return AgentResponse(
            answer=answer,
            refused=True,
            refusal_reason=routing_refusal_reason,
            domain_label="other",
            crisis_route_fired=crisis.triggered,
        )

    log.info("[agent_router] Tools selected: %s", [tc["name"] for tc in tool_calls])

    # ── Step 4: Execute tools (parallel) ──────────────────────────────────
    tool_results = _execute_tools(tool_calls)

    for tr in tool_results:
        if tr and not tr.success:
            log.warning("[agent_router] Tool failed: %s — %s", tr.tool_name, tr.error)

    # ── Step 5: Infer domain_label from tools used (used for audit only) ──
    # Sprint 6 removes the intent classifier; domain_label is now inferred
    # from which tool(s) returned results.
    domain_label = "other"
    successful_domains = [tr.domain for tr in tool_results if tr and tr.success and tr.domain]
    if "employment" in successful_domains:
        domain_label = "employment"
    elif "tax" in successful_domains:
        domain_label = "tax"
    elif "labour_stats" in successful_domains:
        domain_label = "labour_stats"

    # ── Step 6+7: Synthesise + output guard ───────────────────────────────
    answer, sources, domains_used, chart, regen_count, banned_hits = _synthesise(
        client, question, tool_results, domain_label,
    )

    log.info(
        "[agent_router] Done. domains=%s sources=%d regen=%d banned_hits=%s",
        domains_used, len(sources), regen_count, banned_hits,
    )

    return AgentResponse(
        answer=answer,
        sources=sources,
        domains_used=domains_used,
        tool_calls=tool_calls,
        refused=False,
        chart=chart,
        domain_label=domain_label,
        crisis_route_fired=crisis.triggered,
        regeneration_count=regen_count,
        banned_phrase_hits=banned_hits,
    )
