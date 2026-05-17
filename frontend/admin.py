"""
Orchestraty — Admin Control Panel
System administration, user management, and monitoring.

Run:  streamlit run frontend/admin.py --server.port 8502
"""

import streamlit as st
import requests
import json
import pandas as pd
from datetime import datetime
from pathlib import Path

API_BASE = "http://localhost:8000"
_ADMIN_TOKEN_FILE = Path.home() / ".orchestraty_admin_session"


def _save_admin_token(token: str):
    try:
        _ADMIN_TOKEN_FILE.write_text(token, encoding="utf-8")
    except Exception:
        pass


def _load_admin_token() -> str | None:
    try:
        if _ADMIN_TOKEN_FILE.exists():
            token = _ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
            return token if token else None
    except Exception:
        pass
    return None


def _clear_admin_token():
    try:
        if _ADMIN_TOKEN_FILE.exists():
            _ADMIN_TOKEN_FILE.unlink()
    except Exception:
        pass

st.set_page_config(
    page_title="Orchestraty Admin",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    .stApp { font-family: 'Inter', sans-serif; }

    /* Sidebar */
    div[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    }
    div[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    div[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.1) !important; }

    /* Header banner */
    .admin-banner {
        background: linear-gradient(135deg, #0f172a 0%, #dc2626 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 14px;
        margin-bottom: 1.5rem;
    }
    .admin-banner h1 { margin: 0; font-size: 1.6rem; font-weight: 800; letter-spacing: -0.02em; }
    .admin-banner p { margin: 0.3rem 0 0 0; opacity: 0.8; font-size: 0.9rem; }

    /* Stat cards */
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

    /* Section titles */
    .sec-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #0f172a;
        border-left: 4px solid #dc2626;
        padding-left: 12px;
        margin: 1.5rem 0 1rem 0;
    }

    /* Role badges */
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

    /* Clean tables */
    .stDataFrame { border-radius: 8px; overflow: hidden; }

    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── Session State ──
if "admin_token" not in st.session_state:
    st.session_state.admin_token = None
if "admin_user" not in st.session_state:
    st.session_state.admin_user = None
if "_admin_restored" not in st.session_state:
    st.session_state._admin_restored = False

# Restore admin session from persisted token
if not st.session_state._admin_restored:
    st.session_state._admin_restored = True
    saved = _load_admin_token()
    if saved and not st.session_state.admin_token:
        try:
            resp = requests.get(
                f"{API_BASE}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {saved}"},
                timeout=5,
            )
            if resp.status_code == 200:
                user = resp.json()
                if user.get("role") == "admin":
                    st.session_state.admin_token = saved
                    st.session_state.admin_user = user
                else:
                    _clear_admin_token()
            else:
                _clear_admin_token()
        except Exception:
            _clear_admin_token()


def auth_headers():
    if st.session_state.admin_token:
        return {"Authorization": f"Bearer {st.session_state.admin_token}"}
    return {}


def api(method, endpoint, **kwargs):
    url = f"{API_BASE}{endpoint}"
    headers = kwargs.pop("headers", {})
    headers.update(auth_headers())
    try:
        resp = getattr(requests, method)(url, headers=headers, timeout=10, **kwargs)
        if resp.status_code < 300:
            return True, resp.json() if resp.status_code != 204 else None
        try:
            return False, resp.json()
        except Exception:
            return False, {"detail": resp.text}
    except requests.ConnectionError:
        return False, {"detail": "API unreachable at localhost:8000"}
    except Exception as e:
        return False, {"detail": str(e)}


def role_pill(role):
    cls = f"pill-{role}" if role in ("admin","reviewer","annotator","viewer") else "pill-viewer"
    return f'<span class="role-pill {cls}">{role}</span>'


# ════════════════════════════════════════════════
#  SIDEBAR — LOGIN + NAV
# ════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### ⚙️ Admin Panel")
    st.caption("Orchestraty System Administration")
    st.markdown("---")

    if st.session_state.admin_token and st.session_state.admin_user:
        u = st.session_state.admin_user
        st.markdown(f"**{u.get('full_name', 'Admin')}**")
        st.caption(f"{u.get('email', '')} · admin")
        st.markdown("---")

        page = st.radio("", [
            "Overview",
            "User Management",
            "Datasets",
            "Task Monitor",
            "Audit Trail",
            "System Health",
        ], label_visibility="collapsed")

        st.markdown("---")
        if st.button("Sign Out", use_container_width=True):
            st.session_state.admin_token = None
            st.session_state.admin_user = None
            _clear_admin_token()
            st.rerun()
    else:
        page = None
        st.markdown("**Admin login required**")
        st.caption("Only admin accounts can access this panel.")
        with st.form("admin_login"):
            email = st.text_input("Email", placeholder="admin@orchestraty.local")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign In", use_container_width=True):
                if email and password:
                    ok, data = api("post", "/api/v1/auth/login",
                                   data={"username": email, "password": password})
                    if ok:
                        st.session_state.admin_token = data["access_token"]
                        ok2, user = api("get", "/api/v1/auth/me")
                        if ok2:
                            if user.get("role") != "admin":
                                st.error("Access denied — admin role required.")
                                st.session_state.admin_token = None
                            else:
                                st.session_state.admin_user = user
                                _save_admin_token(data["access_token"])
                                st.rerun()
                    else:
                        detail = data.get("detail", "Login failed") if isinstance(data, dict) else str(data)
                        st.error(detail)


# ── Login gate ──
if not st.session_state.admin_token:
    st.markdown("""
    <div class="admin-banner">
        <h1>⚙️ Orchestraty Admin Panel</h1>
        <p>System administration and user management</p>
    </div>
    """, unsafe_allow_html=True)
    st.info("Sign in with an admin account using the sidebar. Default credentials are in `.env` (ADMIN_EMAIL / ADMIN_PASSWORD).")
    st.stop()


# ════════════════════════════════════════════════
#  OVERVIEW
# ════════════════════════════════════════════════
if page == "Overview":
    st.markdown("""
    <div class="admin-banner">
        <h1>📊 System Overview</h1>
        <p>Key metrics and platform health at a glance</p>
    </div>
    """, unsafe_allow_html=True)

    ok, stats = api("get", "/api/v1/admin/stats")
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
        st.error("Could not load stats. Is the API running?")

    # Recent audit
    st.markdown('<div class="sec-title">Recent Activity</div>', unsafe_allow_html=True)
    ok, logs = api("get", "/api/v1/audit-logs/?limit=8")
    if ok:
        log_list = logs if isinstance(logs, list) else logs.get("logs", logs.get("items", []))
        if log_list:
            rows = [{
                "Time": l.get("timestamp", "")[:19],
                "Action": l.get("action", ""),
                "Resource": l.get("resource_type", ""),
                "Details": json.dumps(l.get("details", {}))[:80],
            } for l in log_list]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No recent activity.")


# ════════════════════════════════════════════════
#  USER MANAGEMENT
# ════════════════════════════════════════════════
elif page == "User Management":
    st.markdown("""
    <div class="admin-banner">
        <h1>👥 User Management</h1>
        <p>Manage accounts, roles, and access</p>
    </div>
    """, unsafe_allow_html=True)

    # Fetch all users via admin API
    ok, users = api("get", "/api/v1/admin/users")

    if ok and users:
        # Summary
        role_counts = {}
        for u in users:
            role_counts[u["role"]] = role_counts.get(u["role"], 0) + 1
        active_count = sum(1 for u in users if u["is_active"])

        pills_html = " &nbsp; ".join(
            f'{role_pill(r)} <strong>{c}</strong>' for r, c in sorted(role_counts.items())
        )
        st.markdown(
            f'<div style="margin-bottom:1rem;">{pills_html} &nbsp;&nbsp; '
            f'<span style="color:#64748b;">({active_count} active, {len(users) - active_count} inactive)</span></div>',
            unsafe_allow_html=True,
        )

        # User table with actions
        st.markdown('<div class="sec-title">All Users</div>', unsafe_allow_html=True)

        for u in users:
            with st.expander(
                f"{'🟢' if u['is_active'] else '🔴'} **{u.get('full_name') or u['email']}** — "
                f"`{u['email']}` · `{u['role']}`"
            ):
                col_info, col_role, col_status = st.columns([2, 1, 1])

                with col_info:
                    st.markdown(f"**ID:** `{u['id']}`")
                    st.markdown(f"**Joined:** {u.get('created_at', 'N/A')[:19]}")
                    st.markdown(f"**Active:** {'Yes' if u['is_active'] else 'No'}")

                with col_role:
                    st.markdown("**Change Role**")
                    new_role = st.selectbox(
                        "Role",
                        ["admin", "reviewer", "annotator", "viewer"],
                        index=["admin", "reviewer", "annotator", "viewer"].index(u["role"]),
                        key=f"role_{u['id']}",
                        label_visibility="collapsed",
                    )
                    if st.button("Apply Role", key=f"apply_role_{u['id']}", use_container_width=True):
                        ok2, res = api("put", f"/api/v1/admin/users/{u['id']}/role",
                                       json={"role": new_role})
                        if ok2:
                            st.success(res.get("message", "Role updated"))
                            st.rerun()
                        else:
                            st.error(res.get("detail", "Failed") if isinstance(res, dict) else str(res))

                with col_status:
                    st.markdown("**Account Status**")
                    if u["is_active"]:
                        if st.button("Deactivate", key=f"deact_{u['id']}", use_container_width=True):
                            ok2, res = api("put", f"/api/v1/admin/users/{u['id']}/status",
                                           json={"is_active": False})
                            if ok2:
                                st.success("User deactivated")
                                st.rerun()
                            else:
                                st.error(res.get("detail", "Failed") if isinstance(res, dict) else str(res))
                    else:
                        if st.button("Activate", key=f"act_{u['id']}", type="primary", use_container_width=True):
                            ok2, res = api("put", f"/api/v1/admin/users/{u['id']}/status",
                                           json={"is_active": True})
                            if ok2:
                                st.success("User activated")
                                st.rerun()
                            else:
                                st.error(res.get("detail", "Failed") if isinstance(res, dict) else str(res))

                    st.markdown("---")
                    if st.button("Delete User", key=f"del_{u['id']}", type="secondary"):
                        ok3, res = api("delete", f"/api/v1/admin/users/{u['id']}")
                        if ok3:
                            st.success(res.get("message", "User deleted"))
                            st.rerun()
                        else:
                            st.error(res.get("detail", "Failed") if isinstance(res, dict) else str(res))

    elif ok:
        st.info("No users found.")
    else:
        st.error("Failed to load users.")

    # Register new user (admin can set role)
    st.markdown('<div class="sec-title">Create New User</div>', unsafe_allow_html=True)
    with st.form("create_user"):
        cu1, cu2 = st.columns(2)
        new_email = cu1.text_input("Email", placeholder="user@company.com")
        new_name = cu2.text_input("Full Name", placeholder="John Doe")
        cp1, cp2 = st.columns(2)
        new_pass = cp1.text_input("Password", type="password")
        new_role_create = cp2.selectbox("Role", ["viewer", "annotator", "reviewer", "admin"])

        if st.form_submit_button("Create User", use_container_width=True):
            if new_email and new_name and new_pass:
                ok, data = api("post", "/api/v1/auth/register", json={
                    "email": new_email,
                    "password": new_pass,
                    "full_name": new_name,
                    "role": new_role_create,
                })
                if ok:
                    st.success(f"User '{new_email}' created as {new_role_create}!")
                    st.rerun()
                else:
                    detail = data.get("detail", "Failed") if isinstance(data, dict) else str(data)
                    st.error(detail)


# ════════════════════════════════════════════════
#  DATASETS
# ════════════════════════════════════════════════
elif page == "Datasets":
    st.markdown("""
    <div class="admin-banner">
        <h1>📊 Dataset Overview</h1>
        <p>All datasets across the platform</p>
    </div>
    """, unsafe_allow_html=True)

    ok, ds_data = api("get", "/api/v1/datasets/")
    if ok:
        datasets = ds_data.get("datasets", [])
        if datasets:
            status_counts = {}
            for d in datasets:
                s = d.get("status", "raw")
                status_counts[s] = status_counts.get(s, 0) + 1

            # Status summary
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
            } for d in datasets]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No datasets created yet.")
    else:
        st.error("Failed to load datasets.")


# ════════════════════════════════════════════════
#  TASK MONITOR
# ════════════════════════════════════════════════
elif page == "Task Monitor":
    st.markdown("""
    <div class="admin-banner">
        <h1>⏳ Task Monitor</h1>
        <p>Background processing jobs across all pipelines</p>
    </div>
    """, unsafe_allow_html=True)

    fc1, fc2 = st.columns(2)
    type_f = fc1.selectbox("Filter by type", ["All", "structuring", "eda", "labeling"])
    status_f = fc2.selectbox("Filter by status", ["All", "pending", "running", "completed", "failed"])

    endpoint = "/api/v1/tasks/"
    params = []
    if type_f != "All":
        params.append(f"task_type={type_f}")
    if status_f != "All":
        params.append(f"status={status_f}")
    if params:
        endpoint += "?" + "&".join(params)

    ok, tasks = api("get", endpoint)
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
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(task_list)} tasks")
        else:
            st.info("No tasks match the filter.")
    else:
        st.error("Failed to load tasks.")


# ════════════════════════════════════════════════
#  AUDIT TRAIL
# ════════════════════════════════════════════════
elif page == "Audit Trail":
    st.markdown("""
    <div class="admin-banner">
        <h1>📋 Audit Trail</h1>
        <p>Complete activity log across the platform</p>
    </div>
    """, unsafe_allow_html=True)

    limit = st.slider("Entries to show", 10, 200, 50)
    ok, data = api("get", f"/api/v1/audit-logs/?limit={limit}")
    if ok:
        logs = data if isinstance(data, list) else data.get("logs", data.get("items", []))
        if logs:
            rows = [{
                "Timestamp": l.get("timestamp", "")[:19],
                "Action": l.get("action", ""),
                "Resource": l.get("resource_type", ""),
                "Resource ID": str(l.get("resource_id", ""))[:12] + "..." if l.get("resource_id") else "—",
                "Details": json.dumps(l.get("details", {}), default=str)[:100],
            } for l in logs]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(logs)} entries")
        else:
            st.info("No logs yet.")


# ════════════════════════════════════════════════
#  SYSTEM HEALTH
# ════════════════════════════════════════════════
elif page == "System Health":
    st.markdown("""
    <div class="admin-banner">
        <h1>💚 System Health</h1>
        <p>Infrastructure and service status</p>
    </div>
    """, unsafe_allow_html=True)

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
    ok, health = api("get", "/health")
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
