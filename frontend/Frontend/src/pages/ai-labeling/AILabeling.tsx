import { useState, useEffect } from "react";
import { datasetApi } from "../../api/datasets";
import { aiLabelingApi } from "../../api/aiLabeling";
import { useTaskPolling } from "../../hooks/usePolling";

export default function AILabeling() {
  const [datasets, setDatasets] = useState<Array<{id:string; name:string}>>([]);
  const [datasetId, setDatasetId] = useState("");
  const [labels, setLabels] = useState("");
  const [textColumn, setTextColumn] = useState("text");
  const [provider, setProvider] = useState("");
  const [modelName, setModelName] = useState("");
  const [tab, setTab] = useState<"predict" | "propagate" | "aggregate" | "synthetic" | "active">("predict");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [results, setResults] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [syntheticLabel, setSyntheticLabel] = useState("");
  const [syntheticCount, setSyntheticCount] = useState(20);
  const [syntheticResults, setSyntheticResults] = useState<unknown>(null);
  const [confThreshold, setConfThreshold] = useState(0.7);
  const [topK, setTopK] = useState(5);
  const [aggStrategy, setAggStrategy] = useState("confidence_weighted");
  const [includeRule, setIncludeRule] = useState(true);
  const [includeAI, setIncludeAI] = useState(true);
  const [includeProp, setIncludeProp] = useState(true);
  const [activeTopN, setActiveTopN] = useState(20);
  const [activeResults, setActiveResults] = useState<unknown>(null);
  const { task } = useTaskPolling(taskId);

  useEffect(() => {
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets(Array.isArray(r.data) ? r.data : r.data.datasets || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (task?.status === "completed") {
      setRunning(false);
      if (datasetId) {
        aiLabelingApi.getPredictions(datasetId).then((r) => setResults(r.data)).catch(() => {});
      }
    }
    if (task?.status === "failed") setRunning(false);
  }, [task?.status, datasetId]);

  const loadResults = () => {
    if (!datasetId) return;
    aiLabelingApi.getPredictions(datasetId).then((r) => setResults(r.data)).catch(() => {});
  };

  const runPredict = async () => {
    if (!datasetId || !labels) return;
    setRunning(true); setResults(null);
    try {
      const res = await aiLabelingApi.predict({
        dataset_id: datasetId, labels: labels.split(",").map((l) => l.trim()),
        text_column: textColumn, provider: provider || undefined, model_name: modelName || undefined,
      });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed"); }
  };

  const runPredictImages = async () => {
    if (!datasetId || !labels) return;
    setRunning(true);
    try {
      const res = await aiLabelingApi.predictImages({
        dataset_id: datasetId, labels: labels.split(",").map((l) => l.trim()),
        provider: provider || undefined, model_name: modelName || undefined,
      });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed"); }
  };

  const runPropagate = async () => {
    if (!datasetId || !labels) return;
    setRunning(true);
    try {
      const res = await aiLabelingApi.propagate({
        dataset_id: datasetId, labels: labels.split(",").map((l) => l.trim()),
        confidence_threshold: confThreshold, top_k: topK,
      });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed"); }
  };

  const runAggregate = async () => {
    if (!datasetId) return;
    setRunning(true);
    try {
      const res = await aiLabelingApi.aggregate({
        dataset_id: datasetId, strategy: aggStrategy,
        include_rule_labels: includeRule, include_ai_labels: includeAI,
        include_propagated_labels: includeProp,
      });
      setTaskId(res.data.task_id);
    } catch { setRunning(false); alert("Failed"); }
  };

  const runSynthetic = async () => {
    if (!syntheticLabel) return;
    try {
      const res = await aiLabelingApi.generateSynthetic({
        label: syntheticLabel, count: syntheticCount,
        provider: provider || undefined, model_name: modelName || undefined,
      });
      setSyntheticResults(res.data);
    } catch { alert("Failed"); }
  };

  const runActiveLearning = async () => {
    if (!datasetId) return;
    try {
      const res = await aiLabelingApi.activeLearning({ dataset_id: datasetId, top_n: activeTopN });
      setActiveResults(res.data);
    } catch { alert("Failed"); }
  };

  return (
    <div>
      <div className="page-header">
        <h1>AI-Powered Labeling</h1>
        <p>Use LLMs to predict labels, generate synthetic data, propagate, and aggregate</p>
      </div>

      <div className="tabs">
        {(["predict", "propagate", "aggregate", "synthetic", "active"] as const).map((t) => (
          <button key={t} className={`tab ${tab === t ? "tab--active" : ""}`} onClick={() => setTab(t)}>
            {t === "predict" ? "AI Predict" : t === "propagate" ? "Propagation" : t === "aggregate" ? "Aggregation" : t === "synthetic" ? "Synthetic Data" : "Active Learning"}
          </button>
        ))}
      </div>

      <div className="two-col">
        <div className="card">
          <div className="card-header"><h3>Configuration</h3></div>
          {tab !== "synthetic" && (
            <div className="input-group">
              <label>Dataset</label>
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                <option value="">Select a dataset</option>
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </div>
          )}

          {(tab === "predict" || tab === "propagate") && (
            <div className="input-group">
              <label>Labels (comma-separated)</label>
              <input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="positive, negative, neutral" />
            </div>
          )}

          {tab === "predict" && (
            <>
              <div className="input-group">
                <label>Text Column</label>
                <input value={textColumn} onChange={(e) => setTextColumn(e.target.value)} />
              </div>
              <div className="form-row">
                <div className="input-group">
                  <label>Provider (optional)</label>
                  <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="ollama" />
                </div>
                <div className="input-group">
                  <label>Model (optional)</label>
                  <input value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder="gemma4:e4b" />
                </div>
              </div>
              <div style={{display:"flex", gap: 12}}>
                <button className="btn btn--primary" onClick={runPredict} disabled={!datasetId || !labels || running}>
                  {running ? "Running..." : "Predict Text"}
                </button>
                <button className="btn btn--secondary" onClick={runPredictImages} disabled={!datasetId || !labels || running}>
                  Predict Images
                </button>
              </div>
            </>
          )}

          {tab === "propagate" && (
            <>
              <div className="form-row">
                <div className="input-group">
                  <label>Confidence Threshold</label>
                  <input type="number" step="0.05" min="0" max="1" value={confThreshold} onChange={(e) => setConfThreshold(+e.target.value)} />
                </div>
                <div className="input-group">
                  <label>Top K</label>
                  <input type="number" min={1} max={50} value={topK} onChange={(e) => setTopK(+e.target.value)} />
                </div>
              </div>
              <button className="btn btn--primary" onClick={runPropagate} disabled={!datasetId || running}>
                {running ? "Running..." : "Run Propagation"}
              </button>
            </>
          )}

          {tab === "aggregate" && (
            <>
              <div className="input-group">
                <label>Strategy</label>
                <select value={aggStrategy} onChange={(e) => setAggStrategy(e.target.value)}>
                  <option value="confidence_weighted">Confidence Weighted</option>
                  <option value="majority_vote">Majority Vote</option>
                </select>
              </div>
              <div style={{display:"flex", flexDirection:"column", gap: 8, marginBottom: 16}}>
                <label style={{display:"flex", alignItems:"center", gap: 8, fontSize: 13, color: "var(--dash-text-secondary)"}}>
                  <input type="checkbox" checked={includeRule} onChange={(e) => setIncludeRule(e.target.checked)} /> Include Rule Labels
                </label>
                <label style={{display:"flex", alignItems:"center", gap: 8, fontSize: 13, color: "var(--dash-text-secondary)"}}>
                  <input type="checkbox" checked={includeAI} onChange={(e) => setIncludeAI(e.target.checked)} /> Include AI Labels
                </label>
                <label style={{display:"flex", alignItems:"center", gap: 8, fontSize: 13, color: "var(--dash-text-secondary)"}}>
                  <input type="checkbox" checked={includeProp} onChange={(e) => setIncludeProp(e.target.checked)} /> Include Propagated Labels
                </label>
              </div>
              <button className="btn btn--primary" onClick={runAggregate} disabled={!datasetId || running}>
                {running ? "Running..." : "Run Aggregation"}
              </button>
            </>
          )}

          {tab === "synthetic" && (
            <>
              <div className="input-group">
                <label>Label</label>
                <input value={syntheticLabel} onChange={(e) => setSyntheticLabel(e.target.value)} placeholder="positive" />
              </div>
              <div className="input-group">
                <label>Count</label>
                <input type="number" min={1} max={100} value={syntheticCount} onChange={(e) => setSyntheticCount(+e.target.value)} />
              </div>
              <div className="form-row">
                <div className="input-group">
                  <label>Provider (optional)</label>
                  <input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="ollama" />
                </div>
                <div className="input-group">
                  <label>Model (optional)</label>
                  <input value={modelName} onChange={(e) => setModelName(e.target.value)} />
                </div>
              </div>
              <button className="btn btn--primary" onClick={runSynthetic} disabled={!syntheticLabel}>Generate</button>
            </>
          )}

          {tab === "active" && (
            <>
              <div className="input-group">
                <label>Top N Candidates</label>
                <input type="number" min={1} max={200} value={activeTopN} onChange={(e) => setActiveTopN(+e.target.value)} />
              </div>
              <button className="btn btn--primary" onClick={runActiveLearning} disabled={!datasetId}>Find Candidates</button>
            </>
          )}

          {task && running && (
            <div style={{marginTop: 16}}>
              <div className="progress-bar"><div className="progress-bar__fill" style={{width: `${task.progress || 0}%`}} /></div>
              <p style={{fontSize: 12, color: "var(--dash-text-muted)", marginTop: 6}}>{task.progress_message || "Processing..."}</p>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-header"><h3>Results</h3></div>
          {(tab === "synthetic" && syntheticResults) ? (
            <pre style={{fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto", maxHeight: 500, whiteSpace: "pre-wrap"}}>
              {JSON.stringify(syntheticResults, null, 2)}
            </pre>
          ) : (tab === "active" && activeResults) ? (
            <pre style={{fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto", maxHeight: 500, whiteSpace: "pre-wrap"}}>
              {JSON.stringify(activeResults, null, 2)}
            </pre>
          ) : results ? (
            <pre style={{fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto", maxHeight: 500, whiteSpace: "pre-wrap"}}>
              {JSON.stringify(results, null, 2)}
            </pre>
          ) : (
            <div className="empty-state"><h3>No results yet</h3></div>
          )}
        </div>
      </div>
    </div>
  );
}
