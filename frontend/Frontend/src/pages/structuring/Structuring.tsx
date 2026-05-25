import { useState, useEffect } from "react";
import { datasetApi } from "../../api/datasets";
import { structuringApi } from "../../api/structuring";
import { useTaskPolling } from "../../hooks/usePolling";
import TaskMonitor from "../../components/TaskMonitor/TaskMonitor";
import "./Structuring.css";

interface CleaningStep {
  step: string; description: string; rows_before: number; rows_after: number;
  rows_affected: number; columns_affected: string[];
}

interface CleaningReport {
  original_rows: number; original_columns: number; final_rows: number;
  final_columns: number; steps: CleaningStep[]; total_rows_removed: number;
  total_nulls_filled: number;
}

/** Shape of the `result` JSON stored by the structuring Celery task. */
interface StructuringResult {
  cleaning?: CleaningReport;
  quality?: { overall_score?: number; grade?: string };
  schema?: { row_count?: number; column_count?: number };
  cleaned_file_path?: string;
  original_rows?: number;
  final_rows?: number;
}

const NULL_STRATEGIES = [
  { value: "fill_mode", label: "Fill with Mode", desc: "Replace nulls with the most frequent value in each column" },
  { value: "fill_mean", label: "Fill with Mean", desc: "Replace nulls with column mean (numeric only)" },
  { value: "fill_median", label: "Fill with Median", desc: "Replace nulls with column median (numeric only)" },
  { value: "fill_empty", label: "Fill Empty", desc: "Replace nulls with empty string or zero" },
  { value: "drop_rows", label: "Drop Rows", desc: "Remove any row that contains null values" },
];

export default function Structuring() {
  const [datasets, setDatasets] = useState<Array<{id:string; name:string}>>([]);
  const [datasetId, setDatasetId] = useState("");
  const [nullStrategy, setNullStrategy] = useState("fill_mode");
  const [removeDups, setRemoveDups] = useState(true);
  const [handleOutliers, setHandleOutliers] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [results, setResults] = useState<{result?: StructuringResult} | null>(null);
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<"visual" | "steps" | "raw">("visual");
  const { task, steps, stuck, progressPct } = useTaskPolling(taskId);

  useEffect(() => {
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets((Array.isArray(r.data) ? r.data : r.data.datasets || []).filter((d: {source_type?: string}) => d.source_type !== "image"));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (task?.status === "completed" && datasetId) {
      structuringApi.getResults(datasetId).then((r) => setResults(r.data)).catch(() => {});
      setRunning(false);
    }
    if (task?.status === "failed") setRunning(false);
  }, [task?.status, datasetId]);

  // Auto-load existing results when dataset changes
  useEffect(() => {
    if (!datasetId) return;
    structuringApi.getResults(datasetId).then((r) => setResults(r.data)).catch(() => {});
  }, [datasetId]);

  const handleRun = async () => {
    if (!datasetId) return;
    setRunning(true); setResults(null);
    try {
      const res = await structuringApi.run({
        dataset_id: datasetId, null_strategy: nullStrategy,
        remove_duplicates: removeDups, handle_outliers: handleOutliers,
      });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed to start"); }
  };

  // The Celery task stores { schema, cleaning, quality, ... } — the cleaning
  // report (with steps/totals) lives under `result.cleaning`, NOT `result`.
  const report: CleaningReport | null = results?.result?.cleaning ?? null;
  const quality = results?.result?.quality ?? null;
  const retentionPct = report ? Math.round((report.final_rows / Math.max(report.original_rows, 1)) * 100) : 0;

  return (
    <div>
      <div className="page-header">
        <h1>Data Structuring</h1>
        <p>Clean, normalize, and structure your datasets</p>
      </div>

      <div className="two-col">
        {/* ── Config Panel ── */}
        <div className="card">
          <div className="card-header"><h3>Configuration</h3></div>
          <div className="input-group">
            <label>Dataset</label>
            <select value={datasetId} onChange={(e) => { setDatasetId(e.target.value); setResults(null); }}>
              <option value="">Select a dataset</option>
              {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>

          <div className="input-group">
            <label>Null Handling Strategy</label>
            <select value={nullStrategy} onChange={(e) => setNullStrategy(e.target.value)}>
              {NULL_STRATEGIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <p className="struct-hint">{NULL_STRATEGIES.find(s => s.value === nullStrategy)?.desc}</p>
          </div>

          <div className="struct-options">
            <label className="struct-toggle">
              <input type="checkbox" checked={removeDups} onChange={(e) => setRemoveDups(e.target.checked)} />
              <div>
                <span className="struct-toggle__label">Remove Duplicates</span>
                <span className="struct-toggle__desc">Drop exact duplicate rows</span>
              </div>
            </label>
            <label className="struct-toggle">
              <input type="checkbox" checked={handleOutliers} onChange={(e) => setHandleOutliers(e.target.checked)} />
              <div>
                <span className="struct-toggle__label">Handle Outliers</span>
                <span className="struct-toggle__desc">IQR-based outlier removal</span>
              </div>
            </label>
          </div>

          <button className="btn btn--primary" style={{width: "100%"}} onClick={handleRun} disabled={!datasetId || running}>
            {running ? "Processing..." : "Run Structuring"}
          </button>

          <TaskMonitor task={task} steps={steps} stuck={stuck} progressPct={progressPct} running={running} />
        </div>

        {/* ── Results Panel ── */}
        <div className="card" style={{flex: 2}}>
          <div className="tabs">
            <button className={`tab ${tab === "visual" ? "tab--active" : ""}`} onClick={() => setTab("visual")}>Visual</button>
            <button className={`tab ${tab === "steps" ? "tab--active" : ""}`} onClick={() => setTab("steps")}>Steps</button>
            <button className={`tab ${tab === "raw" ? "tab--active" : ""}`} onClick={() => setTab("raw")}>Raw JSON</button>
          </div>

          {!report ? (
            <div className="empty-state"><h3>No results yet</h3><p>Select a dataset and run structuring</p></div>
          ) : (
            <>
              {/* ── Visual Tab ── */}
              {tab === "visual" && (
                <div className="struct-visual">
                  {/* Quality score banner */}
                  {quality && typeof quality.overall_score === "number" && (
                    <div style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "12px 16px", marginBottom: 16, borderRadius: 12,
                      background: "var(--dash-primary-dim)", border: "1px solid var(--dash-border)",
                    }}>
                      <span style={{fontSize: 13, fontWeight: 600, color: "var(--dash-text)"}}>Data Quality Score</span>
                      <span style={{fontSize: 15, fontWeight: 800, color: "var(--dash-primary)"}}>
                        {quality.overall_score}{quality.grade ? ` · Grade ${quality.grade}` : ""}
                      </span>
                    </div>
                  )}
                  {/* Summary stat cards */}
                  <div className="struct-stats-grid">
                    <div className="struct-stat-card">
                      <span className="struct-stat-card__value">{report.original_rows.toLocaleString()}</span>
                      <span className="struct-stat-card__label">Original Rows</span>
                    </div>
                    <div className="struct-stat-card">
                      <span className="struct-stat-card__value">{report.final_rows.toLocaleString()}</span>
                      <span className="struct-stat-card__label">Final Rows</span>
                    </div>
                    <div className="struct-stat-card">
                      <span className="struct-stat-card__value struct-stat-card__value--danger">{report.total_rows_removed.toLocaleString()}</span>
                      <span className="struct-stat-card__label">Rows Removed</span>
                    </div>
                    <div className="struct-stat-card">
                      <span className="struct-stat-card__value struct-stat-card__value--success">{report.total_nulls_filled.toLocaleString()}</span>
                      <span className="struct-stat-card__label">Nulls Handled</span>
                    </div>
                  </div>

                  {/* Retention bar */}
                  <div className="struct-retention">
                    <div className="struct-retention__header">
                      <span>Data Retention</span>
                      <span className="struct-retention__pct">{retentionPct}%</span>
                    </div>
                    <div className="struct-retention__track">
                      <div className="struct-retention__fill" style={{
                        width: `${retentionPct}%`,
                        background: retentionPct > 90 ? "#22c55e" : retentionPct > 70 ? "#f59e0b" : "#ef4444"
                      }} />
                    </div>
                    <div className="struct-retention__footer">
                      <span>{report.original_columns} columns</span>
                      <span>{report.final_columns} final columns</span>
                    </div>
                  </div>

                  {/* Steps timeline */}
                  {(report.steps?.length ?? 0) > 0 && (
                    <div className="struct-timeline">
                      <h4>Cleaning Pipeline</h4>
                      {(report.steps ?? []).map((step, i) => (
                        <div key={i} className="struct-timeline__step">
                          <div className="struct-timeline__dot" />
                          <div className="struct-timeline__content">
                            <span className="struct-timeline__name">{step.step.replace(/_/g, " ")}</span>
                            <span className="struct-timeline__desc">{step.description}</span>
                            <div className="struct-timeline__meta">
                              <span>{step.rows_before.toLocaleString()} → {step.rows_after.toLocaleString()} rows</span>
                              {step.rows_affected > 0 && <span className="struct-timeline__affected">({step.rows_affected} affected)</span>}
                              {step.columns_affected.length > 0 && (
                                <span className="struct-timeline__cols">Cols: {step.columns_affected.slice(0, 5).join(", ")}{step.columns_affected.length > 5 ? ` +${step.columns_affected.length - 5}` : ""}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* ── Steps Tab ── */}
              {tab === "steps" && (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr><th>Step</th><th>Description</th><th>Before</th><th>After</th><th>Affected</th><th>Columns</th></tr>
                    </thead>
                    <tbody>
                      {(report.steps ?? []).map((step, i) => (
                        <tr key={i}>
                          <td style={{fontWeight: 600, color: "var(--dash-text)"}}>{step.step.replace(/_/g, " ")}</td>
                          <td>{step.description}</td>
                          <td>{step.rows_before.toLocaleString()}</td>
                          <td>{step.rows_after.toLocaleString()}</td>
                          <td>{step.rows_affected.toLocaleString()}</td>
                          <td style={{fontSize: 12, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}
                            title={step.columns_affected.join(", ")}>
                            {step.columns_affected.join(", ") || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* ── Raw JSON Tab ── */}
              {tab === "raw" && (
                <pre style={{fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto", maxHeight: 500, whiteSpace: "pre-wrap", background: "var(--dash-surface-hover)", padding: 12, borderRadius: 8}}>
                  {JSON.stringify(results, null, 2)}
                </pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
