import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { datasetApi } from "../../api/datasets";
import { taskApi } from "../../api/tasks";
import "./UserHome.css";

interface TaskRow { id?: string; task_type?: string; status?: string; created_at?: string }
interface DatasetRow { id?: string; source_type?: string }

const QUICK_LINKS = [
  { to: "/app", label: "Open Dashboard", icon: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" },
  { to: "/app/datasets", label: "Datasets", icon: "M12 2C6.5 2 2 4 2 6.5v11C2 20 6.5 22 12 22s10-2 10-4.5v-11C22 4 17.5 2 12 2zM2 9.5c0 2.5 4.5 4.5 10 4.5s10-2 10-4.5" },
  { to: "/app/images", label: "Image Pipeline", icon: "M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21" },
  { to: "/app/workflows", label: "Workflows", icon: "M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zM6 21a3 3 0 100-6 3 3 0 000 6zM18 9a9 9 0 01-9 9" },
];

function relativeTime(iso?: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const min = Math.floor((Date.now() - then) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

export default function UserHome() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [datasets, setDatasets] = useState<DatasetRow[]>([]);

  useEffect(() => {
    datasetApi.list({ limit: 100 })
      .then((r) => setDatasets(Array.isArray(r.data) ? r.data : r.data.datasets || []))
      .catch(() => {});
    taskApi.list({ limit: 100 })
      .then((r) => setTasks(Array.isArray(r.data) ? r.data : r.data.tasks || []))
      .catch(() => {});
  }, []);

  const firstName = user?.full_name?.split(" ")[0] || user?.email?.split("@")[0] || "there";
  const imageDatasets = datasets.filter((d) => d.source_type === "image").length;
  const completed = tasks.filter((t) => t.status === "completed").length;
  const recent = [...tasks]
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime())
    .slice(0, 6);

  const dotClass = (s?: string) => {
    if (s === "completed") return "uh-event__dot--completed";
    if (s === "failed") return "uh-event__dot--failed";
    if (s === "running" || s === "started" || s === "progress") return "uh-event__dot--running";
    return "uh-event__dot--pending";
  };

  return (
    <div className="uh">
      <div className="uh-hero">
        <div className="uh-hero__eyebrow">Your Orchestraty workspace</div>
        <div className="uh-hero__title">Welcome back, {firstName}</div>
        <p className="uh-hero__sub">
          Here's a snapshot of your data-to-model lifecycle. Jump back into the platform
          to ingest, structure, label, and ship production-ready datasets.
        </p>
        <div className="uh-hero__actions">
          <Link to="/app" className="uh-btn uh-btn--solid">
            Go to Dashboard
            <svg width="15" height="15" viewBox="0 0 14 14" fill="none">
              <path d="M2.5 7h9M7 2.5L11.5 7 7 11.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </Link>
          <Link to="/app/datasets" className="uh-btn uh-btn--ghost">Manage Datasets</Link>
        </div>
      </div>

      <div className="uh-stats">
        <div className="uh-stat">
          <div className="uh-stat__value">{datasets.length}</div>
          <div className="uh-stat__label">Total Datasets</div>
        </div>
        <div className="uh-stat">
          <div className="uh-stat__value">{imageDatasets}</div>
          <div className="uh-stat__label">Image Datasets</div>
        </div>
        <div className="uh-stat">
          <div className="uh-stat__value">{completed}</div>
          <div className="uh-stat__label">Tasks Completed</div>
        </div>
        <div className="uh-stat">
          <div className="uh-stat__value">{tasks.length}</div>
          <div className="uh-stat__label">Total Tasks Run</div>
        </div>
      </div>

      <div className="uh-grid">
        <div className="uh-panel">
          <div className="uh-panel__title">Recent Activity</div>
          {recent.length === 0 ? (
            <div className="uh-empty">No activity yet — run your first pipeline to see it here.</div>
          ) : (
            <div className="uh-activity">
              {recent.map((t, i) => (
                <div key={t.id || i} className="uh-event">
                  <span className={`uh-event__dot ${dotClass(t.status)}`} />
                  <span className="uh-event__type">{(t.task_type || "task").replace(/_/g, " ")}</span>
                  <span className="uh-event__meta">{t.status} · {relativeTime(t.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="uh-panel">
          <div className="uh-panel__title">Jump back in</div>
          <div className="uh-links">
            {QUICK_LINKS.map((l) => (
              <Link key={l.to} to={l.to} className="uh-link">
                <span className="uh-link__icon">
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                    <path d={l.icon} />
                  </svg>
                </span>
                <span className="uh-link__text">{l.label}</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
