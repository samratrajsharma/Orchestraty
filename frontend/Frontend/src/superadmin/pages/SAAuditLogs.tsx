import { useState, useEffect } from "react";
import { auditApi } from "../../api/audit";

interface AuditEntry {
  id: string;
  user_id: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  details?: Record<string, unknown>;
  created_at: string;
}

export default function SAAuditLogs() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState("");

  useEffect(() => {
    setLoading(true);
    auditApi.list({ action: actionFilter || undefined, limit: 100 })
      .then((r) => setLogs(Array.isArray(r.data) ? r.data : (r.data?.items || [])))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [actionFilter]);

  return (
    <div>
      <div className="page-header">
        <h1>Audit Logs</h1>
        <p>Track all system actions — role changes, deletions, password resets, and more</p>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Recent Activity ({logs.length})</h3>
          <div style={{ display: "flex", gap: 8 }}>
            <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}
              style={{ padding: "6px 12px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 8, color: "var(--dash-text)", fontSize: 13 }}>
              <option value="">All actions</option>
              <option value="admin.role_change">Role Changes</option>
              <option value="admin.delete_user">User Deletions</option>
              <option value="admin.reset_password">Password Resets</option>
              <option value="admin.activate_user">User Activations</option>
              <option value="admin.deactivate_user">User Deactivations</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="empty-state"><orc-spinner size="lg" /><h3>Loading logs...</h3></div>
        ) : logs.length === 0 ? (
          <div className="empty-state"><h3>No audit logs found</h3><p>Actions will appear here as they occur</p></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Time</th><th>Action</th><th>Resource</th><th>Details</th></tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                    <td>
                      <span className={`badge ${
                        log.action.includes("delete") ? "badge--danger" :
                        log.action.includes("role") ? "badge--warning" :
                        log.action.includes("reset") ? "badge--info" : "badge--neutral"
                      }`}>
                        {log.action.replace("admin.", "")}
                      </span>
                    </td>
                    <td>{log.resource_type} {log.resource_id ? `#${log.resource_id.slice(0, 8)}` : ""}</td>
                    <td style={{ fontSize: 12, color: "var(--dash-text-secondary)", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {log.details ? JSON.stringify(log.details) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
