import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import App from "./App";
import DashboardLayout from "./layouts/DashboardLayout";
import Login from "./pages/auth/Login";
import Register from "./pages/auth/Register";
import Dashboard from "./pages/dashboard/Dashboard";
import Datasets from "./pages/datasets/Datasets";
import DatasetDetail from "./pages/datasets/DatasetDetail";
import Structuring from "./pages/structuring/Structuring";
import EDA from "./pages/eda/EDA";
import Labeling from "./pages/labeling/Labeling";
import AILabeling from "./pages/ai-labeling/AILabeling";
import ImagePipeline from "./pages/images/ImagePipeline";
import ReviewPage from "./pages/review/ReviewPage";
import Workflows from "./pages/workflows/Workflows";
import WorkflowDetail from "./pages/workflows/WorkflowDetail";
import LLMConfig from "./pages/llm/LLMConfig";
import Admin from "./pages/admin/Admin";
import Tasks from "./pages/tasks/Tasks";
import AuditLogs from "./pages/audit/AuditLogs";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuth();
  if (loading) return <div className="dash-loading">Loading...</div>;
  if (!token) return <Navigate to="/app/login" replace />;
  return <>{children}</>;
}

export default function AppRouter() {
  return (
    <Routes>
      {/* Landing page */}
      <Route path="/" element={<App />} />

      {/* Auth */}
      <Route path="/app/login" element={<Login />} />
      <Route path="/app/register" element={<Register />} />

      {/* Dashboard app */}
      <Route path="/app" element={<ProtectedRoute><DashboardLayout /></ProtectedRoute>}>
        <Route index element={<Dashboard />} />
        <Route path="datasets" element={<Datasets />} />
        <Route path="datasets/:id" element={<DatasetDetail />} />
        <Route path="structuring" element={<Structuring />} />
        <Route path="eda" element={<EDA />} />
        <Route path="labeling" element={<Labeling />} />
        <Route path="ai-labeling" element={<AILabeling />} />
        <Route path="images" element={<ImagePipeline />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="workflows" element={<Workflows />} />
        <Route path="workflows/:id" element={<WorkflowDetail />} />
        <Route path="llm" element={<LLMConfig />} />
        <Route path="admin" element={<Admin />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="audit" element={<AuditLogs />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
