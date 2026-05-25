import { useState, useEffect } from "react";
import { datasetApi } from "../../api/datasets";
import { labelingApi } from "../../api/labeling";
import type { RuleDefinition } from "../../api/labeling";
import { useTaskPolling } from "../../hooks/usePolling";
import TaskMonitor from "../../components/TaskMonitor/TaskMonitor";
import "./Labeling.css";

interface LabelingReport {
  total_rows: number; labeled_count: number; unlabeled_count: number;
  label_distribution: Record<string, number>; rules_applied: number;
  conflict_count: number;
  per_rule_stats: Array<{ rule_index: number; matches: number; label: string }>;
  warnings: string[];
}

/* ── Rule templates ── */
const RULE_TEMPLATES = [
  { name: "Sentiment (positive)", rules: [{ column: "text", operator: "contains", value: "good,great,excellent,amazing", label: "positive", priority: 0 }] },
  { name: "Sentiment (negative)", rules: [{ column: "text", operator: "contains", value: "bad,terrible,awful,poor", label: "negative", priority: 0 }] },
  { name: "Category by keyword", rules: [{ column: "description", operator: "contains", value: "", label: "", priority: 0 }] },
  { name: "Null detection", rules: [{ column: "", operator: "is_null", value: "", label: "missing", priority: 0 }] },
];

export default function Labeling() {
  const [datasets, setDatasets] = useState<Array<{id:string; name:string}>>([]);
  const [datasetId, setDatasetId] = useState("");
  const [columns, setColumns] = useState<string[]>([]);
  const [operators, setOperators] = useState<string[]>([]);
  const [rules, setRules] = useState<RuleDefinition[]>([{ column: "", operator: "equals", value: "", label: "", priority: 0 }]);
  const [conflictStrategy, setConflictStrategy] = useState("first_match");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [results, setResults] = useState<{ result?: { labeling?: LabelingReport } } | null>(null);
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<"visual" | "rules-perf" | "raw">("visual");
  const { task, steps, stuck, progressPct } = useTaskPolling(taskId);

  useEffect(() => {
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets((Array.isArray(r.data) ? r.data : r.data.datasets || []).filter((d: {source_type?: string}) => d.source_type !== "image"));
    }).catch(() => {});
    labelingApi.getOperators().then((r) => setOperators(r.data.operators || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (task?.status === "completed" && datasetId) {
      labelingApi.getResults(datasetId).then((r) => setResults(r.data)).catch(() => {});
      setRunning(false);
    }
    if (task?.status === "failed") setRunning(false);
  }, [task?.status, datasetId]);

  // Auto-load existing results + the dataset's column list (for the dropdown)
  useEffect(() => {
    if (!datasetId) { setColumns([]); return; }
    labelingApi.getResults(datasetId).then((r) => setResults(r.data)).catch(() => {});
    datasetApi.getColumns(datasetId)
      .then((r) => setColumns(r.data.columns || []))
      .catch(() => setColumns([]));
  }, [datasetId]);

  const addRule = () => setRules([...rules, { column: "", operator: "equals", value: "", label: "", priority: rules.length }]);
  const removeRule = (i: number) => setRules(rules.filter((_, idx) => idx !== i));
  const updateRule = (i: number, field: keyof RuleDefinition, val: string | number) => {
    const updated = [...rules];
    (updated[i] as unknown as Record<string, unknown>)[field] = val;
    setRules(updated);
  };

  const applyTemplate = (tmpl: typeof RULE_TEMPLATES[0]) => {
    setRules(tmpl.rules.map((r, i) => ({ ...r, priority: i })));
  };

  const handleRun = async () => {
    if (!datasetId) return;
    setRunning(true); setResults(null);
    try {
      const validRules = rules.filter((r) => r.column && r.label);
      const res = await labelingApi.run({ dataset_id: datasetId, rules: validRules, conflict_strategy: conflictStrategy });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed"); }
  };

  // The labeling Celery task stores { labeling, labeled_file_path, ... } — the
  // report lives under result.labeling, NOT result itself.
  const report: LabelingReport | null = results?.result?.labeling ?? null;
  const coveragePct = report ? Math.round((report.labeled_count / Math.max(report.total_rows, 1)) * 100) : 0;

  // Label distribution for chart
  const distEntries = report ? Object.entries(report.label_distribution).sort((a, b) => b[1] - a[1]) : [];
  const distMax = distEntries.length > 0 ? Math.max(...distEntries.map(d => d[1])) : 1;

  return (
    <div>
      <div className="page-header">
        <h1>Rule-Based Labeling</h1>
        <p>Define rules to automatically label your data</p>
      </div>

      {/* ── Rules Builder ── */}
      <div className="card" style={{marginBottom: 20}}>
        <div className="card-header">
          <h3>Label Rules</h3>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn btn--sm btn--secondary" onClick={addRule}>+ Add Rule</button>
          </div>
        </div>

        <div className="input-group">
          <label>Dataset</label>
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            <option value="">Select a dataset</option>
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </div>

        {/* Templates */}
        <div className="label-templates">
          <span className="label-templates__title">Quick templates:</span>
          {RULE_TEMPLATES.map((t, i) => (
            <button key={i} className="label-template-chip" onClick={() => applyTemplate(t)}>{t.name}</button>
          ))}
        </div>

        {/* Rules */}
        {rules.map((rule, i) => (
          <div key={i} className="label-rule-row">
            <span className="label-rule-row__num">{i + 1}</span>
            <div className="input-group" style={{flex: 1, marginBottom: 0}}>
              {i === 0 && <label>Column</label>}
              {columns.length > 0 ? (
                <select value={rule.column} onChange={(e) => updateRule(i, "column", e.target.value)}>
                  <option value="">Select column</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input placeholder={datasetId ? "column_name" : "select a dataset first"}
                  value={rule.column} onChange={(e) => updateRule(i, "column", e.target.value)} />
              )}
            </div>
            <div className="input-group" style={{flex: 1, marginBottom: 0}}>
              {i === 0 && <label>Operator</label>}
              <select value={rule.operator} onChange={(e) => updateRule(i, "operator", e.target.value)}>
                {(operators.length ? operators :
                  ["equals","not_equals","contains","not_contains","starts_with","ends_with","regex","greater_than","less_than","in_list","not_in_list","is_null","is_not_null"])
                  .map((op) => <option key={op} value={op}>{op.replace(/_/g, " ")}</option>)}
              </select>
            </div>
            <div className="input-group" style={{flex: 1, marginBottom: 0}}>
              {i === 0 && <label>Value</label>}
              <input placeholder="value" value={rule.value} onChange={(e) => updateRule(i, "value", e.target.value)} />
            </div>
            <div className="input-group" style={{flex: 1, marginBottom: 0}}>
              {i === 0 && <label>Label</label>}
              <input placeholder="label" value={rule.label} onChange={(e) => updateRule(i, "label", e.target.value)}
                style={{borderColor: rule.label ? "#8b5cf6" : undefined}} />
            </div>
            <button className="btn btn--sm btn--danger" onClick={() => removeRule(i)} style={{marginBottom: 0, flexShrink: 0}}>&times;</button>
          </div>
        ))}

        <div style={{display: "flex", gap: 12, marginTop: 16, alignItems: "flex-end", flexWrap: "wrap"}}>
          <div className="input-group" style={{marginBottom: 0}}>
            <label>Conflict Strategy</label>
            <select value={conflictStrategy} onChange={(e) => setConflictStrategy(e.target.value)}>
              <option value="first_match">First Match</option>
              <option value="majority_vote">Majority Vote</option>
            </select>
          </div>
          <button className="btn btn--primary" onClick={handleRun} disabled={!datasetId || running}>
            {running ? "Running..." : "Run Labeling"}
          </button>
        </div>

        <TaskMonitor task={task} steps={steps} stuck={stuck} progressPct={progressPct} running={running} />
      </div>

      {/* ── Results ── */}
      {report && (
        <div className="card">
          <div className="tabs">
            <button className={`tab ${tab === "visual" ? "tab--active" : ""}`} onClick={() => setTab("visual")}>Results</button>
            <button className={`tab ${tab === "rules-perf" ? "tab--active" : ""}`} onClick={() => setTab("rules-perf")}>Rule Performance</button>
            <button className={`tab ${tab === "raw" ? "tab--active" : ""}`} onClick={() => setTab("raw")}>Raw JSON</button>
          </div>

          {/* ── Visual ── */}
          {tab === "visual" && (
            <div className="label-results">
              {/* Stats */}
              <div className="label-stats-grid">
                <div className="label-stat"><span className="label-stat__value">{report.total_rows.toLocaleString()}</span><span className="label-stat__label">Total Rows</span></div>
                <div className="label-stat"><span className="label-stat__value label-stat__value--success">{report.labeled_count.toLocaleString()}</span><span className="label-stat__label">Labeled</span></div>
                <div className="label-stat"><span className="label-stat__value label-stat__value--warning">{report.unlabeled_count.toLocaleString()}</span><span className="label-stat__label">Unlabeled</span></div>
                <div className="label-stat"><span className="label-stat__value">{report.rules_applied}</span><span className="label-stat__label">Rules Used</span></div>
                <div className="label-stat"><span className="label-stat__value label-stat__value--danger">{report.conflict_count}</span><span className="label-stat__label">Conflicts</span></div>
              </div>

              {/* Coverage bar */}
              <div className="label-coverage">
                <div className="label-coverage__header">
                  <span>Label Coverage</span>
                  <span className="label-coverage__pct">{coveragePct}%</span>
                </div>
                <div className="label-coverage__track">
                  <div className="label-coverage__fill" style={{
                    width: `${coveragePct}%`,
                    background: coveragePct > 80 ? "#22c55e" : coveragePct > 50 ? "#f59e0b" : "#ef4444"
                  }} />
                </div>
              </div>

              {/* Label distribution */}
              {distEntries.length > 0 && (
                <div className="label-section">
                  <h4>Label Distribution</h4>
                  <div className="label-dist-chart">
                    {distEntries.map(([label, count]) => (
                      <div key={label} className="label-dist-row">
                        <span className="label-dist-row__label">{label}</span>
                        <div className="label-dist-row__track">
                          <div className="label-dist-row__fill" style={{ width: `${(count / distMax) * 100}%` }} />
                        </div>
                        <span className="label-dist-row__count">{count.toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Warnings */}
              {report.warnings && report.warnings.length > 0 && (
                <div className="label-section">
                  <h4>Warnings</h4>
                  {report.warnings.map((w, i) => (
                    <div key={i} className="label-warning">&#9888; {w}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Rule Performance ── */}
          {tab === "rules-perf" && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>#</th><th>Label</th><th>Matches</th><th>% of Total</th><th>Bar</th></tr>
                </thead>
                <tbody>
                  {(report.per_rule_stats || []).map((rs, i) => (
                    <tr key={i}>
                      <td style={{fontWeight: 600}}>{rs.rule_index + 1}</td>
                      <td><span className="badge badge--info">{rs.label}</span></td>
                      <td>{rs.matches.toLocaleString()}</td>
                      <td>{((rs.matches / Math.max(report.total_rows, 1)) * 100).toFixed(1)}%</td>
                      <td style={{width: 200}}>
                        <div style={{height: 16, background: "var(--dash-surface-hover)", borderRadius: 4, overflow: "hidden"}}>
                          <div style={{height: "100%", width: `${(rs.matches / Math.max(report.total_rows, 1)) * 100}%`, background: "#8b5cf6", borderRadius: 4}} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ── Raw ── */}
          {tab === "raw" && (
            <pre style={{fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto", maxHeight: 500, whiteSpace: "pre-wrap", background: "var(--dash-surface-hover)", padding: 12, borderRadius: 8}}>
              {JSON.stringify(results, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
