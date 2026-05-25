import { useState } from "react";
import { NavLink, Link, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import OrchestrateIcon from "../components/Logo/AntigravityLogo";
import "./DashboardLayout.css";

// Minimum role required to see each nav item
// "viewer" = everyone, "annotator" = annotator+, "reviewer" = reviewer+, "admin" = admin+superadmin
const NAV_ITEMS = [
  { to: "/app", icon: "grid", label: "Dashboard", end: true, minRole: "viewer" },
  { to: "/app/datasets", icon: "database", label: "Datasets", minRole: "viewer" },
  { to: "/app/structuring", icon: "layers", label: "Structuring", minRole: "annotator" },
  { to: "/app/eda", icon: "bar-chart", label: "EDA", minRole: "annotator" },
  { to: "/app/labeling", icon: "tag", label: "Labeling", minRole: "annotator" },
  { to: "/app/ai-labeling", icon: "cpu", label: "AI Labeling", minRole: "annotator" },
  { to: "/app/images", icon: "image", label: "Images", minRole: "annotator" },
  { to: "/app/review", icon: "check-circle", label: "Review & Export", minRole: "reviewer" },
  { to: "/app/workflows", icon: "git-branch", label: "Workflows", minRole: "admin" },
  { to: "/app/llm", icon: "zap", label: "LLM Config", minRole: "admin" },
  { to: "/app/tasks", icon: "activity", label: "Tasks", minRole: "reviewer" },
  { to: "/app/admin", icon: "shield", label: "Admin", minRole: "admin" },
  { to: "/app/audit", icon: "file-text", label: "Audit Logs", minRole: "admin" },
];

const ROLE_LEVEL: Record<string, number> = {
  superadmin: 5, admin: 4, reviewer: 3, annotator: 2, viewer: 1,
};

function hasMinRole(userRole: string, minRole: string): boolean {
  return (ROLE_LEVEL[userRole] || 0) >= (ROLE_LEVEL[minRole] || 0);
}

const ICONS: Record<string, string> = {
  "grid": "M3 3h7v7H3V3zm11 0h7v7h-7V3zm0 11h7v7h-7v-7zM3 14h7v7H3v-7z",
  "database": "M12 2C6.48 2 2 4 2 6.5v11C2 20 6.48 22 12 22s10-2 10-4.5v-11C22 4 17.52 2 12 2zM2 9.5c0 2.5 4.48 4.5 10 4.5s10-2 10-4.5",
  "layers": "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  "bar-chart": "M18 20V10M12 20V4M6 20v-6",
  "tag": "M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82zM7 7h.01",
  "cpu": "M18 4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2zM9 9h6v6H9V9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3",
  "image": "M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21",
  "check-circle": "M22 11.08V12a10 10 0 11-5.93-9.14M22 4L12 14.01l-3-3",
  "git-branch": "M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zM6 21a3 3 0 100-6 3 3 0 000 6zM18 9a9 9 0 01-9 9",
  "zap": "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  "activity": "M22 12h-4l-3 9L9 3l-3 9H2",
  "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  "file-text": "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6M16 13H8M16 17H8M10 9H8",
};

export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);

  const handleLogout = () => { logout(); navigate("/app/login"); };

  return (
    <div className={`dash ${collapsed ? "dash--collapsed" : ""}`}>
      <aside className="dash__sidebar">
        <div className="dash__sidebar-header">
          <Link to="/" className="dash__logo" title="Go to Orchestraty home">
            <OrchestrateIcon size={28} />
            {!collapsed && <span className="dash__brand">Orchestraty</span>}
          </Link>
          <button className="dash__toggle" onClick={() => setCollapsed(!collapsed)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {collapsed ? <path d="M9 18l6-6-6-6"/> : <path d="M15 18l-6-6 6-6"/>}
            </svg>
          </button>
        </div>

        <nav className="dash__nav">
          {NAV_ITEMS
            .filter((item) => hasMinRole(user?.role || "viewer", item.minRole))
            .map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => `dash__nav-item ${isActive ? "dash__nav-item--active" : ""}`}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d={ICONS[item.icon]} />
              </svg>
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="dash__sidebar-footer">
          <div className="dash__user-pill">
            <div className="dash__avatar">{user?.full_name?.[0] || user?.email?.[0] || "U"}</div>
            {!collapsed && (
              <div className="dash__user-info">
                <span className="dash__user-name">{user?.full_name || user?.email}</span>
                <span className="dash__user-role">{user?.role}</span>
              </div>
            )}
          </div>
          <button className="dash__logout" onClick={handleLogout} title="Sign out">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/>
            </svg>
          </button>
        </div>
      </aside>

      <main className="dash__main">
        <Outlet />
      </main>
    </div>
  );
}
