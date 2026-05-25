import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { adminApi } from "../../api/admin";
import { taskApi } from "../../api/tasks";
import { useAuth } from "../../context/AuthContext";
import "./Dashboard.css";

const QUICK_ACTIONS = [
  {
    to: "/app/datasets", label: "New Dataset", desc: "Ingest a CSV or JSON file",
    color: "indigo", icon: "M12 5v14M5 12h14",
  },
  {
    to: "/app/workflows", label: "Run Workflow", desc: "Orchestrate a pipeline",
    color: "cyan", icon: "M8 5v14l11-7z",
  },
  {
    to: "/app/llm", label: "Configure LLM", desc: "Connect model providers",
    color: "amber", icon: "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  },
  {
    to: "/app/images", label: "Image Pipeline", desc: "Upload & embed images",
    color: "emerald", icon: "M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21",
  },
];

interface Stats {
  total_users: number; active_users: number; total_datasets: number;
  total_tasks: number; completed_tasks: number; failed_tasks: number;
}

export default function Dashboard() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentTasks, setRecentTasks] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    if (user?.role === "admin" || user?.role === "superadmin") {
      adminApi.getStats().then((r) => setStats(r.data)).catch(() => {});
    }
    taskApi.list({ limit: 8 }).then((r) => setRecentTasks(r.data.tasks || [])).catch(() => {});
  }, [user?.role]);

  const statusColor = (s: string) => {
    if (s === "completed") return "badge--success";
    if (s === "failed") return "badge--danger";
    if (s === "running" || s === "started") return "badge--warning";
    return "badge--neutral";
  };

  return (
    <div>
      <div className="page-header">
        <h1>Welcome back, {user?.full_name?.split(" ")[0] || "there"}</h1>
        <p>Here\u2019s what\u2019s happening across your workspace</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-card__label">Datasets</div>
          <div className="stat-card__value" style={{color: "var(--dash-primary)"}}>{stats?.total_datasets ?? "\u2014"}</div>
          <div className="stat-card__sub">Total datasets</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__label">Tasks Completed</div>
          <div className="stat-card__value" style={{color: "var(--dash-success)"}}>{stats?.completed_tasks ?? "\u2014"}</div>
          <div className="stat-card__sub">of {stats?.total_tasks ?? 0} total</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__label">Failed Tasks</div>
          <div className="stat-card__value" style={{color: "var(--dash-danger)"}}>{stats?.failed_tasks ?? "\u2014"}</div>
          <div className="stat-card__sub">Need attention</div>
        </div>
        <div className="stat-card">
          <div className="stat-card__label">Active Users</div>
          <div className="stat-card__value" style={{color: "var(--dash-accent)"}}>{stats?.active_users ?? "\u2014"}</div>
          <div className="stat-card__sub">of {stats?.total_users ?? 0} total</div>
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <div className="card-header">
            <h3>Quick Actions</h3>
          </div>
          <div className="qa-grid">
            {QUICK_ACTIONS.map((a) => (
              <Link key={a.to} to={a.to} className="qa-tile">
                <span className={`qa-tile__icon qa-tile__icon--${a.color}`}>
                  <svg width="19" height="19" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d={a.icon} />
                  </svg>
                </span>
                <span className="qa-tile__text">
                  <span className="qa-tile__label">{a.label}</span>
                  <span className="qa-tile__desc">{a.desc}</span>
                </span>
              </Link>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h3>Recent Tasks</h3>
            <Link to="/app/tasks" className="btn btn--sm btn--secondary">View all</Link>
          </div>
          {recentTasks.length === 0 ? (
            <div className="empty-state"><h3>No tasks yet</h3></div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Type</th><th>Status</th><th>Created</th></tr></thead>
                <tbody>
                  {recentTasks.map((t) => (
                    <tr key={String(t.id)}>
                      <td style={{color: "var(--dash-text)"}}>{String(t.task_type)}</td>
                      <td><span className={`badge ${statusColor(String(t.status))}`}>
                        <span className={`badge-dot badge-dot--${String(t.status) === "completed" ? "success" : String(t.status) === "failed" ? "danger" : "warning"}`} />
                        {String(t.status)}
                      </span></td>
                      <td>{new Date(String(t.created_at)).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
