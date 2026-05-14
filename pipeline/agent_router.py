"""
pipeline/agent_router.py
========================
Top-level Claude tool-use agent router for the NZ Employment & Tax Law service.
v2 — full risk-control pipeline wired in.

Execution order:
    1. crisis_detector.detect()         — pre-classifier crisis layer
       ├─ OVERRIDE_RESPONSE → return crisis card immediately
       ├─ FORCE_REFUSE      → jump to REFUSE_WITH_REFERRAL
       └─ FORCE_HIGH_STAKES → pin intent before classifier
    2. intent_classifier.classify()     — LOOKUP / ADVICE / HIGH_STAKES × tier
    3. ROUTING_MATRIX lookup            → routing_outcome (4 templates)
    4. If REFUSE_WITH_REFERRAL          → build_refusal(); skip retrieval
    5. _route()                         → Sonnet picks which tool(s) to call
    6. _execute_tools()                 → parallel retrieval
    7. _synthesise()                    → Haiku generates answer (template-aware)
    8. output_guard.output_guard()      → banned-phrase / structure / footer checks

Public API:
    run(question: str, api_key: str | None = None) -> AgentResponse

    AgentResponse fields:
        answer              str
        sources             list[dict]
        domains_used        list[str]
        tool_calls          list[dict]
        refused             bool
        refusal_reason      str | None
        chart               dict | None
        intent_class        str           "LOOKUP" | "ADVICE" | "HIGH_STAKES"
        domain_tier         str           "H1" | "H2" | "H3" | "M" | "L"
        domain_label        str           "employment" | "tax" | ...
        routing_outcome     str           "DIRECT_ANSWER" | "STRUCTURED_INFORMATIONAL" |
                                          "STRUCTURED_ADVICE_SKELETON" | "REFUSE_WITH_REFERRAL"
        crisis_route_fired  bool
        regeneration_count  int
        banned_phrase_hits  list[str]
        classifier_confidence float
        risk_badge          str           "general_info" | "high_care" |
                                          "please_get_advice" | "refused"
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import anthropic

from api.observability import observe

from pipeline.crisis_detector import detect as crisis_detect, CrisisResult
from pipeline.intent_classifier import classify as intent_classify, ClassifierResult
from pipeline.output_guard import (
    output_guard,
    build_footer,
    RISK_FOOTER_VERBATIM,
    TEMPLATE_HEADINGS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

ROUTING_MODEL   = "claude-sonnet-4-6"
SYNTHESIS_MODEL = "claude-haiku-4-5-20251001"

MAX_TOKENS_ROUTING   = 1024
MAX_TOKENS_SYNTHESIS = 2000   # increased to accommodate structured skeletons

# ---------------------------------------------------------------------------
# Routing matrix  (domain_tier × intent_class → routing_outcome)
# ---------------------------------------------------------------------------

ROUTING_MATRIX: dict[tuple[str, str], str] = {
    ("H1", "LOOKUP"):       "REFUSE_WITH_REFERRAL",
    ("H1", "ADVICE"):       "REFUSE_WITH_REFERRAL",
    ("H1", "HIGH_STAKES"):  "REFUSE_WITH_REFERRAL",
    ("H2", "LOOKUP"):       "STRUCTURED_INFORMATIONAL",
    ("H2", "ADVICE"):       "REFUSE_WITH_REFERRAL",
    ("H2", "HIGH_STAKES"):  "REFUSE_WITH_REFERRAL",
    ("H3", "LOOKUP"):       "STRUCTURED_INFORMATIONAL",
    ("H3", "ADVICE"):       "REFUSE_WITH_REFERRAL",
    ("H3", "HIGH_STAKES"):  "REFUSE_WITH_REFERRAL",
    ("M",  "LOOKUP"):       "DIRECT_ANSWER",
    ("M",  "ADVICE"):       "STRUCTURED_ADVICE_SKELETON",
    ("M",  "HIGH_STAKES"):  "STRUCTURED_ADVICE_SKELETON",
    ("L",  "LOOKUP"):       "DIRECT_ANSWER",
    ("L",  "ADVICE"):       "STRUCTURED_ADVICE_SKELETON",
    ("L",  "HIGH_STAKES"):  "STRUCTURED_ADVICE_SKELETON",
}

RISK_BADGE_MAP: dict[str, str] = {
    "DIRECT_ANSWER":              "general_info",
    "STRUCTURED_INFORMATIONAL":   "high_care",
    "STRUCTURED_ADVICE_SKELETON": "please_get_advice",
    "REFUSE_WITH_REFERRAL":       "refused",
}

# ---------------------------------------------------------------------------
# Domain referral bank (all 16+ domains from framework Appendix)
# ---------------------------------------------------------------------------

REFERRAL_BANK: dict[str, dict[str, list[str]]] = {
    "employment": {
        "free": ["MBIE Employment Service 0800 20 90 20", "Community Law (communitylaw.org.nz)"],
        "paid": ["Employment lawyer", "Union representative"],
    },
    "tenancy": {
        "free": ["Tenancy Services 0800 836 262", "Tenancy Tribunal (tenancy.govt.nz)"],
        "paid": ["Tenancy advocate", "Lawyer"],
    },
    "family": {
        "free": ["Family Legal Aid", "Community Law (communitylaw.org.nz)", "Family Court (justice.govt.nz/family)"],
        "paid": ["Family lawyer"],
    },
    "criminal": {
        "free": ["Police Detention Legal Assistance scheme", "Legal Aid (legalaid.govt.nz)", "Community Law"],
        "paid": ["Criminal lawyer", "Public Defence Service"],
    },
    "immigration": {
        "free": ["Immigration NZ (immigration.govt.nz)", "IAA register of licensed advisers (iaa.govt.nz)"],
        "paid": ["Licensed immigration adviser", "Immigration lawyer"],
    },
    "financial": {
        "free": ["Sorted (sorted.org.nz)", "FMA (fma.govt.nz)", "MoneyTalks 0800 345 123"],
        "paid": ["FMA-licensed financial adviser"],
    },
    "tax": {
        "free": ["IRD (ird.govt.nz) — free helpline 0800 775 247"],
        "paid": ["Chartered accountant", "Tax lawyer"],
    },
    "acc": {
        "free": ["ACC (acc.co.nz)", "ACC Advocacy Trust"],
        "paid": ["ACC review advocate", "Lawyer"],
    },
    "consumer": {
        "free": ["Consumer Protection (consumerprotection.govt.nz)", "Disputes Tribunal (disputestribunal.govt.nz)"],
        "paid": ["Lawyer (if matter exceeds Tribunal limit)"],
    },
    "privacy": {
        "free": ["Office of the Privacy Commissioner — 0800 803 909 (privacy.org.nz)"],
        "paid": ["Privacy lawyer"],
    },
    "mental_health": {
        "crisis": ["1737 — free call or text, any time", "Lifeline Aotearoa 0800 543 354", "Suicide Crisis Helpline 0508 828 865", "111 in immediate danger"],
        "paid": ["GP", "Psychologist", "Psychiatrist"],
    },
    "family_violence": {
        "crisis": ["111 in immediate danger", "Women's Refuge 0800 REFUGE (0800 733 843)", "Shine 0508 744 633", "Are You OK 0800 456 450"],
        "paid": ["Family violence lawyer", "Community Law"],
    },
    "te_tiriti": {
        "free": ["Te Puni Kōkiri (tpk.govt.nz)", "Māori Land Court (maorilandcourt.govt.nz)"],
        "paid": ["Kaupapa Māori legal service"],
    },
    "wills_estates": {
        "free": ["Community Law (communitylaw.org.nz)", "Citizens Advice Bureau 0800 367 222"],
        "paid": ["Lawyer", "Public Trust (publictrust.co.nz)"],
    },
    "traffic": {
        "free": ["Community Law (communitylaw.org.nz)", "Citizens Advice Bureau 0800 367 222"],
        "paid": ["Lawyer"],
    },
    "neighbour": {
        "free": ["Community Law (communitylaw.org.nz)", "Disputes Tribunal"],
        "paid": ["Lawyer"],
    },
    "other": {
        "free": ["Community Law (communitylaw.org.nz)", "Citizens Advice Bureau 0800 367 222"],
        "paid": ["NZ Law Society 'Find a Lawyer' (lawsociety.org.nz)"],
    },
}

_REFUSE_REASONS: dict[str, str] = {
    "immigration":     "immigration advice is a regulated activity in New Zealand — only licensed immigration advisers can provide it",
    "criminal":        "advice on criminal-defence strategy requires a qualified lawyer",
    "mental_health":   "this service is not equipped to help with mental-health crises",
    "family_violence": "your safety is what matters most right now",
    "financial":       "financial product advice is a regulated activity in New Zealand",
    "family":          "this topic requires tailored professional advice given the stakes involved",
    "wills_estates":   "will drafting and estate planning are reserved areas that require a qualified lawyer",
    "H1":              "this falls into a category where this service cannot help safely",
    "H2":              "this topic requires tailored advice from a qualified professional",
    "H3":              "tax positions on specific facts require a qualified tax adviser or IRD",
}

# ---------------------------------------------------------------------------
# Tool schemas (unchanged from v1)
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
# Routing system prompt (unchanged)
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
   statistics, do NOT call any tool. Instead, output exactly:
     {"refused": true, "reason": "out of scope"}
5. If the user describes an active IRD audit, active personal grievance already filed,
   mediation in progress, ERA or Employment Court proceedings underway, or an active tax
   dispute (NOPA/NORP issued), do NOT call any tool. Instead output exactly:
     {"refused": true, "reason": "active proceeding — refer to professional"}
6. Never use the words "legal advice", "tax advice", "your case", or predict outcomes.
7. Do not ask the user for clarification — make the best routing decision you can.
"""

# ---------------------------------------------------------------------------
# Template-aware synthesis prompts
# ---------------------------------------------------------------------------

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

_RISK_FOOTER_INSTRUCTION = (
    "End your answer with EXACTLY this text (verbatim, do not paraphrase):\n\n"
    "---\n"
    "General information only — not legal advice.\n"
    "This response was generated by an AI assistant from public sources and may\n"
    "contain errors or be out of date. The law and individual circumstances vary;\n"
    "nothing here is a substitute for advice from a lawyer or the relevant\n"
    "statutory body. For urgent or high-stakes matters, please contact a\n"
    "professional.\n\n"
    "Free help: Community Law (https://communitylaw.org.nz) · Citizens Advice\n"
    "Bureau 0800 367 222"
)

SYNTHESIS_PROMPTS: dict[str, str] = {

    "DIRECT_ANSWER": f"""\
You are a writing assistant for a New Zealand legal-information service.
You are NOT a lawyer. You provide general information only.

Task: Write a concise, factual answer to the question using ONLY the retrieved sources.

Rules:
1. Use ONLY the retrieved context. If it does not support the answer, say so.
2. Cite the source document or section for every factual claim.
3. Do NOT use banned phrases: {_BANNED_LIST_STR}
4. Use preferred phrases: {_PREFERRED_LIST_STR}
5. Keep answer to 2–3 paragraphs maximum.
6. {_RISK_FOOTER_INSTRUCTION}
""",

    "STRUCTURED_INFORMATIONAL": f"""\
You are a writing assistant for a New Zealand legal-information service.
You are NOT a lawyer. This topic requires extra care — use the structured format below.

Task: Write a structured informational response using ONLY the retrieved sources.

REQUIRED STRUCTURE (use these exact headings):

## General information about <topic>

<Plain-English summary of what the law generally says, with source citations.
Use "the law provides that..." NOT "you should...">

### Where to get tailored help

<Free service + paid professional relevant to this domain>

Rules:
1. Use ONLY the retrieved context.
2. NEVER use second-person framing ("you should", "your situation", "in your case").
   Use third-person: "a person in this position", "the relevant Act provides".
3. Do NOT characterise the user's specific facts.
4. Do NOT use banned phrases: {_BANNED_LIST_STR}
5. {_RISK_FOOTER_INSTRUCTION}
""",

    "STRUCTURED_ADVICE_SKELETON": f"""\
You are a writing assistant for a New Zealand legal-information service.
You are NOT a lawyer. You do not give advice. You write structured general information.

Task: Write a structured response using ONLY the retrieved sources.

REQUIRED STRUCTURE (use these EXACT headings — all seven are mandatory):

## General information about <topic>

### Here is what the law says
<Plain-English summary of the statutory framework. Cite source + section for every claim.>

### Factors that typically matter
<Bullet list of factors a court/tribunal would consider. State each factor and
what tilts it one way or another IN GENERAL — never apply to the user's specific facts.>

### Options people in this situation generally consider
<Enumerate the procedural options that exist. For each: what it involves + where to find
authoritative guidance. Never rank them or recommend one.>

### Time limits and deadlines that may apply
<Surface the most punitive deadline FIRST. If no statutory deadline applies, say so.
For employment matters: always mention the 90-day personal grievance window.>

### Why this is general information, not advice
<Two sentences. Plain language.>

### Where to get advice on your specific situation
<Free option · Paid professional · Crisis option if relevant.>

---
<RISK FOOTER verbatim — see instruction below>

STRICT RULES:
1. Use ONLY the retrieved context. State clearly if sources do not cover the question.
2. NEVER use second-person framing. Use third-person throughout:
   "a tenant in this position", "an employee in this scenario", "the relevant Act provides".
3. NEVER characterise the user's facts ("this is a personal grievance", "this is unfair dismissal").
   Describe what categories EXIST and the factors that determine which applies.
4. NEVER use banned phrases: {_BANNED_LIST_STR}
5. USE preferred phrases: {_PREFERRED_LIST_STR}
6. {_RISK_FOOTER_INSTRUCTION}
""",

    "REFUSE_WITH_REFERRAL": f"""\
You are a writing assistant for a New Zealand legal-information service.
You cannot help with this question — it falls outside what this service can safely answer.

Task: Write a warm, brief refusal with referrals, using this structure:

[1] Acknowledge the question briefly and warmly (one sentence).
[2] Explain in one sentence WHY this service cannot answer it.
[3] Provide the referrals supplied below.
[4] Offer one factual, non-advisory thing the service CAN do
    (e.g. "I can explain what the relevant Act covers in general terms — would that help?").

RULES:
1. Be warm, not cold. The user may be in distress.
2. Do NOT attempt to answer the underlying question.
3. Do NOT use banned phrases: {_BANNED_LIST_STR}
4. {_RISK_FOOTER_INSTRUCTION}
""",
}

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AgentResponse:
    """Structured response from the agent router (v2)."""
    # Core answer
    answer: str
    sources: list[dict] = field(default_factory=list)
    domains_used: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    chart: dict | None = None
    # Risk control metadata
    intent_class: str = "LOOKUP"
    domain_tier: str = "M"
    domain_label: str = "other"
    routing_outcome: str = "DIRECT_ANSWER"
    crisis_route_fired: bool = False
    regeneration_count: int = 0
    banned_phrase_hits: list[str] = field(default_factory=list)
    classifier_confidence: float = 1.0
    risk_badge: str = "general_info"


# ---------------------------------------------------------------------------
# Refusal builder
# ---------------------------------------------------------------------------

def _build_refusal_text(
    client: anthropic.Anthropic,
    domain_label: str,
    domain_tier: str,
    reason: str,
) -> str:
    """Generate a REFUSE_WITH_REFERRAL response via Haiku with domain referrals injected."""
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
            system=SYNTHESIS_PROMPTS["REFUSE_WITH_REFERRAL"],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text if resp.content else ""
    except Exception as exc:
        log.exception("[agent_router] Refusal generation failed: %s", exc)
        text = (
            f"I'm not able to help with that question — {reason}.\n\n"
            f"{referral_text}\n\n"
        )

    footer = build_footer(domain_label, domain_tier)
    if "General information only" not in text:
        text = text.rstrip() + "\n\n---\n" + footer
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
            try:
                parsed = json.loads(text)
                if parsed.get("refused"):
                    refused = True
                    refusal_reason = parsed.get("reason", "out of scope")
            except (json.JSONDecodeError, AttributeError):
                pass

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
    routing_outcome: str,
    domain_label: str,
    domain_tier: str,
) -> tuple[str, list[dict], list[str], dict | None, int, list[str]]:
    """
    Synthesise a cited answer from tool results using the appropriate template.

    Returns: (answer_text, sources, domains_used, chart, regen_count, banned_hits)
    """
    all_chunks: list[str] = []
    all_sources: list[dict] = []
    domains_used: list[str] = []
    chart = None

    for tr in tool_results:
        if not tr or not tr.success:
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
        no_info = (
            "The knowledge base does not contain enough information to answer this question.\n\n"
            "For accurate information please contact:\n"
            "  • Employment NZ 0800 20 90 20\n"
            "  • IRD 0800 775 247\n"
            "  • Community Law (communitylaw.org.nz)\n\n"
            "---\n" + build_footer(domain_label, domain_tier)
        )
        return no_info, deduped_sources, domains_used, chart, 0, []

    # Format context
    context_parts = [f"[Source {i}]\n{chunk}" for i, chunk in enumerate(all_chunks, 1)]
    context_text = "\n\n".join(context_parts)

    user_message = (
        f"Question: {question}\n\n"
        f"Retrieved context:\n{context_text}\n\n"
        "Write your structured answer now."
    )

    system_prompt = SYNTHESIS_PROMPTS[routing_outcome]

    def _call_model(extra_instruction: str = "") -> str:
        msg = user_message
        if extra_instruction:
            msg = extra_instruction + "\n\n" + msg
        resp = client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=MAX_TOKENS_SYNTHESIS,
            system=system_prompt,
            messages=[{"role": "user", "content": msg}],
        )
        return resp.content[0].text if resp.content else ""

    initial_answer = _call_model()

    # Run output guard
    guard_result = output_guard(
        answer_text=initial_answer,
        routing_outcome=routing_outcome,
        domain_label=domain_label,
        domain_tier=domain_tier,
        generate_fn=_call_model,
        max_regenerations=2,
    )

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
    Run the full v2 agent pipeline for a user question.

    Args:
        question: The user's question.
        api_key:  Anthropic API key (falls back to ANTHROPIC_API_KEY env var).

    Returns:
        AgentResponse with full risk-control metadata.
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
                intent_class="HIGH_STAKES",
                domain_tier="H1",
                domain_label=crisis.domain_label or "mental_health",
                routing_outcome="REFUSE_WITH_REFERRAL",
                crisis_route_fired=True,
                risk_badge="refused",
            )

        if crisis.action == "FORCE_REFUSE":
            log.info("[agent_router] Crisis force-refuse: %s", crisis.category)
            domain = crisis.domain_label or "other"
            reason = _REFUSE_REASONS.get(domain, _REFUSE_REASONS["H1"])
            answer = _build_refusal_text(client, domain, "H1", reason)
            return AgentResponse(
                answer=answer,
                refused=True,
                refusal_reason=f"crisis: {crisis.category}",
                intent_class="HIGH_STAKES",
                domain_tier="H1",
                domain_label=domain,
                routing_outcome="REFUSE_WITH_REFERRAL",
                crisis_route_fired=True,
                risk_badge="refused",
            )
        # FORCE_HIGH_STAKES — fall through to classifier with intent pinned

    # ── Step 2: Intent + domain classification ────────────────────────────
    clf: ClassifierResult = intent_classify(question, api_key=key)

    # Apply FORCE_HIGH_STAKES from crisis detector
    intent_class = clf.intent_class
    if crisis.triggered and crisis.action == "FORCE_HIGH_STAKES":
        log.info("[agent_router] Crisis: FORCE_HIGH_STAKES applied.")
        intent_class = "HIGH_STAKES"

    domain_tier  = clf.domain_tier
    domain_label = clf.domain_label

    # ── Step 3: Routing matrix ────────────────────────────────────────────
    routing_outcome = ROUTING_MATRIX.get((domain_tier, intent_class), "STRUCTURED_ADVICE_SKELETON")
    risk_badge = RISK_BADGE_MAP[routing_outcome]
    log.info(
        "[agent_router] intent=%s tier=%s label=%s → outcome=%s",
        intent_class, domain_tier, domain_label, routing_outcome,
    )

    # ── Step 4: Hard refusal (no retrieval needed) ────────────────────────
    if routing_outcome == "REFUSE_WITH_REFERRAL":
        reason = _REFUSE_REASONS.get(domain_label) or _REFUSE_REASONS.get(domain_tier, "this falls outside what this service can safely answer")
        answer = _build_refusal_text(client, domain_label, domain_tier, reason)
        return AgentResponse(
            answer=answer,
            refused=True,
            refusal_reason=f"{domain_tier}×{intent_class} → REFUSE_WITH_REFERRAL",
            intent_class=intent_class,
            domain_tier=domain_tier,
            domain_label=domain_label,
            routing_outcome=routing_outcome,
            crisis_route_fired=crisis.triggered,
            classifier_confidence=clf.confidence,
            risk_badge=risk_badge,
        )

    # ── Step 5: Tool routing (Sonnet) ─────────────────────────────────────
    tool_calls, routing_refused, routing_refusal_reason = _route(client, question)

    if routing_refused:
        log.info("[agent_router] Routing model refused: %s", routing_refusal_reason)
        answer = _build_refusal_text(
            client, domain_label, domain_tier,
            routing_refusal_reason or "this question is outside the scope of this service",
        )
        return AgentResponse(
            answer=answer,
            refused=True,
            refusal_reason=routing_refusal_reason,
            intent_class=intent_class,
            domain_tier=domain_tier,
            domain_label=domain_label,
            routing_outcome="REFUSE_WITH_REFERRAL",
            crisis_route_fired=crisis.triggered,
            classifier_confidence=clf.confidence,
            risk_badge="refused",
        )

    log.info("[agent_router] Tools selected: %s", [tc["name"] for tc in tool_calls])

    # ── Step 6: Execute tools (parallel) ──────────────────────────────────
    tool_results = _execute_tools(tool_calls)

    for tr in tool_results:
        if tr and not tr.success:
            log.warning("[agent_router] Tool failed: %s — %s", tr.tool_name, tr.error)

    # ── Step 7+8: Synthesise + output guard ───────────────────────────────
    answer, sources, domains_used, chart, regen_count, banned_hits = _synthesise(
        client, question, tool_results, routing_outcome, domain_label, domain_tier,
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
        intent_class=intent_class,
        domain_tier=domain_tier,
        domain_label=domain_label,
        routing_outcome=routing_outcome,
        crisis_route_fired=crisis.triggered,
        regeneration_count=regen_count,
        banned_phrase_hits=banned_hits,
        classifier_confidence=clf.confidence,
        risk_badge=risk_badge,
    )
