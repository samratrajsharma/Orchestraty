import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { adminApi } from "../../api/admin";
import { taskApi } from "../../api/tasks";
import { useAuth } from "../../context/AuthContext";

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
          <div style={{display: "flex", flexDirection: "column", gap: "8px"}}>
            <Link to="/app/datasets" className="btn btn--secondary" style={{justifyContent: "flex-start"}}>
              <span>+</span> New Dataset
            </Link>
            <Link to="/app/workflows" className="btn btn--secondary" style={{justifyContent: "flex-start"}}>
              <span>&#x25B6;</span> Run Workflow
            </Link>
            <Link to="/app/llm" className="btn btn--secondary" style={{justifyContent: "flex-start"}}>
              <span>&#x26A1;</span> Configure LLM
            </Link>
            <Link to="/app/images" className="btn btn--secondary" style={{justifyContent: "flex-start"}}>
              <span>&#x1F5BC;</span> Image Pipeline
            </Link>
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
