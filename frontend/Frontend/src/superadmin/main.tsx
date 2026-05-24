import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext";
import "../index.css";
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
