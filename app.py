"""
app.py — NZ Employment Law Assistant
Streamlit Web UI

Usage:
    streamlit run app.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent
VS_DIR       = BASE_DIR / "vectorstore"
FEEDBACK_LOG = BASE_DIR / "data" / "feedback_log.jsonl"
sys.path.insert(0, str(BASE_DIR / "pipeline"))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NZ Employment Law Assistant",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Global ── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    font-family: 'Inter', 'Segoe UI', sans-serif !important;
    background: #F0F4FA !important;
}
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stMain"] .block-container {
    max-width: 780px;
    padding: 0 2rem 100px 2rem;
    margin: 0 auto;
}

/* ── Hide ALL hr / dividers / decorative separators ── */
hr,
[data-testid="stMain"] hr,
[data-testid="stSidebar"] hr,
[data-testid="stSidebar"] [data-testid="stDivider"],
[data-testid="stDivider"],
[data-testid="stMainBlockContainer"] > div > div > [data-testid="stVerticalBlock"] > div:empty,
/* Streamlit puts a decorative separator element between columns at top level */
[data-testid="stMain"] .block-container > div > [data-testid="stVerticalBlock"] > div[style*="height: 0"],
[data-testid="stMain"] .block-container > div > [data-testid="stVerticalBlock"] > div[style*="flex: 1"] > hr {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #0F2A5C !important;
    min-width: 260px !important;
    max-width: 260px !important;
    transform: none !important;
    visibility: visible !important;
}
/* Hide the collapse/expand toggle button */
[data-testid="collapsedControl"],
button[aria-expanded] { display: none !important; }
[data-testid="stSidebar"] > div { padding: 24px 20px !important; }

/* All sidebar text white */
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] li,
[data-testid="stSidebar"] div {
    color: rgba(255,255,255,0.82) !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.76rem !important;
    line-height: 1.7 !important;
}
[data-testid="stSidebar"] strong { color: #fff !important; font-weight: 600 !important; }
[data-testid="stSidebar"] em    { color: rgba(255,255,255,0.5) !important; }

/* Sidebar logo text */
[data-testid="stSidebar"] h3 {
    color: #fff !important;
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    margin-bottom: 12px !important;
}

/* Sidebar buttons */
[data-testid="stSidebar"] .stButton button {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.15) !important;
    color: rgba(255,255,255,0.85) !important;
    border-radius: 7px !important;
    font-size: 0.74rem !important;
    padding: 6px 10px !important;
    text-align: left !important;
    line-height: 1.4 !important;
    width: 100% !important;
    transition: background 0.15s !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(255,255,255,0.14) !important;
    border-color: rgba(255,255,255,0.3) !important;
}

/* ── Top bar ── */
.topbar {
    position: sticky;
    top: 0;
    background: #fff;
    border-bottom: 1px solid #E2E8F0;
    padding: 14px 0;
    margin-bottom: 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    z-index: 100;
}
.topbar-left { display: flex; align-items: center; gap: 10px; }
.topbar-left h1 {
    font-size: 1rem;
    font-weight: 700;
    color: #0F2A5C;
    margin: 0;
}
.topbar-badge {
    font-size: 0.65rem;
    font-weight: 600;
    background: #EEF4FF;
    color: #3B6FC7;
    border: 1px solid #C7D9F8;
    border-radius: 20px;
    padding: 2px 9px;
}

/* ── Sidebar buttons: Clear + Privacy/Disclaimer/Terms ── */
[data-testid="stSidebar"] .stButton button {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.15) !important;
    color: rgba(255,255,255,0.7) !important;
    border-radius: 7px !important;
    font-size: 0.74rem !important;
    padding: 6px 10px !important;
    width: 100% !important;
    font-family: 'Inter', sans-serif !important;
    min-height: unset !important;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(255,255,255,0.14) !important;
    color: #fff !important;
    border-color: rgba(255,255,255,0.3) !important;
}

/* ── Welcome state ── */
.welcome-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 48px 0 32px;
    gap: 20px;
    text-align: center;
}
.welcome-icon {
    width: 66px; height: 66px;
    background: linear-gradient(135deg, #0F2A5C 0%, #3B6FC7 100%);
    border-radius: 18px;
    display: flex; align-items: center; justify-content: center;
    font-size: 32px;
    box-shadow: 0 8px 24px rgba(15,42,92,0.18);
    margin-bottom: 4px;
}
.welcome-title {
    font-size: 1.35rem;
    font-weight: 700;
    color: #0F2A5C;
    margin: 0 0 6px 0;
}
.welcome-sub {
    font-size: 0.85rem;
    color: #64748B;
    max-width: 420px;
    line-height: 1.6;
    margin: 0;
}

/* ── Chips (welcome example cards) ── */
/* Now that topbar uses pure HTML (no st.columns), ALL stHorizontalBlock buttons are chips */
[data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button {
    background: rgba(255,255,255,0.5) !important;
    border: 1px solid #E8EDF5 !important;
    border-radius: 10px !important;
    color: #64748B !important;
    font-size: 0.78rem !important;
    padding: 11px 14px !important;
    text-align: left !important;
    line-height: 1.4 !important;
    height: auto !important;
    min-height: 52px !important;
    transition: all 0.15s !important;
    font-family: 'Inter', sans-serif !important;
    box-shadow: none !important;
    font-weight: 400 !important;
}
[data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button:hover {
    background: #fff !important;
    border-color: #3B6FC7 !important;
    color: #1A2233 !important;
    box-shadow: 0 2px 10px rgba(59,111,199,0.08) !important;
}

/* ── Chat messages ── */
[data-testid="stChatMessage"] {
    background: transparent !important;
    padding: 4px 0 !important;
}
[data-testid="stChatMessage"][data-testid*="user"] > div:last-child {
    background: #0F2A5C !important;
    color: #fff !important;
    border-radius: 12px 12px 3px 12px !important;
}
[data-testid="stChatMessage"][data-testid*="assistant"] > div:last-child {
    background: #fff !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 12px 12px 12px 3px !important;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05) !important;
}

/* ── Chat input ── */
[data-testid="stChatInput"] {
    background: #fff !important;
    border: 1.5px solid #E2E8F0 !important;
    border-radius: 12px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
}
[data-testid="stChatInput"]:focus-within {
    border-color: #3B6FC7 !important;
    box-shadow: 0 0 0 3px rgba(59,111,199,0.1) !important;
}
[data-testid="stChatInput"] button {
    background: #0F2A5C !important;
    border-radius: 8px !important;
    color: #fff !important;
}
[data-testid="stChatInput"] button:hover {
    background: #3B6FC7 !important;
}

/* ── Footer ── */
.footer {
    position: fixed;
    bottom: 0;
    left: 260px;
    right: 0;
    background: #fff;
    border-top: 1px solid #E2E8F0;
    padding: 9px 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0;
    z-index: 999;
}
.footer-info {
    font-size: 0.74rem;
    color: #94A3B8;
    margin-right: 16px;
    font-style: italic;
}
.footer a {
    color: #64748B;
    text-decoration: none;
    padding: 0 10px;
    border-left: 1px solid #CBD5E1;
    font-size: 0.76rem;
    font-weight: 500;
    cursor: pointer;
}
.footer a:first-of-type { border-left: none; }
.footer a:hover { color: #3B6FC7; text-decoration: underline; }
.footer-copy {
    margin-left: auto;
    font-size: 0.74rem;
    color: #94A3B8;
}


/* ── Scrollbar ── */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #D1D9E6; border-radius: 3px; }
</style>
""", unsafe_allow_html=True)


# ── Load RAG ─────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading knowledge base…")
def load_rag():
    from rag_query import RAGSystem
    return RAGSystem(VS_DIR)

try:
    rag = load_rag()
except Exception as e:
    st.error(f"❌ Failed to load RAG system: {e}")
    st.info("Make sure ANTHROPIC_API_KEY is set and the vector store exists at `data/vectorstore/`.")
    st.stop()


# ── Feedback logger ───────────────────────────────────────────────────────────
def log_feedback(question: str, rating: str):
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now().isoformat(), "question": question, "rating": rating}
    with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ── Session state ─────────────────────────────────────────────────────────────
for key, default in [
    ("messages",        []),
    ("confirm_clear",   False),
    ("show_privacy",    False),
    ("show_disclaimer", False),
    ("show_terms",      False),
]:
    if key not in st.session_state:
        st.session_state[key] = default


EXAMPLES = [
    "💼  What is the minimum wage in NZ?",
    "📋  How much notice does an employer need to give?",
    "🚪  What are my rights if I'm made redundant?",
    "🤒  How many sick days am I entitled to?",
    "⚠️  What counts as unjustified dismissal?",
    "🕐  Can my employer change my hours without consent?",
]


# ── Legal dialogs — must be defined BEFORE sidebar ────────────────────────────
@st.dialog("Privacy Policy")
def show_privacy():
    st.markdown("""
**This tool does not collect, store, or transmit any personal data.**

Conversations are temporary and exist only within your browser session — permanently deleted when you close or refresh the page. No account registration is required.

This app has opted out of Streamlit's usage telemetry. Snowflake's own infrastructure practices may still apply — see [Snowflake's Privacy Policy](https://www.snowflake.com/en/legal/privacy/privacy-policy/).

If you are an EU resident, note that Streamlit's infrastructure is US-based.
    """)

@st.dialog("Disclaimer")
def show_disclaimer():
    st.markdown("""
Information is sourced from official NZ government websites including Employment New Zealand and MBIE.

For **general informational purposes only** — not legal advice.

Laws change. Always verify with an official source or qualified employment lawyer. The creator accepts no liability for decisions made based on this tool.
    """)

@st.dialog("Terms of Use")
def show_terms():
    st.markdown("""
Provided **free of charge** for personal and educational use.

By using this tool you agree that:
- Responses are for general reference only, not a substitute for legal advice
- You will not use this tool for any unlawful purpose
- The creator may modify or discontinue the service at any time
    """)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚖️ NZ Employment Law Assistant")

    st.markdown(
        "A free AI-powered tool for understanding "
        "<strong>New Zealand employment law</strong> — whether you're an employee "
        "knowing your rights or an employer navigating your obligations."
        "<br><br>"
        "<strong>What it covers:</strong>"
        "<ul style='margin:6px 0 12px 16px; padding:0; line-height:1.9;'>"
        "<li>Employment agreements &amp; trial periods</li>"
        "<li>Leave entitlements (annual, sick, parental)</li>"
        "<li>Dismissal, redundancy &amp; personal grievances</li>"
        "<li>Wages, hours &amp; workplace rights</li>"
        "<li style='list-style:none; margin-left:-4px; color:rgba(255,255,255,0.4);'>…and more</li>"
        "</ul>"
        "<strong>Data sources:</strong><br>"
        "From official NZ government websites."
        "<br><br>"
        "<em>Not legal advice. For serious matters, consult a qualified employment lawyer.</em>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<hr style="border:none; border-top:1px solid rgba(255,255,255,0.1); '
        'margin:14px 0;" />',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<a href="https://www.linkedin.com/in/laurahfc/" target="_blank" '
        'style="display:flex; align-items:center; gap:8px; background:rgba(255,255,255,0.08); '
        'border:1px solid rgba(255,255,255,0.2); border-radius:7px; padding:9px 12px; '
        'text-decoration:none; color:#fff; font-size:0.78rem; font-weight:500; '
        'white-space:nowrap;">'
        '👤 Built by Laura Cai'
        '<span style="margin-left:auto; background:#0A66C2; color:#fff; font-size:0.68rem; '
        'font-weight:600; padding:2px 9px; border-radius:4px; flex-shrink:0;">LinkedIn</span>'
        '</a>',
        unsafe_allow_html=True,
    )

    # Clear conversation button in sidebar
    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
    if st.button("🗑 Clear conversation", key="sidebar_clear", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    # Privacy / Disclaimer / Terms in sidebar
    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    if st.button("Privacy", key="sb_privacy", use_container_width=True):
        show_privacy()
    if st.button("Disclaimer", key="sb_disclaimer", use_container_width=True):
        show_disclaimer()
    if st.button("Terms of Use", key="sb_terms", use_container_width=True):
        show_terms()


# ── Top bar ───────────────────────────────────────────────────────────────────
st.markdown("""
<div style="display:flex; align-items:center; gap:10px;
            background:#fff; border-bottom:1px solid #E2E8F0;
            padding:14px 0 12px; margin-bottom:24px;">
  <span style="font-size:1rem; font-weight:700; color:#0F2A5C; font-family:Inter,sans-serif;">
    ⚖️ NZ Employment Law Assistant</span>
  <span style="font-size:0.65rem; font-weight:600; background:#EEF4FF; color:#3B6FC7;
               border:1px solid #C7D9F8; border-radius:20px; padding:2px 9px;
               letter-spacing:0.03em;">Free · Beta</span>
</div>
""", unsafe_allow_html=True)

# ── Welcome state ─────────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
<div class="welcome-wrap">
  <div class="welcome-icon">⚖️</div>
  <div>
    <p class="welcome-title">Ask anything about NZ employment law</p>
    <p class="welcome-sub">Get clear, cited answers based on official New Zealand government sources — for employees and employers.</p>
  </div>
</div>
""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        with (col1 if i % 2 == 0 else col2):
            if st.button(ex, key=f"chip_{i}", use_container_width=True):
                # strip emoji prefix for the actual question
                clean = ex.split("  ", 1)[-1] if "  " in ex else ex
                st.session_state["_inject"] = clean
                st.rerun()
    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)


# ── Chat messages ─────────────────────────────────────────────────────────────
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        if msg["role"] == "assistant":
            if msg.get("sources"):
                with st.expander("📚 Sources", expanded=False):
                    seen = set()
                    for i, src in enumerate(msg["sources"], 1):
                        url = src.get("url", "")
                        if url in seen:
                            continue
                        seen.add(url)
                        st.markdown(
                            f"**{i}. {src.get('title','Unknown')}**  \n"
                            f"[{url}]({url})  \n"
                            f"*{src.get('content_type','')}*"
                        )
            if not msg.get("rated"):
                st.markdown('<div style="display:flex; gap:6px; margin-top:4px;">', unsafe_allow_html=True)
                if st.button("👍", key=f"up_{idx}", help="Helpful"):
                    st.session_state.messages[idx]["rated"] = "up"
                    log_feedback(
                        st.session_state.messages[idx-1]["content"] if idx > 0 else "", "up"
                    )
                    st.rerun()
                if st.button("👎", key=f"dn_{idx}", help="Not helpful"):
                    st.session_state.messages[idx]["rated"] = "down"
                    log_feedback(
                        st.session_state.messages[idx-1]["content"] if idx > 0 else "", "down"
                    )
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.caption("👍 Thanks!" if msg["rated"] == "up" else "👎 Thanks — noted.")


# ── Handle question ───────────────────────────────────────────────────────────
def handle_question(question: str):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Searching knowledge base…"):
            try:
                result  = rag.query(question)
                answer  = result["answer"]
                sources = result["sources"]
            except Exception as e:
                answer  = f"❌ Error: {e}"
                sources = []
        st.markdown(answer)
        if sources:
            with st.expander("📚 Sources", expanded=False):
                seen = set()
                for i, src in enumerate(sources, 1):
                    url = src.get("url", "")
                    if url in seen:
                        continue
                    seen.add(url)
                    st.markdown(
                        f"**{i}. {src.get('title','Unknown')}**  \n"
                        f"[{url}]({url})  \n"
                        f"*{src.get('content_type','')}*"
                    )
    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})


question = st.chat_input("Ask a question about NZ employment law…")
if question:
    handle_question(question)

if "_inject" in st.session_state:
    q = st.session_state.pop("_inject")
    handle_question(q)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="footer">
  <span class="footer-info">⚖️ For informational purposes only — not legal advice</span>
  <span class="footer-copy">© 2026 Laura Cai</span>
</div>
""", unsafe_allow_html=True)
