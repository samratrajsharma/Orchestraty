import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import SuperAdminLayout from "./SuperAdminLayout";
import SALogin from "./pages/SALogin";
import SADashboard from "./pages/SADashboard";
import SAUsers from "./pages/SAUsers";
import SAAuditLogs from "./pages/SAAuditLogs";
import SASettings from "./pages/SASettings";

export default function SuperAdminApp() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", background: "var(--dash-bg)" }}>
        <div style={{ textAlign: "center" }}>
          <orc-spinner size="lg" />
          <p style={{ marginTop: 16, color: "var(--dash-text-muted)" }}>Loading SuperAdmin Panel...</p>
        </div>
      </div>
    );
  }

  // Not logged in → show login
  if (!user) {
    return <SALogin />;
  }

  // Logged in but NOT superadmin → access denied
  if (user.role !== "superadmin") {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", background: "var(--dash-bg)" }}>
        <div style={{ textAlign: "center", maxWidth: 420, padding: 40 }}>
          <div style={{ fontSize: 64, marginBottom: 16 }}>🔒</div>
          <h2 style={{ color: "var(--dash-text)", marginBottom: 8 }}>Access Denied</h2>
          <p style={{ color: "var(--dash-text-muted)", marginBottom: 24 }}>
            This panel is restricted to the SuperAdmin account only.
            You are logged in as <strong>{user.email}</strong> with role <strong>{user.role}</strong>.
          </p>
          <a href="http://localhost:5173/app" style={{
            display: "inline-block", padding: "10px 24px", background: "var(--brand-gradient, linear-gradient(135deg, #C77DFF, #8B5CF6, #5B5FE3))",
            color: "#fff", borderRadius: 10, textDecoration: "none", fontWeight: 600
          }}>Go to Main App</a>
        </div>
      </div>
    );
  }

  // SuperAdmin → full panel
  return (
    <SuperAdminLayout>
      <Routes>
        <Route path="/" element={<SADashboard />} />
        <Route path="/users" element={<SAUsers />} />
        <Route path="/audit" element={<SAAuditLogs />} />
        <Route path="/settings" element={<SASettings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </SuperAdminLayout>
  );
}
