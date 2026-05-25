import { useState, useEffect } from "react";
import { datasetApi } from "../../api/datasets";
import { aiLabelingApi, type AIExample } from "../../api/aiLabeling";
import { llmApi } from "../../api/llm";
import { useTaskPolling } from "../../hooks/usePolling";
import TaskMonitor from "../../components/TaskMonitor/TaskMonitor";
import "./AILabeling.css";

type Tab = "predict" | "propagate" | "aggregate" | "synthetic" | "active";

interface Pred { text: string; label: string | null; confidence: number; reasoning?: string }
interface Provider { name: string; display_name: string }

const TAB_META: Record<Tab, { label: string; desc: string }> = {
  predict: {
    label: "AI Predict",
    desc: "Use an LLM to classify each row of a text dataset into your labels. Add few-shot examples and plain-English instructions to steer it — and preview on a few rows before the full run.",
  },
  propagate: {
    label: "Propagation",
    desc: "Spread a handful of high-confidence labels across similar rows automatically, using embedding similarity.",
  },
  aggregate: {
    label: "Aggregation",
    desc: "Combine rule-based, AI, and propagated labels into a single final label per row with a confidence score.",
  },
  synthetic: {
    label: "Synthetic Data",
    desc: "Generate realistic synthetic example texts for a label — useful to balance a small or skewed dataset.",
  },
  active: {
    label: "Active Learning",
    desc: "Surface the rows the model is least certain about — the highest-value candidates for human review.",
  },
};

const confColor = (c: number) => (c >= 0.75 ? "#10b981" : c >= 0.5 ? "#f59e0b" : "#ef4444");

export default function AILabeling() {
  const [datasets, setDatasets] = useState<Array<{id:string; name:string}>>([]);
  const [datasetId, setDatasetId] = useState("");
  const [columns, setColumns] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("predict");

  // LLM provider / model
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelName, setModelName] = useState("");
  const [modelWarning, setModelWarning] = useState("");

  // Predict config
  const [labels, setLabels] = useState<string[]>([]);
  const [labelDraft, setLabelDraft] = useState("");
  const [textColumn, setTextColumn] = useState("");
  const [instructions, setInstructions] = useState("");
  const [examples, setExamples] = useState<AIExample[]>([]);

  // Runs / results
  const [taskId, setTaskId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<unknown>(null);
  const [preview, setPreview] = useState<Pred[] | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Other tabs
  const [confThreshold, setConfThreshold] = useState(0.7);
  const [topK, setTopK] = useState(5);
  const [aggStrategy, setAggStrategy] = useState("confidence_weighted");
  const [includeRule, setIncludeRule] = useState(true);
  const [includeAI, setIncludeAI] = useState(true);
  const [includeProp, setIncludeProp] = useState(true);
  const [syntheticLabel, setSyntheticLabel] = useState("");
  const [syntheticCount, setSyntheticCount] = useState(20);
  const [syntheticResults, setSyntheticResults] = useState<unknown>(null);
  const [activeTopN, setActiveTopN] = useState(20);
  const [activeResults, setActiveResults] = useState<unknown>(null);

  const { task, steps, stuck, progressPct } = useTaskPolling(taskId);

  useEffect(() => {
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets((Array.isArray(r.data) ? r.data : r.data.datasets || []).filter((d: {source_type?: string}) => d.source_type !== "image"));
    }).catch(() => {});
    llmApi.getProviders().then((r) => setProviders(r.data.providers || [])).catch(() => {});
  }, []);

  // Fetch the dataset's columns for the text-column dropdown
  useEffect(() => {
    if (!datasetId) { setColumns([]); return; }
    datasetApi.getColumns(datasetId).then((r) => {
      const cols = r.data.columns || [];
      setColumns(cols);
      // Auto-pick a sensible text column
      if (cols.length) {
        const guess = cols.find((c) => /text|content|body|message|review|comment|description/i.test(c));
        setTextColumn(guess || cols[0]);
      }
    }).catch(() => setColumns([]));
  }, [datasetId]);

  // Fetch models whenever a specific provider is chosen
  useEffect(() => {
    if (!provider) { setModels([]); setModelName(""); setModelWarning(""); return; }
    llmApi.getModels(provider).then((r) => {
      setModels(r.data.models || []);
      setModelName(r.data.models?.[0] || "");
      setModelWarning(r.data.warning || "");
    }).catch(() => { setModels([]); setModelWarning("Could not load models for this provider."); });
  }, [provider]);

  useEffect(() => {
    if (task?.status === "completed") {
      setRunning(false);
      if (datasetId) {
        const fetchResults =
          tab === "propagate" ? aiLabelingApi.getPropagation :
          tab === "aggregate" ? aiLabelingApi.getAggregation :
          aiLabelingApi.getPredictions;
        fetchResults(datasetId).then((r) => setResults(r.data)).catch(() => {});
      }
    }
    if (task?.status === "failed") setRunning(false);
  }, [task?.status, datasetId, tab]);

  // ── Labels (chip input) ──
  const addLabel = (raw: string) => {
    raw.split(",").map((s) => s.trim()).filter(Boolean).forEach((l) => {
      setLabels((prev) => (prev.includes(l) ? prev : [...prev, l]));
    });
    setLabelDraft("");
  };
  const removeLabel = (l: string) => setLabels((prev) => prev.filter((x) => x !== l));

  // ── Few-shot examples ──
  const addExample = () => setExamples((prev) => [...prev, { text: "", label: "" }]);
  const updateExample = (i: number, field: keyof AIExample, val: string) =>
    setExamples((prev) => prev.map((e, idx) => (idx === i ? { ...e, [field]: val } : e)));
  const removeExample = (i: number) => setExamples((prev) => prev.filter((_, idx) => idx !== i));

  const cleanExamples = () => examples.filter((e) => e.text.trim() && e.label.trim());

  const basePredictPayload = () => ({
    dataset_id: datasetId,
    labels,
    text_column: textColumn || "text",
    provider: provider || undefined,
    model_name: modelName || undefined,
    examples: cleanExamples().length ? cleanExamples() : undefined,
    instructions: instructions.trim() || undefined,
  });

  const apiError = (err: unknown): string => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === "string" ? d : "Something went wrong. Check your LLM provider config and try again.";
  };

  const runPreview = async () => {
    if (!datasetId || labels.length === 0) return;
    setPreviewing(true); setPreview(null);
    try {
      const res = await aiLabelingApi.preview({ ...basePredictPayload(), sample_size: 5 });
      setPreview(res.data.predictions || []);
    } catch (err) { alert(`Preview failed: ${apiError(err)}`); }
    finally { setPreviewing(false); }
  };

  const runPredict = async () => {
    if (!datasetId || labels.length === 0) return;
    setRunning(true); setResults(null); setPreview(null);
    try {
      const res = await aiLabelingApi.predict(basePredictPayload());
      setTaskId(res.data.task_id);
    } catch (err) { setRunning(false); alert(`Prediction failed: ${apiError(err)}`); }
  };

  const runPropagate = async () => {
    if (!datasetId || labels.length === 0) return;
    setRunning(true); setResults(null);
    try {
      const res = await aiLabelingApi.propagate({
        dataset_id: datasetId, labels, confidence_threshold: confThreshold, top_k: topK,
      });
      setTaskId(res.data.task_id);
    } catch (err) { setRunning(false); alert(`Propagation failed: ${apiError(err)}`); }
  };

  const runAggregate = async () => {
    if (!datasetId) return;
    setRunning(true); setResults(null);
    try {
      const res = await aiLabelingApi.aggregate({
        dataset_id: datasetId, strategy: aggStrategy,
        include_rule_labels: includeRule, include_ai_labels: includeAI,
        include_propagated_labels: includeProp,
      });
      setTaskId(res.data.task_id);
    } catch (err) { setRunning(false); alert(`Aggregation failed: ${apiError(err)}`); }
  };

  const runSynthetic = async () => {
    if (!syntheticLabel.trim()) return;
    setSyntheticResults("loading");
    try {
      const res = await aiLabelingApi.generateSynthetic({
        label: syntheticLabel.trim(), count: syntheticCount,
        provider: provider || undefined, model_name: modelName || undefined,
      });
      setSyntheticResults(res.data);
    } catch (err) { setSyntheticResults(null); alert(`Generation failed: ${apiError(err)}`); }
  };

  const runActiveLearning = async () => {
    if (!datasetId) return;
    setActiveResults("loading");
    try {
      const res = await aiLabelingApi.activeLearning({ dataset_id: datasetId, top_n: activeTopN });
      setActiveResults(res.data);
    } catch (err) { setActiveResults(null); alert(`Active learning failed: ${apiError(err)}`); }
  };

  // ── Normalize full-run predictions for display ──
  const aiReport = ((results as Record<string, unknown>)?.result as Record<string, unknown>)?.ai_labeling
    ?? (results as Record<string, unknown>)?.result
    ?? results;
  const fullPredsRaw = Array.isArray((aiReport as Record<string, unknown>)?.predictions)
    ? ((aiReport as Record<string, unknown>).predictions as Record<string, unknown>[])
    : [];
  const fullPreds: Pred[] = fullPredsRaw.map((p) => ({
    text: String(p.text ?? ""),
    label: (p.predicted_label as string) || (p.label as string) || null,
    confidence: Number(p.confidence ?? 0),
    reasoning: p.reasoning as string | undefined,
  }));

  const distribution = (preds: Pred[]): Array<[string, number]> => {
    const m: Record<string, number> = {};
    preds.forEach((p) => { const k = p.label || "unlabeled"; m[k] = (m[k] || 0) + 1; });
    return Object.entries(m).sort((a, b) => b[1] - a[1]);
  };

  const renderPredList = (preds: Pred[]) => (
    <div>
      {preds.slice(0, 30).map((p, i) => (
        <div key={i} className="ail-pred">
          <div className="ail-pred__top">
            <span className={`ail-pred__label ${!p.label ? "ail-pred__label--empty" : ""}`}>
              {p.label || "unlabeled"}
            </span>
            <span className="ail-confbar">
              <span className="ail-confbar__fill"
                style={{ width: `${Math.round(p.confidence * 100)}%`, background: confColor(p.confidence) }} />
            </span>
            <span className="ail-pred__conf">{Math.round(p.confidence * 100)}%</span>
          </div>
          <div className="ail-pred__text">{p.text}</div>
          {p.reasoning && <div className="ail-pred__reason">{p.reasoning}</div>}
        </div>
      ))}
      {preds.length > 30 && (
        <p style={{ fontSize: 12, color: "var(--dash-text-muted)", marginTop: 10 }}>
          Showing first 30 of {preds.length} predictions.
        </p>
      )}
    </div>
  );

  const renderDistribution = (preds: Pred[]) => {
    const dist = distribution(preds);
    const max = Math.max(...dist.map((d) => d[1]), 1);
    return (
      <div className="ail-dist">
        {dist.map(([name, count]) => (
          <div key={name} className="ail-dist__row">
            <span className="ail-dist__name">{name}</span>
            <span className="ail-dist__track">
              <span className="ail-dist__fill" style={{ width: `${(count / max) * 100}%` }} />
            </span>
            <span className="ail-dist__count">{count}</span>
          </div>
        ))}
      </div>
    );
  };

  const jsonBlock = (data: unknown) => (
    <pre style={{ fontSize: 12, color: "var(--dash-text-secondary)", overflow: "auto",
      maxHeight: 460, whiteSpace: "pre-wrap", background: "var(--dash-surface-hover)",
      padding: 12, borderRadius: 8 }}>
      {JSON.stringify(data, null, 2)}
    </pre>
  );

  // ── LLM provider / model picker (shared) ──
  const providerPicker = (
    <div className="form-row">
      <div className="input-group">
        <label>LLM Provider</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="">My default provider</option>
          {providers.map((p) => <option key={p.name} value={p.name}>{p.display_name}</option>)}
        </select>
      </div>
      <div className="input-group">
        <label>Model</label>
        {provider && models.length > 0 ? (
          <select value={modelName} onChange={(e) => setModelName(e.target.value)}>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        ) : (
          <input value={modelName} onChange={(e) => setModelName(e.target.value)}
            placeholder={provider ? "model name" : "default model"} />
        )}
      </div>
    </div>
  );

  return (
    <div>
      <div className="page-header">
        <h1>AI-Powered Labeling</h1>
        <p>Let an LLM label your data — predict, propagate, aggregate, and more</p>
      </div>

      <div className="tabs">
        {(Object.keys(TAB_META) as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? "tab--active" : ""}`} onClick={() => setTab(t)}>
            {TAB_META[t].label}
          </button>
        ))}
      </div>
      <p className="ail-tab-desc">{TAB_META[tab].desc}</p>

      <div className="two-col">
        {/* ───────────── Config ───────────── */}
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

          {/* ── Predict ── */}
          {tab === "predict" && (
            <>
              <div className="input-group">
                <label>Text Column</label>
                {columns.length > 0 ? (
                  <select value={textColumn} onChange={(e) => setTextColumn(e.target.value)}>
                    {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : (
                  <input value={textColumn} onChange={(e) => setTextColumn(e.target.value)}
                    placeholder={datasetId ? "text" : "select a dataset first"} />
                )}
              </div>

              <div className="input-group">
                <label>Labels</label>
                <input value={labelDraft}
                  onChange={(e) => setLabelDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addLabel(labelDraft); } }}
                  onBlur={() => labelDraft.trim() && addLabel(labelDraft)}
                  placeholder="Type a label and press Enter" />
                <div className="ail-chips">
                  {labels.map((l) => (
                    <span key={l} className="ail-chip">{l}
                      <button type="button" onClick={() => removeLabel(l)} aria-label={`Remove ${l}`}>&times;</button>
                    </span>
                  ))}
                </div>
              </div>

              {providerPicker}
              {modelWarning && (
                <p style={{ fontSize: 12, color: "var(--dash-warning)", marginTop: -8, marginBottom: 12 }}>
                  {modelWarning}
                </p>
              )}

              <div className="ail-section">
                <div className="ail-section__head">
                  <span className="ail-section__title">Custom instructions</span>
                </div>
                <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3}
                  placeholder="Optional — describe in plain English how to decide the label, e.g. 'Label as urgent only if the message asks for an immediate response.'" />
              </div>

              <div className="ail-section">
                <div className="ail-section__head">
                  <span className="ail-section__title">Few-shot examples</span>
                  <button className="btn btn--sm btn--secondary" onClick={addExample}>+ Add example</button>
                </div>
                <p className="ail-section__hint">Show the model a few text → label pairs to boost accuracy.</p>
                <div className="ail-examples">
                  {examples.length === 0 && <span className="ail-example-empty">No examples yet — optional but recommended.</span>}
                  {examples.map((ex, i) => (
                    <div key={i} className="ail-example-row">
                      <input value={ex.text} onChange={(e) => updateExample(i, "text", e.target.value)} placeholder="example text" />
                      <input value={ex.label} onChange={(e) => updateExample(i, "label", e.target.value)} placeholder="label" />
                      <button className="btn btn--sm btn--danger" onClick={() => removeExample(i)}>&times;</button>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button className="btn btn--secondary" onClick={runPreview}
                  disabled={!datasetId || labels.length === 0 || previewing}>
                  {previewing ? "Testing..." : "Preview on 5 rows"}
                </button>
                <button className="btn btn--primary" onClick={runPredict}
                  disabled={!datasetId || labels.length === 0 || running}>
                  {running ? "Running..." : "Run Full Prediction"}
                </button>
              </div>
            </>
          )}

          {/* ── Propagate ── */}
          {tab === "propagate" && (
            <>
              <div className="input-group">
                <label>Labels</label>
                <input value={labelDraft}
                  onChange={(e) => setLabelDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addLabel(labelDraft); } }}
                  onBlur={() => labelDraft.trim() && addLabel(labelDraft)}
                  placeholder="Type a label and press Enter" />
                <div className="ail-chips">
                  {labels.map((l) => (
                    <span key={l} className="ail-chip">{l}
                      <button type="button" onClick={() => removeLabel(l)}>&times;</button>
                    </span>
                  ))}
                </div>
              </div>
              <div className="form-row">
                <div className="input-group">
                  <label>Confidence Threshold</label>
                  <input type="number" step="0.05" min="0" max="1" value={confThreshold}
                    onChange={(e) => setConfThreshold(+e.target.value)} />
                </div>
                <div className="input-group">
                  <label>Top K Neighbors</label>
                  <input type="number" min={1} max={50} value={topK} onChange={(e) => setTopK(+e.target.value)} />
                </div>
              </div>
              <button className="btn btn--primary" onClick={runPropagate} disabled={!datasetId || running}>
                {running ? "Running..." : "Run Propagation"}
              </button>
            </>
          )}

          {/* ── Aggregate ── */}
          {tab === "aggregate" && (
            <>
              <div className="input-group">
                <label>Strategy</label>
                <select value={aggStrategy} onChange={(e) => setAggStrategy(e.target.value)}>
                  <option value="confidence_weighted">Confidence Weighted</option>
                  <option value="majority_vote">Majority Vote</option>
                </select>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
                {([["Rule labels", includeRule, setIncludeRule],
                   ["AI labels", includeAI, setIncludeAI],
                   ["Propagated labels", includeProp, setIncludeProp]] as const).map(([lbl, val, set]) => (
                  <label key={lbl} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--dash-text-secondary)" }}>
                    <input type="checkbox" checked={val} onChange={(e) => set(e.target.checked)} /> Include {lbl}
                  </label>
                ))}
              </div>
              <button className="btn btn--primary" onClick={runAggregate} disabled={!datasetId || running}>
                {running ? "Running..." : "Run Aggregation"}
              </button>
            </>
          )}

          {/* ── Synthetic ── */}
          {tab === "synthetic" && (
            <>
              <div className="input-group">
                <label>Label to generate for</label>
                <input value={syntheticLabel} onChange={(e) => setSyntheticLabel(e.target.value)} placeholder="positive" />
              </div>
              <div className="input-group">
                <label>How many samples</label>
                <input type="number" min={1} max={100} value={syntheticCount}
                  onChange={(e) => setSyntheticCount(+e.target.value)} />
              </div>
              {providerPicker}
              <button className="btn btn--primary" onClick={runSynthetic} disabled={!syntheticLabel.trim()}>
                {syntheticResults === "loading" ? "Generating..." : "Generate Samples"}
              </button>
            </>
          )}

          {/* ── Active Learning ── */}
          {tab === "active" && (
            <>
              <div className="input-group">
                <label>Number of candidates</label>
                <input type="number" min={1} max={200} value={activeTopN}
                  onChange={(e) => setActiveTopN(+e.target.value)} />
              </div>
              <button className="btn btn--primary" onClick={runActiveLearning} disabled={!datasetId}>
                {activeResults === "loading" ? "Finding..." : "Find Candidates"}
              </button>
            </>
          )}

          <TaskMonitor task={task} steps={steps} stuck={stuck} progressPct={progressPct} running={running} />
        </div>

        {/* ───────────── Results ───────────── */}
        <div className="card">
          <div className="card-header"><h3>Results</h3></div>

          {tab === "predict" ? (
            previewing ? (
              <div className="empty-state"><h3>Testing on a few rows...</h3></div>
            ) : preview ? (
              <div>
                <div className="ail-preview-note">
                  Preview only — {preview.length} sample row{preview.length === 1 ? "" : "s"}.
                  Run the full prediction when the labels look right.
                </div>
                {renderPredList(preview)}
              </div>
            ) : fullPreds.length > 0 ? (
              <div>
                <div className="ail-summary">
                  <div className="ail-summary__card">
                    <div className="ail-summary__value">{fullPreds.length}</div>
                    <div className="ail-summary__label">Rows</div>
                  </div>
                  <div className="ail-summary__card">
                    <div className="ail-summary__value">{fullPreds.filter((p) => p.label).length}</div>
                    <div className="ail-summary__label">Labeled</div>
                  </div>
                  <div className="ail-summary__card">
                    <div className="ail-summary__value">
                      {Math.round((fullPreds.reduce((s, p) => s + p.confidence, 0) / Math.max(fullPreds.length, 1)) * 100)}%
                    </div>
                    <div className="ail-summary__label">Avg Confidence</div>
                  </div>
                </div>
                {renderDistribution(fullPreds)}
                {renderPredList(fullPreds)}
              </div>
            ) : (
              <div className="empty-state">
                <h3>No predictions yet</h3>
                <p>Configure your labels and run a preview or full prediction.</p>
              </div>
            )
          ) : tab === "synthetic" ? (
            syntheticResults && syntheticResults !== "loading" ? jsonBlock(syntheticResults)
              : <div className="empty-state"><h3>No samples yet</h3></div>
          ) : tab === "active" ? (
            activeResults && activeResults !== "loading" ? jsonBlock(activeResults)
              : <div className="empty-state"><h3>No candidates yet</h3></div>
          ) : (
            results ? jsonBlock(results) : <div className="empty-state"><h3>No results yet</h3></div>
          )}
        </div>
      </div>
    </div>
  );
}
