import { useState, useEffect, useCallback } from "react";
import { auditApi } from "../../api/audit";
import "./AuditLogs.css";

interface AuditLog {
  id: string; user_id: string; user_email?: string; action: string;
  resource_type: string; resource_id?: string; details?: string;
  timestamp: string;
}

type Category = "create" | "update" | "delete" | "auth" | "run" | "default";

/** Map an action string to a visual category. */
function categorize(action: string): Category {
  const a = action.toLowerCase();
  if (a.includes("delete") || a.includes("remove") || a.includes("deactivate")) return "delete";
  if (a.includes("create") || a.includes("register") || a.includes("upload") || a.includes("add")) return "create";
  if (a.includes("login") || a.includes("logout") || a.includes("auth")) return "auth";
  if (a.includes("run") || a.includes("execute") || a.includes("start")) return "run";
  if (a.includes("update") || a.includes("role") || a.includes("status") || a.includes("edit")) return "update";
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

/** Human-friendly relative time. */
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

  const hasFilters = actionFilter !== "" || resourceFilter !== "";

  return (
    <div>
      <div className="page-header">
        <h1>Audit Logs</h1>
        <p>A complete, time-ordered record of activity across your workspace</p>
      </div>

      <div className="card">
        <div className="card-header" style={{marginBottom: 16}}>
          <h3>Activity Log {logs.length > 0 && <span style={{color: "var(--dash-text-muted)", fontWeight: 500}}>· {logs.length}</span>}</h3>
        </div>

        <div className="al-toolbar" style={{marginBottom: 8}}>
          <input className="al-input" value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)} placeholder="Filter by action..." />
          <input className="al-input" value={resourceFilter}
            onChange={(e) => setResourceFilter(e.target.value)} placeholder="Filter by resource..." />
          {hasFilters && (
            <button className="al-clear" onClick={() => { setActionFilter(""); setResourceFilter(""); }}>
              Clear filters
            </button>
          )}
        </div>

        {loading ? (
          <div className="empty-state"><h3>Loading activity...</h3></div>
        ) : logs.length === 0 ? (
          <div className="empty-state">
            <h3>No activity yet</h3>
            <p>{hasFilters ? "No events match your filters" : "Actions across the workspace will appear here"}</p>
          </div>
        ) : (
          <div>
            {logs.map((l) => {
              const cat = categorize(l.action);
              return (
                <div key={l.id} className="al-event">
                  <div className={`al-icon al-icon--${cat}`}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d={CATEGORY_ICON[cat]} />
                    </svg>
                  </div>
                  <div className="al-body">
                    <div className="al-line1">
                      <span className="al-action">{l.action}</span>
                      {l.resource_type && <span className="al-resource">{l.resource_type}</span>}
                      <span className="al-time" title={new Date(l.timestamp).toLocaleString()}>
                        {relativeTime(l.timestamp)}
                      </span>
                    </div>
                    <div className="al-line2">
                      <span className="al-user">{l.user_email || l.user_id}</span>
                      {l.resource_id && (
                        <>
                          <span className="al-sep">•</span>
                          <span className="al-resource-id">#{l.resource_id.slice(0, 8)}</span>
                        </>
                      )}
                      {l.details && (
                        <>
                          <span className="al-sep">•</span>
                          <span className="al-detail" title={l.details}>{l.details}</span>
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
