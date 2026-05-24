import { type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import OrchestrateIcon from "../components/Logo/AntigravityLogo";
import "./superadmin.css";

const navItems = [
  { to: "/", icon: "📊", label: "Dashboard", end: true },
  { to: "/users", icon: "👥", label: "User Management" },
  { to: "/audit", icon: "📋", label: "Audit Logs" },
  { to: "/settings", icon: "⚙️", label: "System Settings" },
];

export default function SuperAdminLayout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="sa-layout">
      {/* ── Sidebar ── */}
      <aside className="sa-sidebar">
        <div className="sa-sidebar__brand">
          <OrchestrateIcon size={32} />
          <div>
            <div className="sa-sidebar__title">Orchestraty</div>
            <div className="sa-sidebar__subtitle">SuperAdmin</div>
          </div>
        </div>

        <nav className="sa-sidebar__nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `sa-nav-item ${isActive ? "sa-nav-item--active" : ""}`}
            >
              <span className="sa-nav-item__icon">{item.icon}</span>
              <span className="sa-nav-item__label">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sa-sidebar__footer">
          <div className="sa-sidebar__user">
            <div className="sa-sidebar__avatar">SA</div>
            <div>
              <div className="sa-sidebar__user-name">{user?.full_name || "SuperAdmin"}</div>
              <div className="sa-sidebar__user-email">{user?.email}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="btn btn--sm btn--secondary" style={{ flex: 1 }}
              onClick={() => window.open("http://localhost:5173/app", "_blank")}>
              Main App ↗
            </button>
            <button className="btn btn--sm btn--danger" style={{ flex: 1 }} onClick={logout}>
              Logout
            </button>
          </div>
        </div>
      </aside>

      {/* ── Content ── */}
      <main className="sa-content">
        <div className="sa-content__inner">
          {children}
        </div>
      </main>
    </div>
  );
}
