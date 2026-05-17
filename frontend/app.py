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
        if st.button("Logout", use_container_width=True):
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
                if st.form_submit_button("Create Account", use_container_width=True):
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
                if st.form_submit_button("Sign In", use_container_width=True):
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
        ds_source = c2.selectbox("Source Type", ["csv", "json", "api", "database"])
        ds_desc = st.text_area("Description", placeholder="What is this dataset about?", height=80)
        if st.form_submit_button("Create Dataset", type="primary", use_container_width=True):
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
tab_upload, tab_structure, tab_eda, tab_label, tab_info = st.tabs([
    "📤 Upload Data", "🔧 Structure & Clean", "📈 Analyze (EDA)", "🏷️ Label", "ℹ️ Details"
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

        if st.button("Upload & Validate", type="primary", use_container_width=True):
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
                    st.dataframe(cols_df, use_container_width=True, hide_index=True)

                if validation.get("sample_data"):
                    st.markdown('<div class="section-title">Data Preview</div>', unsafe_allow_html=True)
                    try:
                        st.dataframe(pd.DataFrame(validation["sample_data"]), use_container_width=True)
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
            st.dataframe(pd.DataFrame(v_rows), use_container_width=True, hide_index=True)


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

            submitted_struct = st.form_submit_button("Run Structuring Pipeline", type="primary", use_container_width=True)

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
                    st.dataframe(pd.DataFrame(dim_data), use_container_width=True, hide_index=True)

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
                        st.dataframe(pd.DataFrame(s_data), use_container_width=True, hide_index=True)
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

            submitted_eda = st.form_submit_button("Run EDA Pipeline", type="primary", use_container_width=True)

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
                        st.dataframe(pd.DataFrame(col_data), use_container_width=True, hide_index=True)

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
                        st.dataframe(size_df, use_container_width=True, hide_index=True)

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

            if st.form_submit_button("Add Rule", use_container_width=True):
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
            if bc1.button("Clear Rules", use_container_width=True):
                st.session_state.labeling_rules = []
                st.rerun()
            run_labeling = bc2.button("Run Labeling", type="primary", use_container_width=True)

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
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)

                # Per-rule stats
                per_rule = labeling.get("per_rule_stats", [])
                if per_rule:
                    with st.expander("Per-Rule Statistics"):
                        st.dataframe(pd.DataFrame(per_rule), use_container_width=True, hide_index=True)

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
        if sc2.button("Update", use_container_width=True):
            ok2, _ = api_call("put", f"/api/v1/datasets/{ds_id}/status", json={"status": new_status})
            if ok2:
                st.success(f"Status updated to {new_status}")
                st.rerun()

        # Metadata
        st.markdown('<div class="section-title">Metadata</div>', unsafe_allow_html=True)
        if metadata:
            meta_rows = [{"Key": m["key"], "Value": m["value"]} for m in metadata]
            st.dataframe(pd.DataFrame(meta_rows), use_container_width=True, hide_index=True)

        mc1, mc2, mc3 = st.columns([2, 2, 1])
        mk = mc1.text_input("Key", placeholder="e.g., domain", label_visibility="collapsed")
        mv = mc2.text_input("Value", placeholder="e.g., customer_support", label_visibility="collapsed")
        if mc3.button("Add", use_container_width=True):
            if mk and mv:
                ok3, _ = api_call("put", f"/api/v1/datasets/{ds_id}/metadata",
                                  json={"key": mk, "value": mv})
                if ok3:
                    st.success(f"Added `{mk}`")
                    st.rerun()

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
                st.dataframe(pd.DataFrame(t_rows), use_container_width=True, hide_index=True)
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
