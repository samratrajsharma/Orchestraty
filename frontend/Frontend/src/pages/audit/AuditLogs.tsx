import { useState, useEffect, useCallback } from "react";
import { auditApi } from "../../api/audit";

interface AuditLog {
  id: string; user_id: string; user_email?: string; action: string;
  resource_type: string; resource_id?: string; details?: string;
  created_at: string;
}

export default function AuditLogs() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    auditApi.list({
      action: actionFilter || undefined,
      resource_type: resourceFilter || undefined,
      limit: 50,
    }).then((r) => setLogs(Array.isArray(r.data) ? r.data : r.data.logs || []))
      .catch(() => {}).finally(() => setLoading(false));
  }, [actionFilter, resourceFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="page-header">
        <h1>Audit Logs</h1>
        <p>Track all system activity</p>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Activity Log</h3>
          <div style={{display:"flex", gap: 8}}>
            <input value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}
              placeholder="Filter by action"
              style={{padding: "6px 12px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 8, color: "var(--dash-text)", fontSize: 13, width: 150}} />
            <input value={resourceFilter} onChange={(e) => setResourceFilter(e.target.value)}
              placeholder="Filter by resource"
              style={{padding: "6px 12px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 8, color: "var(--dash-text)", fontSize: 13, width: 150}} />
          </div>
        </div>
        {loading ? <div className="empty-state"><h3>Loading...</h3></div> : logs.length === 0 ? (
          <div className="empty-state"><h3>No logs found</h3></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Timestamp</th><th>User</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.id}>
                    <td>{new Date(l.created_at).toLocaleString()}</td>
                    <td style={{color: "var(--dash-text)"}}>{l.user_email || l.user_id}</td>
                    <td><span className="badge badge--info">{l.action}</span></td>
                    <td>{l.resource_type}{l.resource_id ? ` / ${l.resource_id.slice(0, 8)}...` : ""}</td>
                    <td style={{maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{l.details || "\u2014"}</td>
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
