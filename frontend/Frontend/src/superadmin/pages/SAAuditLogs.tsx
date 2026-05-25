import { useState, useEffect } from "react";
import { auditApi } from "../../api/audit";
import "../../pages/audit/AuditLogs.css";

interface AuditEntry {
  id: string;
  user_id: string;
  user_email?: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  details?: Record<string, unknown> | string;
  timestamp: string;
}

type Category = "create" | "update" | "delete" | "auth" | "run" | "default";

function categorize(action: string): Category {
  const a = action.toLowerCase();
  if (a.includes("delete") || a.includes("remove") || a.includes("deactivate")) return "delete";
  if (a.includes("create") || a.includes("register") || a.includes("upload") || a.includes("add")) return "create";
  if (a.includes("login") || a.includes("logout") || a.includes("auth")) return "auth";
  if (a.includes("run") || a.includes("execute") || a.includes("start")) return "run";
  if (a.includes("update") || a.includes("role") || a.includes("status") ||
      a.includes("edit") || a.includes("reset") || a.includes("activate")) return "update";
  return "default";
}

const CATEGORY_ICON: Record<Category, string> = {
  create: "M12 5v14M5 12h14",
  update: "M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4 12.5-12.5z",
  delete: "M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6",
  auth: "M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4M10 17l5-5-5-5M15 12H3",
  run: "M5 3l14 9-14 9V3z",
  default: "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
};

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function detailText(details?: Record<string, unknown> | string): string {
  if (!details) return "";
  if (typeof details === "string") return details;
  try { return JSON.stringify(details); } catch { return ""; }
}

export default function SAAuditLogs() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState("");

  useEffect(() => {
    setLoading(true);
    auditApi.list({ action: actionFilter || undefined, limit: 100 })
      .then((r) => setLogs(Array.isArray(r.data) ? r.data : (r.data?.logs || r.data?.items || [])))
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
        <div className="card-header" style={{marginBottom: 16}}>
          <h3>Recent Activity {logs.length > 0 && <span style={{color: "var(--dash-text-muted)", fontWeight: 500}}>· {logs.length}</span>}</h3>
        </div>

        <div className="al-toolbar" style={{marginBottom: 8}}>
          <select className="um-select" value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
            <option value="">All actions</option>
            <option value="admin.role_change">Role Changes</option>
            <option value="admin.delete_user">User Deletions</option>
            <option value="admin.reset_password">Password Resets</option>
            <option value="admin.activate_user">User Activations</option>
            <option value="admin.deactivate_user">User Deactivations</option>
          </select>
          {actionFilter && (
            <button className="al-clear" onClick={() => setActionFilter("")}>Clear filter</button>
          )}
        </div>

        {loading ? (
          <div className="empty-state"><h3>Loading activity...</h3></div>
        ) : logs.length === 0 ? (
          <div className="empty-state">
            <h3>No audit logs found</h3>
            <p>Actions will appear here as they occur</p>
          </div>
        ) : (
          <div>
            {logs.map((log) => {
              const cat = categorize(log.action);
              const detail = detailText(log.details);
              return (
                <div key={log.id} className="al-event">
                  <div className={`al-icon al-icon--${cat}`}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d={CATEGORY_ICON[cat]} />
                    </svg>
                  </div>
                  <div className="al-body">
                    <div className="al-line1">
                      <span className="al-action">{log.action.replace("admin.", "")}</span>
                      {log.resource_type && <span className="al-resource">{log.resource_type}</span>}
                      <span className="al-time" title={new Date(log.timestamp).toLocaleString()}>
                        {relativeTime(log.timestamp)}
                      </span>
                    </div>
                    <div className="al-line2">
                      <span className="al-user">{log.user_email || log.user_id}</span>
                      {log.resource_id && (
                        <>
                          <span className="al-sep">•</span>
                          <span className="al-resource-id">#{log.resource_id.slice(0, 8)}</span>
                        </>
                      )}
                      {detail && (
                        <>
                          <span className="al-sep">•</span>
                          <span className="al-detail" title={detail}>{detail}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
