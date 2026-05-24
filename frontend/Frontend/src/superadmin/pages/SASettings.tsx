import { useAuth } from "../../context/AuthContext";

export default function SASettings() {
  const { user } = useAuth();

  return (
    <div>
      <div className="page-header">
        <h1>System Settings</h1>
        <p>Orchestraty configuration and system information</p>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header"><h3>SuperAdmin Account</h3></div>
        <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: "12px 20px", padding: "4px 0" }}>
          <span style={{ fontWeight: 600, color: "var(--dash-text-muted)", fontSize: 13 }}>Email</span>
          <span style={{ color: "var(--dash-text)", fontSize: 14 }}>{user?.email}</span>
          <span style={{ fontWeight: 600, color: "var(--dash-text-muted)", fontSize: 13 }}>Name</span>
          <span style={{ color: "var(--dash-text)", fontSize: 14 }}>{user?.full_name || "—"}</span>
          <span style={{ fontWeight: 600, color: "var(--dash-text-muted)", fontSize: 13 }}>Role</span>
          <span className="sa-role-badge sa-role-badge--superadmin">{user?.role}</span>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header"><h3>Role Hierarchy</h3></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "4px 0" }}>
          {[
            { role: "SuperAdmin", desc: "Full system control. Can promote/demote admins, delete users, reset passwords. Accessed via :5174.", color: "#7c3aed" },
            { role: "Admin", desc: "User management (viewer/annotator/reviewer only). Access to admin tab in main app. Cannot modify superadmin or other admins.", color: "#6366f1" },
            { role: "Reviewer", desc: "Can review labeled data, approve/reject annotations, and export datasets.", color: "#059669" },
            { role: "Annotator", desc: "Can annotate data, create labels, and work on datasets assigned to them.", color: "#d97706" },
            { role: "Viewer", desc: "Read-only access. Default role for new users. Can view datasets and dashboards.", color: "#6b7280" },
          ].map((r) => (
            <div key={r.role} style={{
              display: "flex", gap: 16, alignItems: "flex-start", padding: "14px 16px",
              background: "var(--dash-surface-hover)", borderRadius: 10
            }}>
              <div style={{
                width: 10, height: 10, borderRadius: "50%", background: r.color,
                marginTop: 5, flexShrink: 0
              }} />
              <div>
                <div style={{ fontWeight: 700, color: r.color, fontSize: 14 }}>{r.role}</div>
                <div style={{ fontSize: 13, color: "var(--dash-text-secondary)", marginTop: 2 }}>{r.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>Service Endpoints</h3></div>
        <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: "10px 20px", padding: "4px 0" }}>
          {[
            ["Main App", "http://localhost:5173"],
            ["SuperAdmin Panel", "http://localhost:5174"],
            ["API Server", "http://localhost:8000"],
            ["API Docs", "http://localhost:8000/docs"],
            ["MinIO Console", "http://localhost:9001"],
          ].map(([label, url]) => (
            <>
              <span key={label} style={{ fontWeight: 600, color: "var(--dash-text-muted)", fontSize: 13 }}>{label}</span>
              <a key={url} href={url} target="_blank" rel="noreferrer"
                style={{ color: "var(--dash-primary)", fontSize: 14, textDecoration: "none" }}>
                {url} ↗
              </a>
            </>
          ))}
        </div>
      </div>
    </div>
  );
}
