import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import "../index.css";
// Shared component styles (.card, .table, .btn, .badge, .page-header, …).
// Without this the SuperAdmin pages render completely unstyled.
import "../layouts/DashboardLayout.css";
// SuperAdmin-specific styles incl. the login screen — imported here so the
// login is styled even before SuperAdminLayout mounts.
import "./superadmin.css";
import "../pages/admin/Admin.css";
import "../pages/audit/AuditLogs.css";
import "../stencil-components/index";
import SuperAdminApp from "./SuperAdminApp";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <SuperAdminApp />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>
);
