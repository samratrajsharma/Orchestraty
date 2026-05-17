"""
Orchestraty — Data Intelligence Dashboard
Unified pipeline view: Create → Upload → Structure → Analyze → Label

Run:  streamlit run frontend/app.py
"""

import streamlit as st
import requests
import json
import pandas as pd
import time
import os
from datetime import datetime
from pathlib import Path

# ── Config ──
API_BASE = "http://localhost:8000"
_TOKEN_FILE = Path.home() / ".orchestraty_session"


# ── Token persistence (survives browser refresh) ──
def _save_token(token: str):
    """Persist JWT to a local file so login survives page refresh."""
    try:
        _TOKEN_FILE.write_text(token, encoding="utf-8")
    except Exception:
        pass


def _load_token() -> str | None:
    """Load persisted JWT. Returns None if missing or unreadable."""
    try:
        if _TOKEN_FILE.exists():
            token = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            return token if token else None
    except Exception:
        pass
    return None


def _clear_token():
    """Remove the persisted token file on logout."""
    try:
        if _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()
    except Exception:
        pass

st.set_page_config(
    page_title="Orchestraty",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Modern CSS ──
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    .stApp {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Sidebar styling */
    div[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    }
    div[data-testid="stSidebar"] * {
        color: #e2e8f0 !important;
    }
    div[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.1) !important;
    }
    div[data-testid="stSidebar"] .stSelectbox label,
    div[data-testid="stSidebar"] .stTextInput label {
        color: rgba(255,255,255,0.7) !important;
    }

    /* Hero header */
    .hero-banner {
        background: linear-gradient(135deg, #0f172a 0%, #1e40af 50%, #7c3aed 100%);
        color: white;
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 1.5rem;
        position: relative;
        overflow: hidden;
    }
    .hero-banner::after {
        content: '';
        position: absolute;
        top: -50%;
        right: -20%;
        width: 300px;
        height: 300px;
        background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 70%);
        border-radius: 50%;
    }
    .hero-banner h1 {
        margin: 0;
        font-size: 1.75rem;
        font-weight: 800;
        letter-spacing: -0.03em;
    }
    .hero-banner p {
        margin: 0.4rem 0 0 0;
        opacity: 0.85;
        font-size: 0.95rem;
        font-weight: 400;
    }

    /* Pipeline step cards */
    .pipeline-step {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.8rem;
        transition: all 0.2s ease;
        background: white;
    }
    .pipeline-step:hover {
        border-color: #7c3aed;
        box-shadow: 0 4px 16px rgba(124, 58, 237, 0.12);
    }
    .pipeline-step.active {
        border-color: #7c3aed;
        border-width: 2px;
        background: linear-gradient(135deg, #faf5ff 0%, #f5f3ff 100%);
    }
    .pipeline-step.completed {
        border-color: #10b981;
        background: linear-gradient(135deg, #ecfdf5 0%, #f0fdf4 100%);
    }

    /* Step number badge */
    .step-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        border-radius: 50%;
        font-size: 0.8rem;
        font-weight: 700;
        margin-right: 8px;
    }
    .step-badge-pending { background: #f1f5f9; color: #64748b; }
    .step-badge-active { background: #7c3aed; color: white; }
    .step-badge-done { background: #10b981; color: white; }

    /* Status chips */
    .status-chip {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .chip-raw { background: #fef2f2; color: #dc2626; }
    .chip-processed { background: #fef3c7; color: #d97706; }
    .chip-labeled { background: #dbeafe; color: #2563eb; }
    .chip-reviewed { background: #e0e7ff; color: #4f46e5; }
    .chip-ready { background: #d1fae5; color: #059669; }

    /* Metric cards */
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
        margin: 1rem 0;
    }
    .metric-box {
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
    }
    .metric-box .value {
        font-size: 1.5rem;
        font-weight: 700;
        color: #0f172a;
        line-height: 1.2;
    }
    .metric-box .label {
        font-size: 0.75rem;
        color: #64748b;
        margin-top: 4px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Grade badge */
    .grade-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 48px;
        height: 48px;
        border-radius: 12px;
        font-size: 1.5rem;
        font-weight: 800;
    }
    .grade-A { background: #d1fae5; color: #059669; }
    .grade-B { background: #dbeafe; color: #2563eb; }
    .grade-C { background: #fef3c7; color: #d97706; }
    .grade-D { background: #fed7aa; color: #ea580c; }
    .grade-F { background: #fecaca; color: #dc2626; }

    /* Section dividers */
    .section-title {
        font-size: 1.1rem;
        font-weight: 700;
        color: #0f172a;
        border-left: 4px solid #7c3aed;
        padding-left: 12px;
        margin: 1.5rem 0 1rem 0;
    }

    /* Dataset card in sidebar */
    .ds-card {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 10px;
        padding: 0.75rem 1rem;
        margin-bottom: 0.5rem;
        cursor: pointer;
        transition: all 0.15s ease;
    }
    .ds-card:hover {
        background: rgba(255,255,255,0.14);
        border-color: rgba(124, 58, 237, 0.5);
    }
    .ds-card.selected {
        background: rgba(124, 58, 237, 0.2);
        border-color: #7c3aed;
    }
    .ds-card .ds-name {
        font-weight: 600;
        font-size: 0.9rem;
    }
    .ds-card .ds-meta {
        font-size: 0.75rem;
        opacity: 0.6;
        margin-top: 2px;
    }

    /* Clean form styling */
    .stForm {
        border: 1px solid #e2e8f0 !important;
        border-radius: 12px !important;
        padding: 1.5rem !important;
    }

    /* Tabs override for cleaner look */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0px;
        background: #f8fafc;
        border-radius: 10px;
        padding: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 20px;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        background: white;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }

    /* Rule list items */
    .rule-item {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 8px 12px;
        margin-bottom: 6px;
        font-size: 0.88rem;
    }
    .rule-item code {
        background: #e0e7ff;
        color: #4f46e5;
        padding: 1px 6px;
        border-radius: 4px;
        font-size: 0.82rem;
    }

    /* Welcome screen */
    .welcome-card {
        text-align: center;
        padding: 4rem 2rem;
        background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
        border-radius: 16px;
        border: 1px solid #e2e8f0;
    }
    .welcome-card h2 {
        font-weight: 800;
        color: #0f172a;
        font-size: 2rem;
        letter-spacing: -0.03em;
    }
    .welcome-card p {
        color: #64748b;
        font-size: 1.05rem;
        max-width: 500px;
        margin: 0.5rem auto 0 auto;
    }

    /* Admin role pills */
    .role-pill {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .pill-admin { background: #fef2f2; color: #dc2626; }
    .pill-reviewer { background: #dbeafe; color: #2563eb; }
    .pill-annotator { background: #fef3c7; color: #d97706; }
    .pill-viewer { background: #d1fae5; color: #059669; }

    /* Admin stat cards */
    .stat-row { display: flex; gap: 12px; margin: 1rem 0; flex-wrap: wrap; }
    .stat-card {
        flex: 1;
        min-width: 130px;
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
    }
    .stat-card .num { font-size: 1.8rem; font-weight: 800; color: #0f172a; }
    .stat-card .lbl { font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }

    /* Admin section titles */
    .sec-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #0f172a;
        border-left: 4px solid #dc2626;
        padding-left: 12px;
        margin: 1.5rem 0 1rem 0;
    }

    /* Hide default streamlit footer */
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── Session State ──
for key, default in [
    ("token", None), ("user", None),
    ("selected_dataset", None), ("labeling_rules", []),
    ("_session_restored", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# Restore session from persisted token on first load / after browser refresh
if not st.session_state._session_restored:
    st.session_state._session_restored = True
    saved_token = _load_token()
    if saved_token and not st.session_state.token:
        # Validate the saved token is still good
        try:
            resp = requests.get(
                f"{API_BASE}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {saved_token}"},
                timeout=5,
            )
            if resp.status_code == 200:
                st.session_state.token = saved_token
                st.session_state.user = resp.json()
            else:
                _clear_token()
        except Exception:
            _clear_token()


def auth_headers():
    if st.session_state.token:
        return {"Authorization": f"Bearer {st.session_state.token}"}
    return {}


def api_call(method, endpoint, **kwargs):
    url = f"{API_BASE}{endpoint}"
    headers = kwargs.pop("headers", {})
    headers.update(auth_headers())
    try:
        resp = getattr(requests, method)(url, headers=headers, timeout=15, **kwargs)
        if resp.status_code < 300:
            return True, resp.json() if resp.status_code != 204 else None
        try:
            return False, resp.json()
        except Exception:
            return False, {"detail": resp.text}
    except requests.ConnectionError:
        return False, {"detail": "Cannot connect to API at localhost:8000"}
    except Exception as e:
        return False, {"detail": str(e)}


def show_error(data):
    if isinstance(data, dict):
        detail = data.get("detail", str(data))
        if isinstance(detail, list):
            for d in detail:
                st.error(f"**{d.get('loc', [''])}**: {d.get('msg', '')}")
        else:
            st.error(f"{detail}")
    else:
        st.error(str(data))


def poll_task_until_done(task_id: str, placeholder, timeout: int = 120, interval: int = 3):
    """Poll a background task until it completes or fails. Shows live progress in the placeholder."""
    start = time.time()
    while time.time() - start < timeout:
        ok, tdata = api_call("get", f"/api/v1/tasks/{task_id}")
        if not ok:
            time.sleep(interval)
            continue

        status = tdata.get("status", "pending")
        progress = tdata.get("progress", 0) or 0
        msg = tdata.get("progress_message", "Processing...")

        if status == "completed":
            placeholder.success(f"Pipeline completed! {msg}")
            return True
        elif status == "failed":
            placeholder.error(f"Pipeline failed: {tdata.get('error_message', msg)}")
            return False

        pct = int(progress * 100)
        placeholder.info(f"Running... {pct}% — {msg}")
        time.sleep(interval)

    placeholder.warning("Timed out waiting for task. Check the Details tab for status.")
    return False


def status_chip(status_val):
    chip_class = f"chip-{status_val}" if status_val in ("raw","processed","labeled","reviewed","ready") else "chip-raw"
    return f'<span class="status-chip {chip_class}">{status_val}</span>'


def role_pill(role):
    cls = f"pill-{role}" if role in ("admin", "reviewer", "annotator", "viewer") else "pill-viewer"
    return f'<span class="role-pill {cls}">{role}</span>'


# ════════════════════════════════════════════════
#  SIDEBAR
# ════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### 🧠 Orchestraty")
    st.caption("Enterprise AI Operating System")
    st.markdown("---")

    # Connection indicator
    try:
        r = requests.get(f"{API_BASE}/health", timeout=3)
        if r.status_code == 200:
            st.markdown("🟢 API Connected")
        else:
            st.markdown("🟡 API Error")
    except Exception:
        st.markdown("🔴 API Offline")

    st.markdown("---")

    # Auth section
    if st.session_state.token and st.session_state.user:
        u = st.session_state.user
        st.markdown(f"**{u.get('full_name', 'User')}**")
        st.caption(f"{u.get('email', '')} · {u.get('role', 'viewer')}")

        st.markdown("---")

        # Dataset selector in sidebar
        st.markdown("**Your Datasets**")
        ok, ds_data = api_call("get", "/api/v1/datasets/")
        datasets = ds_data.get("datasets", []) if ok else []

        if datasets:
            ds_names = {f"{d['name']}": d for d in datasets}
            selected_name = st.selectbox(
                "Select a dataset to work with",
                list(ds_names.keys()),
                label_visibility="collapsed",
            )
            st.session_state.selected_dataset = ds_names[selected_name]
        else:
            st.info("No datasets yet")
            st.session_state.selected_dataset = None

        st.markdown("---")

        # Navigation
        st.markdown("**Navigation**")
        nav_options = ["Pipeline", "Workflows", "LLM Settings"]
        if u.get("role") == "admin":
            nav_options.append("Admin")
        nav = st.radio(
            "View",
            nav_options,
            label_visibility="collapsed",
            horizontal=True,
        )
        if "nav_view" not in st.session_state:
            st.session_state.nav_view = "Pipeline"
        st.session_state.nav_view = nav

        st.markdown("---")
        if st.button("Logout", width="stretch"):
            st.session_state.token = None
            st.session_state.user = None
            st.session_state.selected_dataset = None
            _clear_token()
            st.rerun()
    else:
        auth_mode = st.radio("Account", ["Login", "Register"], horizontal=True, label_visibility="collapsed")

        if auth_mode == "Register":
            with st.form("reg"):
                reg_email = st.text_input("Email", placeholder="you@company.com")
                reg_pass = st.text_input("Password", type="password")
                reg_name = st.text_input("Full Name")
                if st.form_submit_button("Create Account", width="stretch"):
                    if reg_email and reg_pass and reg_name:
                        ok, data = api_call("post", "/api/v1/auth/register", json={
                            "email": reg_email, "password": reg_pass, "full_name": reg_name})
                        if ok:
                            st.session_state.token = data["token"]["access_token"]
                            st.session_state.user = data["user"]
                            _save_token(data["token"]["access_token"])
                            st.rerun()
                        else:
                            show_error(data)
        else:
            with st.form("login"):
                l_email = st.text_input("Email", placeholder="you@company.com")
                l_pass = st.text_input("Password", type="password")
                if st.form_submit_button("Sign In", width="stretch"):
                    if l_email and l_pass:
                        ok, data = api_call("post", "/api/v1/auth/login",
                                            data={"username": l_email, "password": l_pass})
                        if ok:
                            st.session_state.token = data["access_token"]
                            _save_token(data["access_token"])
                            ok2, user = api_call("get", "/api/v1/auth/me")
                            if ok2:
                                st.session_state.user = user
                            st.rerun()
                        else:
                            show_error(data)


# ── Login gate ──
if not st.session_state.token:
    st.markdown("""
    <div class="welcome-card">
        <h2>🧠 Orchestraty</h2>
        <p>Enterprise AI Operating System for intelligent data processing, analysis, and labeling.</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ════════════════════════════════════════════════
#  WORKFLOWS VIEW
# ════════════════════════════════════════════════

if st.session_state.get("nav_view") == "Workflows":
    st.markdown("""
    <div class="hero-banner">
        <h1>Workflow Pipelines</h1>
        <p>Automate multi-step data processing with pre-built or custom workflow templates</p>
    </div>
    """, unsafe_allow_html=True)

    wf_tab1, wf_tab2, wf_tab3 = st.tabs(["My Workflows", "Quick Start", "Templates"])

    with wf_tab1:
        # List user's workflows
        ok_wf, wf_data = api_call("get", "/api/v1/workflows/")
        workflows = wf_data if ok_wf and isinstance(wf_data, list) else []

        if workflows:
            for wf in workflows:
                status_icon = {"draft": "📝", "running": "🔄", "paused": "⏸️",
                               "completed": "✅", "failed": "❌"}.get(wf.get("status", ""), "❓")
                with st.expander(f"{status_icon} **{wf.get('name', 'Untitled')}** — {wf.get('status', '?')}", expanded=False):
                    wc1, wc2, wc3 = st.columns(3)
                    wc1.metric("Status", wf.get("status", "—"))
                    wc2.metric("Step", f"{wf.get('current_step', 0)}/{wf.get('total_steps', 0)}")
                    wc3.metric("ID", str(wf.get("id", ""))[:8] + "...")

                    # Actions
                    ac1, ac2, ac3 = st.columns(3)
                    if wf.get("status") == "running":
                        if ac1.button("Pause", key=f"pause_{wf['id']}", width="stretch"):
                            ok_p, _ = api_call("put", f"/api/v1/workflows/{wf['id']}/pause")
                            if ok_p:
                                st.rerun()
                    elif wf.get("status") == "paused":
                        if ac1.button("Resume", key=f"resume_{wf['id']}", width="stretch"):
                            ok_r, _ = api_call("put", f"/api/v1/workflows/{wf['id']}/resume")
                            if ok_r:
                                st.rerun()

                    if ac2.button("View Details", key=f"detail_{wf['id']}", width="stretch"):
                        ok_d, d_data = api_call("get", f"/api/v1/workflows/{wf['id']}")
                        if ok_d:
                            steps = d_data.get("steps", [])
                            if steps:
                                step_rows = []
                                for i, s in enumerate(steps):
                                    s_icon = {"pending": "⏳", "running": "🔄", "completed": "✅",
                                              "failed": "❌", "skipped": "⏭️"}.get(
                                        s.get("status", "pending"), "❓")
                                    step_rows.append({
                                        "#": i + 1,
                                        "Status": f"{s_icon} {s.get('status', 'pending')}",
                                        "Type": s.get("step_type", "—"),
                                        "Name": s.get("name", "—"),
                                    })
                                st.dataframe(pd.DataFrame(step_rows), width="stretch",
                                             hide_index=True)

                    if ac3.button("Delete", key=f"del_wf_{wf['id']}", width="stretch"):
                        ok_del, _ = api_call("delete", f"/api/v1/workflows/{wf['id']}")
                        if ok_del:
                            st.rerun()
        else:
            st.info("No workflows yet. Use Quick Start or create one from a template.")

    with wf_tab2:
        st.caption("Instantly create and run a pipeline for any of your datasets.")
        # Get datasets for selection
        ok_ds, ds_list = api_call("get", "/api/v1/datasets/")
        datasets = ds_list.get("datasets", []) if ok_ds else []

        if datasets:
            ds_options = {d["name"]: d for d in datasets}
            qs_ds = st.selectbox("Select Dataset", list(ds_options.keys()), key="qs_ds")
            qs_dataset = ds_options[qs_ds]

            if st.button("Start Default Pipeline", type="primary", width="stretch"):
                with st.spinner("Creating and starting pipeline..."):
                    ok, data = api_call("post", f"/api/v1/workflows/quick-start/{qs_dataset['id']}")
                if ok:
                    st.success(f"Workflow '{data.get('name', 'Pipeline')}' started!")
                    st.rerun()
                else:
                    show_error(data)
        else:
            st.info("Create a dataset first to use Quick Start.")

    with wf_tab3:
        ok_t, t_data = api_call("get", "/api/v1/workflows/templates")
        templates = t_data.get("templates", []) if ok_t else []

        if templates:
            for tmpl in templates:
                with st.expander(f"**{tmpl.get('name', 'Unnamed')}** — {tmpl.get('category', 'general')}"):
                    st.markdown(tmpl.get("description", "No description"))
                    steps = tmpl.get("steps", [])
                    if steps:
                        st.caption(f"{len(steps)} steps: " + " → ".join(
                            s.get("name", s.get("step_type", "?")) for s in steps))

                    if datasets:
                        tmpl_ds = st.selectbox("Dataset", list(ds_options.keys()),
                                               key=f"tmpl_ds_{tmpl.get('name', '')}")
                        if st.button("Use Template", key=f"use_{tmpl.get('name', '')}",
                                     width="stretch"):
                            tmpl_dataset = ds_options[tmpl_ds]
                            with st.spinner("Starting workflow..."):
                                ok, data = api_call("post", "/api/v1/workflows/", json={
                                    "name": f"{tmpl['name']} — {tmpl_ds}",
                                    "dataset_id": tmpl_dataset["id"],
                                    "steps": steps,
                                })
                            if ok:
                                st.success("Workflow started!")
                                st.rerun()
                            else:
                                show_error(data)
        else:
            st.info("No templates available.")

    st.stop()


# ════════════════════════════════════════════════
#  LLM SETTINGS VIEW
# ════════════════════════════════════════════════

if st.session_state.get("nav_view") == "LLM Settings":
    st.markdown("""
    <div class="hero-banner">
        <h1>LLM Provider Settings</h1>
        <p>Configure AI model providers — local (Ollama) or cloud APIs (OpenAI, Anthropic, Groq)</p>
    </div>
    """, unsafe_allow_html=True)

    # Fetch providers and current configs
    ok_prov, prov_data = api_call("get", "/api/v1/llm/providers")
    ok_cfg, cfg_data = api_call("get", "/api/v1/llm/config")

    providers = prov_data.get("providers", []) if ok_prov else []
    saved_configs = cfg_data.get("configs", []) if ok_cfg else []
    saved_map = {c["provider"]: c for c in saved_configs}

    # Show saved configs
    if saved_configs:
        st.markdown('<div class="section-title">Active Configurations</div>', unsafe_allow_html=True)
        for cfg in saved_configs:
            provider_name = cfg["provider"]
            # Find display name
            disp = provider_name
            for p in providers:
                if p["name"] == provider_name:
                    disp = p["display_name"]
                    break
            default_tag = " ⭐ DEFAULT" if cfg.get("is_default") else ""
            key_status = "🔑 Key set" if cfg.get("has_api_key") else "🔓 No key"

            with st.expander(f"**{disp}**{default_tag} — {cfg['model_name']}  ({key_status})", expanded=False):
                ec1, ec2, ec3 = st.columns(3)
                ec1.metric("Model", cfg["model_name"])
                ec2.metric("Temperature", f"{cfg['temperature']:.1f}")
                ec3.metric("Max Tokens", f"{cfg['max_tokens']:,}")

                if cfg.get("base_url"):
                    st.caption(f"Base URL: `{cfg['base_url']}`")

                dc1, dc2 = st.columns(2)
                if dc1.button("Test Connection", key=f"test_{provider_name}", width="stretch"):
                    with st.spinner(f"Testing {disp}..."):
                        ok_t, t_data = api_call("post", "/api/v1/llm/test", json={
                            "provider": provider_name,
                            "model_name": cfg["model_name"],
                            "base_url": cfg.get("base_url"),
                        })
                    if ok_t and t_data.get("success"):
                        st.success(f"Connected! Latency: {t_data.get('latency_ms', 0):.0f}ms — \"{t_data.get('message', '')[:80]}\"")
                    else:
                        msg = t_data.get("message", "Unknown error") if ok_t else str(t_data)
                        st.error(f"Connection failed: {msg}")

                if dc2.button("Remove", key=f"del_{provider_name}", width="stretch"):
                    ok_d, _ = api_call("delete", f"/api/v1/llm/config/{provider_name}")
                    if ok_d:
                        st.success(f"Removed {disp} configuration")
                        st.rerun()
                    else:
                        show_error(_)
    else:
        st.info("No LLM providers configured yet. Add one below to get started.")

    # Add / Update provider
    st.markdown('<div class="section-title">Add / Update Provider</div>', unsafe_allow_html=True)

    if providers:
        provider_options = {p["display_name"]: p for p in providers}
        selected_display = st.selectbox("Provider", list(provider_options.keys()))
        sel_prov = provider_options[selected_display]
        sel_name = sel_prov["name"]

        # Fetch models for this provider
        ok_m, m_data = api_call("get", f"/api/v1/llm/models/{sel_name}")
        model_list = m_data.get("models", []) if ok_m else sel_prov.get("models", [])
        model_source = m_data.get("source", "defaults") if ok_m else "defaults"
        model_warning = m_data.get("warning") if ok_m else None

        if model_warning:
            st.warning(model_warning)
        if model_source == "live":
            st.caption(f"Models fetched live from {sel_name}")

        # Pre-fill from saved config if exists
        existing = saved_map.get(sel_name, {})

        with st.form("llm_config_form"):
            fc1, fc2 = st.columns(2)

            if model_list:
                # If existing model is in list, select it; otherwise default to first
                default_idx = 0
                if existing.get("model_name") in model_list:
                    default_idx = model_list.index(existing["model_name"])
                model_name = fc1.selectbox("Model", model_list, index=default_idx)
            else:
                model_name = fc1.text_input("Model Name", value=existing.get("model_name", ""),
                                            placeholder="e.g., my-model")

            if sel_prov.get("requires_api_key"):
                api_key = fc2.text_input("API Key", type="password",
                                         placeholder="sk-... (leave blank to keep existing)",
                                         help="Leave empty to keep existing key")
            else:
                api_key = None
                fc2.caption("No API key required for this provider")

            fc3, fc4 = st.columns(2)
            temperature = fc3.slider("Temperature", 0.0, 2.0,
                                     value=existing.get("temperature", 0.7), step=0.1)
            max_tokens = fc4.number_input("Max Tokens", min_value=1, max_value=128000,
                                          value=existing.get("max_tokens", 2048))

            base_url = st.text_input(
                "Base URL (optional)",
                value=existing.get("base_url", sel_prov.get("default_base_url") or ""),
                placeholder="Leave default or enter custom endpoint",
                help="Override the default API endpoint"
            )

            is_default = st.checkbox("Set as default provider", value=existing.get("is_default", False))

            if st.form_submit_button("Save Configuration", type="primary", width="stretch"):
                if model_name:
                    payload = {
                        "provider": sel_name,
                        "model_name": model_name,
                        "temperature": temperature,
                        "max_tokens": int(max_tokens),
                        "is_default": is_default,
                    }
                    if api_key:
                        payload["api_key"] = api_key
                    if base_url:
                        payload["base_url"] = base_url

                    ok_s, s_data = api_call("post", "/api/v1/llm/config", json=payload)
                    if ok_s:
                        st.success(f"Saved {selected_display} configuration!")
                        st.rerun()
                    else:
                        show_error(s_data)
                else:
                    st.error("Please select or enter a model name")
    else:
        st.error("Could not load provider list. Is the API running?")

    # Quick test any arbitrary provider
    st.markdown('<div class="section-title">Quick Connection Test</div>', unsafe_allow_html=True)
    st.caption("Test any provider without saving — useful for verifying API keys before committing.")

    with st.form("quick_test_form"):
        qt1, qt2 = st.columns(2)
        qt_provider = qt1.selectbox("Provider", [p["name"] for p in providers],
                                    key="qt_prov") if providers else qt1.text_input("Provider")
        qt_model = qt2.text_input("Model", placeholder="e.g., llama3, gpt-4o")
        qt3, qt4 = st.columns(2)
        qt_key = qt3.text_input("API Key", type="password", placeholder="(if required)")
        qt_url = qt4.text_input("Base URL", placeholder="(optional override)")

        if st.form_submit_button("Test", width="stretch"):
            if qt_model:
                payload = {"provider": qt_provider, "model_name": qt_model}
                if qt_key:
                    payload["api_key"] = qt_key
                if qt_url:
                    payload["base_url"] = qt_url
                with st.spinner("Testing connection..."):
                    ok_qt, qt_data = api_call("post", "/api/v1/llm/test", json=payload)
                if ok_qt and qt_data.get("success"):
                    st.success(
                        f"Connected to `{qt_data.get('model', '')}` in "
                        f"{qt_data.get('latency_ms', 0):.0f}ms — \"{qt_data.get('message', '')[:100]}\""
                    )
                else:
                    msg = qt_data.get("message", "Unknown error") if ok_qt else str(qt_data)
                    st.error(f"Failed: {msg}")
            else:
                st.error("Please enter a model name")

    st.stop()


# ════════════════════════════════════════════════
#  ADMIN SERVICES VIEW (admin role only)
# ════════════════════════════════════════════════

if st.session_state.get("nav_view") == "Admin":
    st.markdown("""
    <div class="hero-banner" style="background: linear-gradient(135deg, #0f172a 0%, #dc2626 100%);">
        <h1>Admin Services</h1>
        <p>System administration, user management, and monitoring</p>
    </div>
    """, unsafe_allow_html=True)

    admin_tab1, admin_tab2, admin_tab3, admin_tab4, admin_tab5, admin_tab6 = st.tabs(
        ["Overview", "User Management", "Datasets", "Task Monitor", "Audit Trail", "System Health"]
    )

    # ── Overview Tab ──
    with admin_tab1:
        ok, stats = api_call("get", "/api/v1/admin/stats")
        if ok:
            st.markdown(f"""
            <div class="stat-row">
                <div class="stat-card"><div class="num">{stats['total_users']}</div><div class="lbl">Total Users</div></div>
                <div class="stat-card"><div class="num">{stats['active_users']}</div><div class="lbl">Active Users</div></div>
                <div class="stat-card"><div class="num">{stats['admin_count']}</div><div class="lbl">Admins</div></div>
                <div class="stat-card"><div class="num">{stats['total_datasets']}</div><div class="lbl">Datasets</div></div>
            </div>
            <div class="stat-row">
                <div class="stat-card"><div class="num">{stats['total_tasks']}</div><div class="lbl">Total Tasks</div></div>
                <div class="stat-card"><div class="num">{stats['completed_tasks']}</div><div class="lbl">Completed</div></div>
                <div class="stat-card"><div class="num">{stats['failed_tasks']}</div><div class="lbl">Failed</div></div>
                <div class="stat-card"><div class="num">{stats['total_tasks'] - stats['completed_tasks'] - stats['failed_tasks']}</div><div class="lbl">In Progress</div></div>
            </div>
            """, unsafe_allow_html=True)
        else:
            show_error({"detail": "Could not load stats. Is the API running?"})

        st.markdown('<div class="sec-title">Recent Activity</div>', unsafe_allow_html=True)
        ok, logs = api_call("get", "/api/v1/audit-logs/?limit=8")
        if ok:
            log_list = logs if isinstance(logs, list) else logs.get("logs", logs.get("items", []))
            if log_list:
                rows = [{
                    "Time": l.get("timestamp", "")[:19],
                    "Action": l.get("action", ""),
                    "Resource": l.get("resource_type", ""),
                    "Details": json.dumps(l.get("details", {}))[:80],
                } for l in log_list]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.caption("No recent activity.")

    # ── User Management Tab ──
    with admin_tab2:
        ok, users = api_call("get", "/api/v1/admin/users")

        if ok and users:
            role_counts = {}
            for usr in users:
                role_counts[usr["role"]] = role_counts.get(usr["role"], 0) + 1
            active_count = sum(1 for usr in users if usr["is_active"])

            pills_html = " &nbsp; ".join(
                f'{role_pill(r)} <strong>{c}</strong>' for r, c in sorted(role_counts.items())
            )
            st.markdown(
                f'<div style="margin-bottom:1rem;">{pills_html} &nbsp;&nbsp; '
                f'<span style="color:#64748b;">({active_count} active, {len(users) - active_count} inactive)</span></div>',
                unsafe_allow_html=True,
            )

            st.markdown('<div class="sec-title">All Users</div>', unsafe_allow_html=True)

            for usr in users:
                with st.expander(
                    f"{'🟢' if usr['is_active'] else '🔴'} **{usr.get('full_name') or usr['email']}** — "
                    f"`{usr['email']}` · `{usr['role']}`"
                ):
                    col_info, col_role, col_status = st.columns([2, 1, 1])

                    with col_info:
                        st.markdown(f"**ID:** `{usr['id']}`")
                        st.markdown(f"**Joined:** {usr.get('created_at', 'N/A')[:19]}")
                        st.markdown(f"**Active:** {'Yes' if usr['is_active'] else 'No'}")

                    with col_role:
                        st.markdown("**Change Role**")
                        new_role = st.selectbox(
                            "Role",
                            ["admin", "reviewer", "annotator", "viewer"],
                            index=["admin", "reviewer", "annotator", "viewer"].index(usr["role"]),
                            key=f"admin_role_{usr['id']}",
                            label_visibility="collapsed",
                        )
                        if st.button("Apply Role", key=f"admin_apply_role_{usr['id']}", width="stretch"):
                            ok2, res = api_call("put", f"/api/v1/admin/users/{usr['id']}/role",
                                                json={"role": new_role})
                            if ok2:
                                st.success(res.get("message", "Role updated"))
                                st.rerun()
                            else:
                                show_error(res)

                    with col_status:
                        st.markdown("**Account Status**")
                        if usr["is_active"]:
                            if st.button("Deactivate", key=f"admin_deact_{usr['id']}", width="stretch"):
                                ok2, res = api_call("put", f"/api/v1/admin/users/{usr['id']}/status",
                                                    json={"is_active": False})
                                if ok2:
                                    st.success("User deactivated")
                                    st.rerun()
                                else:
                                    show_error(res)
                        else:
                            if st.button("Activate", key=f"admin_act_{usr['id']}", type="primary", width="stretch"):
                                ok2, res = api_call("put", f"/api/v1/admin/users/{usr['id']}/status",
                                                    json={"is_active": True})
                                if ok2:
                                    st.success("User activated")
                                    st.rerun()
                                else:
                                    show_error(res)

                        st.markdown("---")
                        if st.button("Delete User", key=f"admin_del_{usr['id']}", type="secondary"):
                            ok3, res = api_call("delete", f"/api/v1/admin/users/{usr['id']}")
                            if ok3:
                                st.success(res.get("message", "User deleted"))
                                st.rerun()
                            else:
                                show_error(res)

        elif ok:
            st.info("No users found.")
        else:
            show_error({"detail": "Failed to load users."})

        # Create new user
        st.markdown('<div class="sec-title">Create New User</div>', unsafe_allow_html=True)
        with st.form("admin_create_user"):
            cu1, cu2 = st.columns(2)
            new_email = cu1.text_input("Email", placeholder="user@company.com", key="admin_new_email")
            new_name = cu2.text_input("Full Name", placeholder="John Doe", key="admin_new_name")
            cp1, cp2 = st.columns(2)
            new_pass = cp1.text_input("Password", type="password", key="admin_new_pass")
            new_role_create = cp2.selectbox("Role", ["viewer", "annotator", "reviewer", "admin"], key="admin_new_role")

            if st.form_submit_button("Create User", width="stretch"):
                if new_email and new_name and new_pass:
                    ok, data = api_call("post", "/api/v1/auth/register", json={
                        "email": new_email,
                        "password": new_pass,
                        "full_name": new_name,
                        "role": new_role_create,
                    })
                    if ok:
                        st.success(f"User '{new_email}' created as {new_role_create}!")
                        st.rerun()
                    else:
                        show_error(data)

    # ── Datasets Tab (admin view) ──
    with admin_tab3:
        ok, ds_data = api_call("get", "/api/v1/datasets/")
        if ok:
            datasets_all = ds_data.get("datasets", [])
            if datasets_all:
                status_counts = {}
                for d in datasets_all:
                    s = d.get("status", "raw")
                    status_counts[s] = status_counts.get(s, 0) + 1

                chips = " &nbsp; ".join(
                    f'<span style="background:#f1f5f9; padding:4px 10px; border-radius:12px; font-size:0.8rem;">'
                    f'{s}: <strong>{c}</strong></span>'
                    for s, c in sorted(status_counts.items())
                )
                st.markdown(f'<div style="margin-bottom:1rem;">{chips}</div>', unsafe_allow_html=True)

                rows = [{
                    "Name": d["name"],
                    "Status": d.get("status", "raw"),
                    "Source": d.get("source_type", "—"),
                    "Created": d.get("created_at", "")[:19],
                    "ID": d["id"][:12] + "...",
                } for d in datasets_all]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.info("No datasets created yet.")
        else:
            show_error({"detail": "Failed to load datasets."})

    # ── Task Monitor Tab ──
    with admin_tab4:
        fc1, fc2 = st.columns(2)
        type_f = fc1.selectbox("Filter by type", ["All", "structuring", "eda", "labeling"], key="admin_task_type")
        status_f = fc2.selectbox("Filter by status", ["All", "pending", "running", "completed", "failed"], key="admin_task_status")

        endpoint = "/api/v1/tasks/"
        params = []
        if type_f != "All":
            params.append(f"task_type={type_f}")
        if status_f != "All":
            params.append(f"status={status_f}")
        if params:
            endpoint += "?" + "&".join(params)

        ok, tasks = api_call("get", endpoint)
        if ok:
            task_list = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
            if task_list:
                icons = {"pending": "🟡", "running": "🔵", "completed": "🟢", "failed": "🔴"}
                rows = [{
                    "Status": f"{icons.get(t.get('status',''), '⚪')} {t.get('status', '—')}",
                    "Type": t.get("task_type", "—"),
                    "Progress": f"{t.get('progress', 0)*100:.0f}%" if t.get("progress") else "—",
                    "Message": t.get("progress_message", "—"),
                    "Dataset": str(t.get("dataset_id", ""))[:8] + "..." if t.get("dataset_id") else "—",
                    "Created": t.get("created_at", "")[:19] if t.get("created_at") else "—",
                } for t in task_list]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                st.caption(f"{len(task_list)} tasks")
            else:
                st.info("No tasks match the filter.")
        else:
            show_error({"detail": "Failed to load tasks."})

    # ── Audit Trail Tab ──
    with admin_tab5:
        limit = st.slider("Entries to show", 10, 200, 50, key="admin_audit_limit")
        ok, data = api_call("get", f"/api/v1/audit-logs/?limit={limit}")
        if ok:
            audit_logs = data if isinstance(data, list) else data.get("logs", data.get("items", []))
            if audit_logs:
                rows = [{
                    "Timestamp": l.get("timestamp", "")[:19],
                    "Action": l.get("action", ""),
                    "Resource": l.get("resource_type", ""),
                    "Resource ID": str(l.get("resource_id", ""))[:12] + "..." if l.get("resource_id") else "—",
                    "Details": json.dumps(l.get("details", {}), default=str)[:100],
                } for l in audit_logs]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                st.caption(f"{len(audit_logs)} entries")
            else:
                st.info("No logs yet.")

    # ── System Health Tab ──
    with admin_tab6:
        services = [
            ("API Server", f"{API_BASE}/health"),
            ("MinIO Storage", "http://localhost:9001"),
            ("Qdrant Vectors", "http://localhost:6333/collections"),
        ]

        cols = st.columns(len(services))
        for i, (name, url) in enumerate(services):
            with cols[i]:
                try:
                    r = requests.get(url, timeout=3)
                    if r.status_code < 400:
                        st.markdown(f"""
                        <div class="stat-card" style="border-color:#10b981;">
                            <div class="num" style="color:#10b981; font-size:1.5rem;">●</div>
                            <div class="lbl">{name} — Online</div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
                        <div class="stat-card" style="border-color:#d97706;">
                            <div class="num" style="color:#d97706; font-size:1.5rem;">●</div>
                            <div class="lbl">{name} — Error</div>
                        </div>
                        """, unsafe_allow_html=True)
                except Exception:
                    st.markdown(f"""
                    <div class="stat-card" style="border-color:#dc2626;">
                        <div class="num" style="color:#dc2626; font-size:1.5rem;">●</div>
                        <div class="lbl">{name} — Offline</div>
                    </div>
                    """, unsafe_allow_html=True)

        st.markdown('<div class="sec-title">API Info</div>', unsafe_allow_html=True)
        ok, health = api_call("get", "/health")
        if ok:
            st.json(health)

        st.markdown('<div class="sec-title">Quick Reference</div>', unsafe_allow_html=True)
        st.code("""# Container management
docker compose -f infrastructure/docker-compose.yml ps
docker compose -f infrastructure/docker-compose.yml logs -f api
docker compose -f infrastructure/docker-compose.yml logs -f celery-worker
docker compose -f infrastructure/docker-compose.yml restart api

# User management (inside API container)
docker exec -it orchestraty-api python manage.py list-users
docker exec -it orchestraty-api python manage.py promote-admin user@email.com
docker exec -it orchestraty-api python manage.py create-superuser
""", language="bash")

    st.stop()


# ════════════════════════════════════════════════
#  MAIN AREA — PIPELINE VIEW
# ════════════════════════════════════════════════

ds = st.session_state.selected_dataset

# If no dataset selected yet, show the create form
if ds is None:
    st.markdown("""
    <div class="hero-banner">
        <h1>Get Started</h1>
        <p>Create your first dataset to begin the data intelligence pipeline</p>
    </div>
    """, unsafe_allow_html=True)

    with st.form("create_ds_main"):
        c1, c2 = st.columns(2)
        ds_name = c1.text_input("Dataset Name", placeholder="e.g., Customer Feedback Q4")
        ds_source = c2.selectbox("Source Type", ["csv", "json", "api", "database", "image"])
        ds_desc = st.text_area("Description", placeholder="What is this dataset about?", height=80)
        if st.form_submit_button("Create Dataset", type="primary", width="stretch"):
            if ds_name:
                ok, data = api_call("post", "/api/v1/datasets/", json={
                    "name": ds_name, "description": ds_desc, "source_type": ds_source})
                if ok:
                    st.success(f"Dataset **{ds_name}** created!")
                    st.rerun()
                else:
                    show_error(data)
    st.stop()


# ── Dataset header ──
ds_id = ds["id"]
ds_status = ds.get("status", "raw")

st.markdown(f"""
<div class="hero-banner">
    <h1>{ds['name']}</h1>
    <p>{ds.get('description', 'No description')} &nbsp;&nbsp; {status_chip(ds_status)}</p>
</div>
""", unsafe_allow_html=True)

# ── Pipeline tabs ──
tab_upload, tab_structure, tab_eda, tab_label, tab_images, tab_info = st.tabs([
    "📤 Upload Data", "🔧 Structure & Clean", "📈 Analyze (EDA)", "🏷️ Label", "🖼️ Images", "ℹ️ Details"
])


# ════════════════════════════════════════════════
#  STEP 1: UPLOAD
# ════════════════════════════════════════════════
with tab_upload:
    st.markdown('<div class="section-title">Upload a Data File</div>', unsafe_allow_html=True)

    uploaded_file = st.file_uploader(
        "Drag and drop or browse",
        type=["csv", "tsv", "json", "jsonl", "txt"],
        help="Supported formats: CSV, TSV, JSON, JSONL — max 500 MB",
        label_visibility="collapsed",
    )

    if uploaded_file:
        st.markdown(f"**Selected:** `{uploaded_file.name}` ({uploaded_file.size:,} bytes)")

        if st.button("Upload & Validate", type="primary", width="stretch"):
            with st.spinner("Uploading and validating..."):
                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                ok, data = api_call("post", f"/api/v1/datasets/{ds_id}/upload", files=files)

            if ok:
                st.success("File uploaded and validated successfully!")

                validation = data.get("validation", {})
                c1, c2, c3 = st.columns(3)
                c1.metric("Rows", f"{validation.get('row_count', 0):,}")
                c2.metric("Columns", validation.get("column_count", 0))
                c3.metric("Valid", "Yes" if validation.get("is_valid") else "No")

                if validation.get("columns"):
                    st.markdown('<div class="section-title">Detected Columns</div>', unsafe_allow_html=True)
                    cols_df = pd.DataFrame({"Column": validation["columns"]})
                    st.dataframe(cols_df, width="stretch", hide_index=True)

                if validation.get("sample_data"):
                    st.markdown('<div class="section-title">Data Preview</div>', unsafe_allow_html=True)
                    try:
                        st.dataframe(pd.DataFrame(validation["sample_data"]), width="stretch")
                    except Exception:
                        st.json(validation["sample_data"])

                if validation.get("warnings"):
                    for w in validation["warnings"]:
                        st.warning(w)

                version = data.get("version", {})
                if version:
                    st.caption(
                        f"Version v{version.get('version_number', '?')} · "
                        f"{version.get('file_size', 0):,} bytes · "
                        f"Hash: {version.get('schema_hash', 'N/A')[:16]}..."
                    )
            else:
                show_error(data)

    # Show existing versions
    ok, detail = api_call("get", f"/api/v1/datasets/{ds_id}")
    if ok:
        versions = detail.get("versions", [])
        if versions:
            st.markdown('<div class="section-title">Uploaded Versions</div>', unsafe_allow_html=True)
            v_rows = []
            for v in versions:
                v_rows.append({
                    "Version": f"v{v.get('version_number', '?')}",
                    "File": v.get("file_name", "—"),
                    "Size": f"{v.get('file_size', 0):,} bytes",
                    "Rows": v.get("row_count", "—"),
                    "Uploaded": v.get("created_at", "")[:19],
                })
            st.dataframe(pd.DataFrame(v_rows), width="stretch", hide_index=True)


# ════════════════════════════════════════════════
#  STEP 2: STRUCTURE & CLEAN
# ════════════════════════════════════════════════
with tab_structure:
    col_config, col_results = st.columns([1, 2])

    with col_config:
        st.markdown('<div class="section-title">Configuration</div>', unsafe_allow_html=True)

        with st.form("struct_form"):
            null_strategy = st.selectbox("Null Handling Strategy", [
                "fill_mode", "fill_mean", "fill_median", "fill_empty", "drop_rows"
            ], help="How to handle missing values in the dataset")
            remove_dupes = st.checkbox("Remove duplicate rows", value=True)
            handle_outliers = st.checkbox("Remove outliers (IQR method)", value=False)

            submitted_struct = st.form_submit_button("Run Structuring Pipeline", type="primary", width="stretch")

        # Auto-poll OUTSIDE the form (Streamlit requires this)
        if submitted_struct:
            with st.spinner("Starting structuring pipeline..."):
                ok, data = api_call("post", "/api/v1/structuring/run", json={
                    "dataset_id": ds_id,
                    "null_strategy": null_strategy,
                    "remove_duplicates": remove_dupes,
                    "handle_outliers": handle_outliers,
                })
            if ok:
                task_id = data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(data)

    with col_results:
        st.markdown('<div class="section-title">Results</div>', unsafe_allow_html=True)

        ok, results = api_call("get", f"/api/v1/structuring/results/{ds_id}")
        if ok:
            result = results.get("result", {})
            quality = result.get("quality", {})

            if quality:
                score = quality.get("overall_score", 0)
                grade = quality.get("grade", "?")

                # Score display
                st.markdown(f"""
                <div style="display:flex; align-items:center; gap:16px; margin-bottom:1rem;">
                    <div class="grade-badge grade-{grade}">{grade}</div>
                    <div>
                        <div style="font-size:1.5rem; font-weight:700; color:#0f172a;">{score:.1f}<span style="font-size:0.9rem; color:#64748b;">/100</span></div>
                        <div style="font-size:0.8rem; color:#64748b;">Overall Quality Score</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                c1, c2 = st.columns(2)
                c1.metric("Original Rows", f"{result.get('original_rows', 0):,}")
                c2.metric("Final Rows", f"{result.get('final_rows', 0):,}")

                # Quality dimensions
                dims = quality.get("dimensions", [])
                if dims:
                    dim_data = []
                    for d in dims:
                        dim_data.append({
                            "Dimension": d["name"].title(),
                            "Score": f"{d['score']:.1f}",
                            "Weight": f"{d['weight']*100:.0f}%",
                            "Details": d["details"],
                        })
                    st.dataframe(pd.DataFrame(dim_data), width="stretch", hide_index=True)

                # Recommendations
                recs = quality.get("recommendations", [])
                if recs:
                    st.markdown('<div class="section-title">Recommendations</div>', unsafe_allow_html=True)
                    for r in recs:
                        st.info(r)

            # Schema
            schema = result.get("schema", {})
            if schema:
                with st.expander("Schema Detection", expanded=False):
                    cols = schema.get("columns", [])
                    if cols:
                        s_data = [{
                            "Column": c["name"],
                            "Type": c["inferred_type"],
                            "Pandas": c["pandas_dtype"],
                            "Nulls": f"{c['null_percentage']:.1f}%",
                            "Unique": f"{c['unique_percentage']:.1f}%",
                        } for c in cols]
                        st.dataframe(pd.DataFrame(s_data), width="stretch", hide_index=True)
                    st.json(schema.get("detected_types_summary", {}))

            # Cleaning
            cleaning = result.get("cleaning", {})
            if cleaning:
                with st.expander("Cleaning Report", expanded=False):
                    st.markdown(
                        f"**{cleaning.get('original_rows', 0):,}** rows → "
                        f"**{cleaning.get('final_rows', 0):,}** rows "
                        f"({cleaning.get('total_rows_removed', 0):,} removed)"
                    )
                    for s in cleaning.get("steps", []):
                        st.markdown(f"- **{s['step']}**: {s['description']}")

            st.caption(f"Completed: {results.get('completed_at', 'N/A')}")
        else:
            st.markdown("""
            <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
                <div style="font-size:2.5rem; margin-bottom:0.5rem;">🔧</div>
                <div style="font-weight:600;">No structuring results yet</div>
                <div style="font-size:0.85rem;">Configure and run the pipeline to see quality scores</div>
            </div>
            """, unsafe_allow_html=True)


# ════════════════════════════════════════════════
#  STEP 3: EDA
# ════════════════════════════════════════════════
with tab_eda:
    col_config, col_results = st.columns([1, 2])

    with col_config:
        st.markdown('<div class="section-title">Analysis Options</div>', unsafe_allow_html=True)

        with st.form("eda_form"):
            run_profiling = st.checkbox("Statistical Profiling", value=True,
                                       help="Generate detailed statistics for every column")
            run_embeddings = st.checkbox("Generate Embeddings", value=True,
                                        help="Create vector representations of text columns")
            run_clustering = st.checkbox("Clustering Analysis", value=True,
                                        help="Group similar rows using K-Means")
            n_clusters = st.number_input("Number of Clusters", min_value=2, max_value=20, value=5)

            submitted_eda = st.form_submit_button("Run EDA Pipeline", type="primary", width="stretch")

        # Auto-poll OUTSIDE the form
        if submitted_eda:
            with st.spinner("Starting EDA pipeline..."):
                ok, data = api_call("post", "/api/v1/eda/run", json={
                    "dataset_id": ds_id,
                    "run_profiling": run_profiling,
                    "run_embeddings": run_embeddings,
                    "run_clustering": run_clustering,
                    "n_clusters": n_clusters,
                })
            if ok:
                task_id = data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(data)

    with col_results:
        st.markdown('<div class="section-title">Analysis Results</div>', unsafe_allow_html=True)

        ok, results = api_call("get", f"/api/v1/eda/results/{ds_id}")
        if ok:
            result = results.get("result", {})

            # Profiling
            profiling = result.get("profiling", {})
            if profiling:
                c1, c2, c3 = st.columns(3)
                c1.metric("Rows", f"{profiling.get('row_count', 0):,}")
                c2.metric("Columns", profiling.get("column_count", 0))
                c3.metric("Memory", f"{profiling.get('memory_usage_mb', 0):.2f} MB")

                warnings = profiling.get("warnings", [])
                if warnings:
                    for w in warnings:
                        st.warning(w)

                cols = profiling.get("columns", [])
                if cols:
                    with st.expander("Column Profiles", expanded=True):
                        col_data = []
                        for c in cols:
                            stats = c.get("stats", {})
                            col_data.append({
                                "Column": c["name"],
                                "Type": c["dtype"],
                                "Nulls": f"{c.get('null_pct', 0):.1f}%",
                                "Unique": f"{c.get('unique_pct', 0):.1f}%",
                                "Mean": stats.get("mean", "—"),
                                "Std": stats.get("std", "—"),
                            })
                        st.dataframe(pd.DataFrame(col_data), width="stretch", hide_index=True)

            # Clustering
            clustering = result.get("clustering", {})
            if clustering:
                with st.expander("Clustering Results", expanded=True):
                    cl1, cl2, cl3 = st.columns(3)
                    cl1.metric("Clusters", clustering.get("n_clusters", 0))
                    cl2.metric("Silhouette", f"{clustering.get('silhouette_score', 0):.3f}")
                    cl3.metric("Method", clustering.get("method", "—"))

                    sizes = clustering.get("cluster_sizes", {})
                    if sizes:
                        size_df = pd.DataFrame(list(sizes.items()), columns=["Cluster", "Count"])
                        st.dataframe(size_df, width="stretch", hide_index=True)

            # Embeddings
            embeddings = result.get("embeddings", {})
            if embeddings:
                with st.expander("Embeddings"):
                    st.markdown(f"**Method:** {embeddings.get('method_used', '—')}")
                    st.markdown(f"**Vectors:** {embeddings.get('total_vectors', 0):,}")
                    st.markdown(f"**Dimensions:** {embeddings.get('vector_dimension', 0)}")
                    embedded_cols = embeddings.get("columns_embedded", [])
                    if embedded_cols:
                        st.markdown(f"**Columns:** {', '.join(embedded_cols)}")

            st.caption(f"Completed: {results.get('completed_at', 'N/A')}")
        else:
            st.markdown("""
            <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
                <div style="font-size:2.5rem; margin-bottom:0.5rem;">📈</div>
                <div style="font-weight:600;">No analysis results yet</div>
                <div style="font-size:0.85rem;">Run the EDA pipeline to explore your data</div>
            </div>
            """, unsafe_allow_html=True)


# ════════════════════════════════════════════════
#  STEP 4: LABEL
# ════════════════════════════════════════════════
with tab_label:
    col_rules, col_results = st.columns([1, 1])

    with col_rules:
        st.markdown('<div class="section-title">Define Labeling Rules</div>', unsafe_allow_html=True)

        # Fetch operators
        ok_ops, ops_data = api_call("get", "/api/v1/labeling/rules/operators")
        operators = ops_data.get("operators", [
            "equals", "not_equals", "contains", "not_contains",
            "starts_with", "ends_with", "greater_than", "less_than",
            "regex_match", "in_list", "is_null", "is_not_null"
        ]) if ok_ops else ["equals", "contains", "greater_than", "less_than", "regex_match"]

        with st.form("rule_form"):
            rc1, rc2 = st.columns(2)
            r_col = rc1.text_input("Column", placeholder="e.g., sentiment")
            r_op = rc2.selectbox("Operator", operators)
            rv1, rv2 = st.columns(2)
            r_val = rv1.text_input("Value", placeholder="e.g., positive")
            r_label = rv2.text_input("Assign Label", placeholder="e.g., positive_feedback")
            r_priority = st.slider("Priority", 0, 100, 0, help="Higher priority wins in conflicts")

            if st.form_submit_button("Add Rule", width="stretch"):
                if r_col and r_label:
                    try:
                        val = float(r_val)
                        if val == int(val):
                            val = int(val)
                    except (ValueError, TypeError):
                        val = r_val

                    st.session_state.labeling_rules.append({
                        "column": r_col, "operator": r_op,
                        "value": val, "label": r_label, "priority": r_priority,
                    })
                    st.rerun()

        # Current rules
        if st.session_state.labeling_rules:
            st.markdown(f"**Rules ({len(st.session_state.labeling_rules)}):**")
            for i, rule in enumerate(st.session_state.labeling_rules):
                st.markdown(
                    f'<div class="rule-item">'
                    f'<strong>{i+1}.</strong> '
                    f'<code>{rule["column"]}</code> '
                    f'<strong>{rule["operator"]}</strong> '
                    f'<code>{rule["value"]}</code> '
                    f'→ <strong>{rule["label"]}</strong> '
                    f'<span style="color:#94a3b8; font-size:0.8rem;">(p:{rule["priority"]})</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            conflict_strategy = st.selectbox(
                "Conflict Resolution",
                ["first_match", "highest_priority", "all"],
                help="How to handle when multiple rules match a row"
            )

            bc1, bc2 = st.columns(2)
            if bc1.button("Clear Rules", width="stretch"):
                st.session_state.labeling_rules = []
                st.rerun()
            run_labeling = bc2.button("Run Labeling", type="primary", width="stretch")

            if run_labeling:
                with st.spinner("Running labeling pipeline..."):
                    ok, data = api_call("post", "/api/v1/labeling/run", json={
                        "dataset_id": ds_id,
                        "rules": st.session_state.labeling_rules,
                        "conflict_strategy": conflict_strategy,
                    })
                if ok:
                    task_id = data.get("task_id", "")
                    progress_slot = st.empty()
                    done = poll_task_until_done(task_id, progress_slot)
                    if done:
                        time.sleep(1)
                        st.rerun()
                else:
                    show_error(data)
        else:
            st.caption("No rules defined. Add rules above to start labeling.")

    with col_results:
        st.markdown('<div class="section-title">Labeling Results</div>', unsafe_allow_html=True)

        ok, results = api_call("get", f"/api/v1/labeling/results/{ds_id}")
        if ok:
            result = results.get("result", {})
            labeling = result.get("labeling", {})

            if labeling:
                # Show warnings (e.g. non-existent column names)
                for w in labeling.get("warnings", []):
                    st.warning(w)

                lc1, lc2, lc3 = st.columns(3)
                lc1.metric("Total Rows", f"{labeling.get('total_rows', 0):,}")
                lc2.metric("Labeled", f"{labeling.get('labeled_count', 0):,}")
                lc3.metric("Unlabeled", f"{labeling.get('unlabeled_count', 0):,}")

                # Distribution
                dist = labeling.get("label_distribution", {})
                if dist:
                    st.markdown("**Label Distribution:**")
                    dist_df = pd.DataFrame(list(dist.items()), columns=["Label", "Count"])
                    st.dataframe(dist_df, width="stretch", hide_index=True)

                # Per-rule stats
                per_rule = labeling.get("per_rule_stats", [])
                if per_rule:
                    with st.expander("Per-Rule Statistics"):
                        st.dataframe(pd.DataFrame(per_rule), width="stretch", hide_index=True)

            st.caption(f"Output: {result.get('labeled_file_path', '—')}")
            st.caption(f"Completed: {results.get('completed_at', 'N/A')}")
        else:
            st.markdown("""
            <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
                <div style="font-size:2.5rem; margin-bottom:0.5rem;">🏷️</div>
                <div style="font-weight:600;">No labeling results yet</div>
                <div style="font-size:0.85rem;">Define rules and run the labeling pipeline</div>
            </div>
            """, unsafe_allow_html=True)

        # ── AI-Powered Labeling ──────────────────────────────────────────
        st.markdown('<div class="section-title">AI-Powered Labeling</div>', unsafe_allow_html=True)

        ai_tab1, ai_tab2, ai_tab3, ai_tab4 = st.tabs([
            "AI Predict", "Propagate", "Aggregate", "Synthetic Data"
        ])

        with ai_tab1:
            st.caption("Use your configured LLM to predict labels for each row.")
            with st.form("ai_predict_form"):
                ap1, ap2 = st.columns(2)
                ai_labels_str = ap1.text_input("Labels (comma-separated)",
                                               placeholder="e.g., positive, negative, neutral")
                ai_text_col = ap2.text_input("Text Column", value="text",
                                             placeholder="Column containing text to classify")
                ai_examples_str = st.text_area(
                    "Few-shot Examples (optional, JSON)",
                    placeholder='[{"text": "Great product!", "label": "positive"}]',
                    height=80,
                )
                if st.form_submit_button("Run AI Prediction", type="primary", width="stretch"):
                    if ai_labels_str:
                        ai_labels = [l.strip() for l in ai_labels_str.split(",") if l.strip()]
                        examples = None
                        if ai_examples_str.strip():
                            try:
                                examples = json.loads(ai_examples_str)
                            except json.JSONDecodeError:
                                st.error("Invalid JSON for examples")
                                examples = "INVALID"
                        if examples != "INVALID":
                            payload = {
                                "dataset_id": ds_id,
                                "labels": ai_labels,
                                "text_column": ai_text_col,
                            }
                            if examples:
                                payload["examples"] = examples
                            with st.spinner("Starting AI prediction..."):
                                ok, data = api_call("post", "/api/v1/labeling/ai/predict", json=payload)
                            if ok:
                                task_id = data.get("task_id", "")
                                progress_slot = st.empty()
                                done = poll_task_until_done(task_id, progress_slot, timeout=300)
                                if done:
                                    time.sleep(1)
                                    st.rerun()
                            else:
                                show_error(data)
                    else:
                        st.error("Please enter at least one label")

            # Show AI prediction results
            ok_ai, ai_data = api_call("get", f"/api/v1/labeling/ai/predictions/{ds_id}")
            if ok_ai:
                ai_result = ai_data.get("result", {})
                if ai_result:
                    ac1, ac2, ac3 = st.columns(3)
                    ac1.metric("Predicted", ai_result.get("labeled_count", 0))
                    ac2.metric("Avg Confidence", f"{ai_result.get('avg_confidence', 0):.2f}")
                    ac3.metric("Model", ai_result.get("model_used", "—"))

                    preds = ai_result.get("predictions", [])
                    if preds:
                        with st.expander(f"Predictions ({len(preds)} items)"):
                            pred_df = pd.DataFrame([
                                {"Text": p.get("text", "")[:80], "Label": p.get("predicted_label", ""),
                                 "Confidence": f"{p.get('confidence', 0):.2f}"}
                                for p in preds[:100]
                            ])
                            st.dataframe(pred_df, width="stretch", hide_index=True)

        with ai_tab2:
            st.caption("Propagate labels from labeled items to similar unlabeled items using embeddings.")
            with st.form("propagate_form"):
                pp1, pp2 = st.columns(2)
                prop_labels_str = pp1.text_input("Labels to propagate",
                                                 placeholder="e.g., positive, negative")
                prop_threshold = pp2.slider("Confidence Threshold", 0.0, 1.0, 0.7, 0.05)
                prop_k = st.number_input("Top-K Neighbors", min_value=1, max_value=20, value=5)

                if st.form_submit_button("Run Propagation", type="primary", width="stretch"):
                    if prop_labels_str:
                        prop_labels = [l.strip() for l in prop_labels_str.split(",") if l.strip()]
                        with st.spinner("Starting label propagation..."):
                            ok, data = api_call("post", "/api/v1/labeling/ai/propagate", json={
                                "dataset_id": ds_id,
                                "labels": prop_labels,
                                "confidence_threshold": prop_threshold,
                                "top_k": prop_k,
                            })
                        if ok:
                            task_id = data.get("task_id", "")
                            progress_slot = st.empty()
                            done = poll_task_until_done(task_id, progress_slot)
                            if done:
                                time.sleep(1)
                                st.rerun()
                        else:
                            show_error(data)

            ok_prop, prop_data = api_call("get", f"/api/v1/labeling/ai/propagation/{ds_id}")
            if ok_prop:
                prop_result = prop_data.get("result", {})
                if prop_result:
                    pc1, pc2, pc3 = st.columns(3)
                    pc1.metric("Total Unlabeled", prop_result.get("total_unlabeled", 0))
                    pc2.metric("Propagated", prop_result.get("propagated_count", 0))
                    pc3.metric("Skipped", prop_result.get("skipped_count", 0))

        with ai_tab3:
            st.caption("Merge labels from rules, AI predictions, and propagation into final labels.")
            with st.form("aggregate_form"):
                agg_strategy = st.selectbox("Aggregation Strategy",
                                           ["confidence_weighted", "majority_vote", "priority"])
                ag1, ag2, ag3 = st.columns(3)
                inc_rules = ag1.checkbox("Include Rules", value=True)
                inc_ai = ag2.checkbox("Include AI", value=True)
                inc_prop = ag3.checkbox("Include Propagated", value=True)

                if st.form_submit_button("Run Aggregation", type="primary", width="stretch"):
                    with st.spinner("Aggregating labels..."):
                        ok, data = api_call("post", "/api/v1/labeling/ai/aggregate", json={
                            "dataset_id": ds_id,
                            "strategy": agg_strategy,
                            "include_rule_labels": inc_rules,
                            "include_ai_labels": inc_ai,
                            "include_propagated_labels": inc_prop,
                        })
                    if ok:
                        task_id = data.get("task_id", "")
                        progress_slot = st.empty()
                        done = poll_task_until_done(task_id, progress_slot)
                        if done:
                            time.sleep(1)
                            st.rerun()
                    else:
                        show_error(data)

            ok_agg, agg_data = api_call("get", f"/api/v1/labeling/ai/aggregation/{ds_id}")
            if ok_agg:
                agg_result = agg_data.get("result", {})
                if agg_result:
                    agc1, agc2, agc3 = st.columns(3)
                    agc1.metric("Total Items", agg_result.get("total_items", 0))
                    agc2.metric("Labeled", agg_result.get("labeled_count", 0))
                    agc3.metric("Conflicts", agg_result.get("conflict_count", 0))

                    agg_dist = agg_result.get("label_distribution", {})
                    if agg_dist:
                        st.markdown("**Final Label Distribution:**")
                        st.dataframe(
                            pd.DataFrame(list(agg_dist.items()), columns=["Label", "Count"]),
                            width="stretch", hide_index=True,
                        )

            # Active Learning
            st.markdown("---")
            st.caption("Find items with the highest uncertainty for human review.")
            al_n = st.number_input("Top N uncertain items", min_value=5, max_value=100, value=20,
                                   key="al_n")
            if st.button("Find Uncertain Items", width="stretch"):
                with st.spinner("Analyzing uncertainty..."):
                    ok_al, al_data = api_call("post", "/api/v1/labeling/ai/active-learning",
                                              json={"dataset_id": ds_id, "top_n": al_n})
                if ok_al:
                    candidates = al_data.get("candidates", [])
                    if candidates:
                        st.markdown(f"**{len(candidates)} items need review:**")
                        al_df = pd.DataFrame([
                            {"Item ID": c.get("item_id", "")[:16] + "...",
                             "Uncertainty": f"{c.get('uncertainty_score', 0):.3f}",
                             "Conflicting Labels": ", ".join(c.get("conflicting_labels", []))}
                            for c in candidates
                        ])
                        st.dataframe(al_df, width="stretch", hide_index=True)
                    else:
                        st.info("No uncertain items found. Run multiple labeling sources first.")
                else:
                    show_error(al_data)

        with ai_tab4:
            st.caption("Generate synthetic training data using your configured LLM.")
            with st.form("synthetic_form"):
                sy1, sy2 = st.columns(2)
                syn_label = sy1.text_input("Label to generate for", placeholder="e.g., positive")
                syn_count = sy2.number_input("Number of samples", min_value=1, max_value=100, value=20)
                syn_examples_str = st.text_area(
                    "Example texts (optional, JSON array)",
                    placeholder='[{"text": "Great product!", "label": "positive"}]',
                    height=80,
                )

                if st.form_submit_button("Generate Synthetic Data", type="primary",
                                         width="stretch"):
                    if syn_label:
                        syn_examples = None
                        if syn_examples_str.strip():
                            try:
                                syn_examples = json.loads(syn_examples_str)
                            except json.JSONDecodeError:
                                st.error("Invalid JSON")
                                syn_examples = "INVALID"
                        if syn_examples != "INVALID":
                            payload = {"label": syn_label, "count": syn_count}
                            if syn_examples:
                                payload["examples"] = syn_examples
                            with st.spinner("Generating synthetic data..."):
                                ok, data = api_call("post", "/api/v1/labeling/ai/synthetic",
                                                    json=payload)
                            if ok:
                                samples = data.get("samples", [])
                                st.success(f"Generated {len(samples)} samples for '{syn_label}'")
                                if samples:
                                    st.dataframe(
                                        pd.DataFrame({"Sample": samples}),
                                        width="stretch", hide_index=True,
                                    )
                            else:
                                show_error(data)
                    else:
                        st.error("Please enter a label")


# ════════════════════════════════════════════════
#  STEP 5: IMAGES
# ════════════════════════════════════════════════
with tab_images:
    col_upload_img, col_gallery = st.columns([1, 2])

    with col_upload_img:
        st.markdown('<div class="section-title">Upload Images</div>', unsafe_allow_html=True)

        img_files = st.file_uploader(
            "Drag and drop images",
            type=["png", "jpg", "jpeg", "webp", "tiff", "bmp"],
            accept_multiple_files=True,
            help="Supported: PNG, JPG, WEBP, TIFF, BMP — max 50 MB each",
            label_visibility="collapsed",
            key="img_uploader",
        )

        if img_files:
            st.markdown(f"**Selected:** {len(img_files)} image(s)")
            if st.button("Upload Images", type="primary", width="stretch"):
                uploaded_count = 0
                error_count = 0
                progress_bar = st.progress(0)
                status_text = st.empty()

                for i, img_file in enumerate(img_files):
                    status_text.info(f"Uploading {img_file.name}... ({i+1}/{len(img_files)})")
                    files = {"file": (img_file.name, img_file.getvalue(), img_file.type or "image/png")}
                    ok, data = api_call("post", f"/api/v1/images/{ds_id}/upload", files=files)
                    if ok:
                        uploaded_count += 1
                    else:
                        error_count += 1
                        st.warning(f"Failed to upload {img_file.name}: {data.get('detail', 'Unknown error')}")
                    progress_bar.progress((i + 1) / len(img_files))

                progress_bar.empty()
                if uploaded_count > 0:
                    status_text.success(f"Uploaded {uploaded_count} image(s)" +
                                        (f" ({error_count} failed)" if error_count else ""))
                    time.sleep(1)
                    st.rerun()
                elif error_count > 0:
                    status_text.error(f"All {error_count} uploads failed")

        # AI Processing
        st.markdown('<div class="section-title">AI Processing</div>', unsafe_allow_html=True)

        if st.button("Generate CLIP Embeddings", width="stretch",
                     help="Create vector embeddings for similarity search"):
            with st.spinner("Starting embedding generation..."):
                ok, data = api_call("post", f"/api/v1/images/{ds_id}/embeddings")
            if ok:
                task_id = data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(data)

        ec1, ec2 = st.columns(2)
        cluster_size = ec1.number_input("Min Cluster Size", min_value=2, max_value=50, value=5)
        if ec2.button("Run Clustering", width="stretch"):
            with st.spinner("Starting image clustering..."):
                ok, data = api_call("post", f"/api/v1/images/{ds_id}/cluster",
                                    json={"min_cluster_size": cluster_size})
            if ok:
                task_id = data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(data)

        # Similarity search
        st.markdown('<div class="section-title">Similarity Search</div>', unsafe_allow_html=True)
        search_query = st.text_input("Search by text description", placeholder="e.g., a red car on a highway")
        search_k = st.slider("Results", 5, 50, 10, key="search_k")
        if st.button("Search", width="stretch") and search_query:
            with st.spinner("Searching..."):
                ok, data = api_call("post", f"/api/v1/images/{ds_id}/search",
                                    json={"query_text": search_query, "top_k": search_k})
            if ok:
                results = data.get("results", [])
                if results:
                    st.markdown(f"**Found {len(results)} similar images:**")
                    cols = st.columns(4)
                    for idx, r in enumerate(results):
                        asset = r.get("asset", {})
                        with cols[idx % 4]:
                            thumb_url = asset.get("thumbnail_url")
                            if thumb_url:
                                st.image(thumb_url, caption=f"{asset.get('file_name', '?')}\nScore: {r.get('score', 0):.3f}",
                                         width="stretch")
                            else:
                                st.markdown(f"**{asset.get('file_name', '?')}** — Score: {r.get('score', 0):.3f}")
                else:
                    st.info("No similar images found. Generate embeddings first.")
            else:
                show_error(data)

    with col_gallery:
        st.markdown('<div class="section-title">Image Gallery</div>', unsafe_allow_html=True)

        # Pagination
        if "img_page" not in st.session_state:
            st.session_state.img_page = 0
        page_size = 20

        # Cluster filter
        ok_cl, cl_data = api_call("get", f"/api/v1/images/{ds_id}/clusters")
        cluster_filter = None
        if ok_cl:
            cluster_summary = cl_data.get("cluster_summary", {})
            if cluster_summary:
                cluster_opts = ["All"] + [f"Cluster {k} ({v} images)" for k, v in cluster_summary.items()]
                sel_cluster = st.selectbox("Filter by cluster", cluster_opts, label_visibility="collapsed")
                if sel_cluster != "All":
                    cluster_filter = int(sel_cluster.split(" ")[1])

        # Fetch gallery
        gallery_params = f"skip={st.session_state.img_page * page_size}&limit={page_size}"
        if cluster_filter is not None:
            gallery_params += f"&cluster_id={cluster_filter}"

        ok_g, g_data = api_call("get", f"/api/v1/images/{ds_id}/gallery?{gallery_params}")

        if ok_g:
            images = g_data.get("images", [])
            total = g_data.get("total", 0)

            if images:
                st.caption(f"Showing {len(images)} of {total} images")

                # Display grid
                cols = st.columns(4)
                for idx, img in enumerate(images):
                    with cols[idx % 4]:
                        thumb_url = img.get("thumbnail_url")
                        if thumb_url:
                            st.image(thumb_url, width="stretch")
                        st.caption(
                            f"**{img.get('file_name', '?')}**\n"
                            f"{img.get('width', '?')}x{img.get('height', '?')} · "
                            f"{img.get('color_mode', '?')}"
                        )
                        cluster_tag = img.get("cluster_id")
                        if cluster_tag is not None:
                            st.caption(f"Cluster: {cluster_tag}")

                        # Expandable detail
                        with st.expander("Details"):
                            st.markdown(f"**Size:** {img.get('file_size', 0):,} bytes")
                            st.markdown(f"**Type:** {img.get('mime_type', '—')}")
                            if img.get("has_exif"):
                                st.markdown("**EXIF:** Available")
                            original_url = img.get("original_url")
                            if original_url:
                                st.markdown(f"[View Original]({original_url})")

                # Pagination controls
                total_pages = max(1, (total + page_size - 1) // page_size)
                pc1, pc2, pc3 = st.columns([1, 2, 1])
                if pc1.button("← Prev", disabled=st.session_state.img_page == 0):
                    st.session_state.img_page -= 1
                    st.rerun()
                pc2.markdown(f"<div style='text-align:center;'>Page {st.session_state.img_page + 1} / {total_pages}</div>",
                             unsafe_allow_html=True)
                if pc3.button("Next →", disabled=st.session_state.img_page >= total_pages - 1):
                    st.session_state.img_page += 1
                    st.rerun()
            else:
                st.markdown("""
                <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
                    <div style="font-size:2.5rem; margin-bottom:0.5rem;">🖼️</div>
                    <div style="font-weight:600;">No images yet</div>
                    <div style="font-size:0.85rem;">Upload images using the panel on the left</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="text-align:center; padding:3rem 1rem; color:#94a3b8;">
                <div style="font-size:2.5rem; margin-bottom:0.5rem;">🖼️</div>
                <div style="font-weight:600;">No images yet</div>
                <div style="font-size:0.85rem;">Upload images using the panel on the left</div>
            </div>
            """, unsafe_allow_html=True)


# ════════════════════════════════════════════════
#  DETAILS / INFO TAB
# ════════════════════════════════════════════════
with tab_info:
    ok, detail = api_call("get", f"/api/v1/datasets/{ds_id}")
    if ok:
        ds_detail = detail.get("dataset", {})
        versions = detail.get("versions", [])
        metadata = detail.get("metadata", [])

        st.markdown('<div class="section-title">Dataset Information</div>', unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Status", ds_detail.get("status", "—"))
        c2.metric("Source", ds_detail.get("source_type", "—"))
        c3.metric("Versions", len(versions))
        c4.metric("Metadata", len(metadata))

        st.markdown(f"**ID:** `{ds_id}`")
        st.markdown(f"**Created:** {ds_detail.get('created_at', 'N/A')[:19]}")
        st.markdown(f"**Description:** {ds_detail.get('description', 'No description')}")

        # Status update
        st.markdown('<div class="section-title">Update Status</div>', unsafe_allow_html=True)
        sc1, sc2 = st.columns([3, 1])
        new_status = sc1.selectbox(
            "New Status", ["raw", "processed", "labeled", "reviewed", "ready"],
            index=["raw", "processed", "labeled", "reviewed", "ready"].index(
                ds_detail.get("status", "raw")
            ),
            label_visibility="collapsed",
        )
        if sc2.button("Update", width="stretch"):
            ok2, _ = api_call("put", f"/api/v1/datasets/{ds_id}/status", json={"status": new_status})
            if ok2:
                st.success(f"Status updated to {new_status}")
                st.rerun()

        # Metadata
        st.markdown('<div class="section-title">Metadata</div>', unsafe_allow_html=True)
        if metadata:
            meta_rows = [{"Key": m["key"], "Value": m["value"]} for m in metadata]
            st.dataframe(pd.DataFrame(meta_rows), width="stretch", hide_index=True)

        mc1, mc2, mc3 = st.columns([2, 2, 1])
        mk = mc1.text_input("Key", placeholder="e.g., domain", label_visibility="collapsed")
        mv = mc2.text_input("Value", placeholder="e.g., customer_support", label_visibility="collapsed")
        if mc3.button("Add", width="stretch"):
            if mk and mv:
                ok3, _ = api_call("put", f"/api/v1/datasets/{ds_id}/metadata",
                                  json={"key": mk, "value": mv})
                if ok3:
                    st.success(f"Added `{mk}`")
                    st.rerun()

        # Quality & Export
        st.markdown('<div class="section-title">Quality & Export</div>', unsafe_allow_html=True)

        qe1, qe2, qe3 = st.columns(3)
        if qe1.button("Evaluate Quality", width="stretch"):
            with st.spinner("Running quality evaluation..."):
                ok_qe, qe_data = api_call("post", f"/api/v1/review/quality/{ds_id}")
            if ok_qe:
                task_id = qe_data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(qe_data)

        export_fmt = qe2.selectbox("Export Format", ["csv", "json", "coco", "yolo"],
                                   label_visibility="collapsed")
        if qe3.button("Export Dataset", width="stretch"):
            with st.spinner(f"Exporting as {export_fmt}..."):
                ok_ex, ex_data = api_call("post", f"/api/v1/review/export/{ds_id}",
                                          json={"format": export_fmt})
            if ok_ex:
                task_id = ex_data.get("task_id", "")
                progress_slot = st.empty()
                done = poll_task_until_done(task_id, progress_slot)
                if done:
                    time.sleep(1)
                    st.rerun()
            else:
                show_error(ex_data)

        # Show quality results if available
        ok_qr, qr_data = api_call("get", f"/api/v1/review/quality/{ds_id}")
        if ok_qr:
            qr = qr_data.get("result", {})
            if qr:
                grade = qr.get("grade", "?")
                score = qr.get("overall_score", 0)

                st.markdown(f"""
                <div style="display:flex; align-items:center; gap:16px; margin-bottom:1rem;">
                    <div class="grade-badge grade-{grade}">{grade}</div>
                    <div>
                        <div style="font-size:1.5rem; font-weight:700; color:#0f172a;">{score:.1f}<span style="font-size:0.9rem; color:#64748b;">/100</span></div>
                        <div style="font-size:0.8rem; color:#64748b;">Label Quality Score</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                dims = qr.get("dimensions", [])
                if dims:
                    dim_data = [{"Dimension": d["name"].title(), "Score": f"{d['score']:.1f}",
                                 "Weight": f"{d['weight']*100:.0f}%", "Details": d["details"]} for d in dims]
                    st.dataframe(pd.DataFrame(dim_data), width="stretch", hide_index=True)

                recs = qr.get("recommendations", [])
                if recs:
                    for rec in recs:
                        st.info(rec)

        # Show export download link if available
        ok_dl, dl_data = api_call("get", f"/api/v1/review/export/{ds_id}")
        if ok_dl:
            dl_url = dl_data.get("download_url")
            if dl_url:
                st.markdown(f"**Latest Export:** [{dl_data.get('format', 'file')}]({dl_url})")

        # Background tasks for this dataset
        st.markdown('<div class="section-title">Processing History</div>', unsafe_allow_html=True)
        ok_tasks, task_data = api_call("get", f"/api/v1/tasks/?dataset_id={ds_id}")
        if ok_tasks:
            task_list = task_data if isinstance(task_data, list) else task_data.get("tasks", [])
            if task_list:
                t_rows = []
                for t in task_list:
                    s = t.get("status", "unknown")
                    icon = {"pending": "🟡", "running": "🔵", "completed": "🟢", "failed": "🔴"}.get(s, "⚪")
                    t_rows.append({
                        "Status": f"{icon} {s}",
                        "Type": t.get("task_type", "—"),
                        "Progress": f"{t.get('progress', 0)*100:.0f}%" if t.get("progress") else "—",
                        "Message": t.get("progress_message", "—"),
                        "Created": t.get("created_at", "")[:19] if t.get("created_at") else "—",
                    })
                st.dataframe(pd.DataFrame(t_rows), width="stretch", hide_index=True)
            else:
                st.caption("No processing tasks yet.")

        # Delete dataset
        st.markdown("---")
        if st.button("Delete This Dataset", type="secondary"):
            ok_del, _ = api_call("delete", f"/api/v1/datasets/{ds_id}")
            if ok_del:
                st.session_state.selected_dataset = None
                st.success("Dataset deleted.")
                st.rerun()
            else:
                show_error(_)
    else:
        show_error(detail)
