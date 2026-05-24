import { useState, useEffect } from "react";
import { adminApi } from "../../api/admin";
import { useAuth } from "../../context/AuthContext";

interface Stats {
  total_users: number;
  active_users: number;
  superadmin_count: number;
  admin_count: number;
  total_datasets: number;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
}

export default function SADashboard() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi.getStats()
      .then((r) => setStats(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>SuperAdmin Dashboard</h1>
        <p>System overview and health — logged in as <strong>{user?.email}</strong></p>
      </div>

      {loading ? (
        <div className="empty-state"><orc-spinner size="lg" /><h3>Loading stats...</h3></div>
      ) : stats ? (
        <>
          <div className="sa-stats">
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Total Users</div>
              <div className="sa-stat-card__value sa-stat-card__value--primary">{stats.total_users}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Active Users</div>
              <div className="sa-stat-card__value sa-stat-card__value--success">{stats.active_users}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Admins</div>
              <div className="sa-stat-card__value sa-stat-card__value--warning">{stats.admin_count}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Datasets</div>
              <div className="sa-stat-card__value">{stats.total_datasets}</div>
            </div>
          </div>

          <div className="sa-stats">
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Total Tasks</div>
              <div className="sa-stat-card__value">{stats.total_tasks}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Completed Tasks</div>
              <div className="sa-stat-card__value sa-stat-card__value--success">{stats.completed_tasks}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">Failed Tasks</div>
              <div className="sa-stat-card__value sa-stat-card__value--danger">{stats.failed_tasks}</div>
            </div>
            <div className="sa-stat-card">
              <div className="sa-stat-card__label">SuperAdmins</div>
              <div className="sa-stat-card__value" style={{color: "#7c3aed"}}>{stats.superadmin_count}</div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>Quick Actions</h3></div>
            <div style={{ display: "flex", gap: 12, padding: "4px 0" }}>
              <a href="/users" className="btn btn--primary">Manage Users</a>
              <a href="/audit" className="btn btn--secondary">View Audit Logs</a>
              <a href="/settings" className="btn btn--secondary">System Settings</a>
              <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer" className="btn btn--secondary">API Docs ↗</a>
            </div>
          </div>
        </>
      ) : (
        <div className="empty-state"><h3>Failed to load stats</h3><p>Is the backend running?</p></div>
      )}
    </div>
  );
}
