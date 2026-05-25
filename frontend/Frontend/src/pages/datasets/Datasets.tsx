import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { datasetApi, inferSourceTypeFromFile } from "../../api/datasets";
import { imageApi } from "../../api/images";

interface Dataset {
  id: string; name: string; description?: string; status?: string;
  source_type?: string; row_count?: number; created_at: string;
}

type DatasetType = "tabular" | "image";

const TABULAR_ACCEPT = ".csv,.json,.jsonl,.tsv,.txt";
const IMAGE_BATCH = 20;

export default function Datasets() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [datasetType, setDatasetType] = useState<DatasetType>("tabular");
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets(Array.isArray(r.data) ? r.data : r.data.datasets || []);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const resetForm = () => {
    setShowUpload(false);
    setFiles([]); setName(""); setDesc(""); setDatasetType("tabular"); setUploadMsg("");
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) return;
    setUploading(true);
    setUploadMsg("");
    try {
      const defaultName = files[0].name.replace(/\.[^.]+$/, "");
      const created = await datasetApi.create({
        name: name || (datasetType === "image" ? `${defaultName} (images)` : defaultName),
        description: desc || undefined,
        source_type: datasetType === "image" ? "image" : inferSourceTypeFromFile(files[0]),
      });
      const newId = created.data?.id;
      if (newId) {
        if (datasetType === "image") {
          const imageFiles = files.filter((f) => f.type.startsWith("image/"));
          if (imageFiles.length === 0) throw new Error("No image files selected");
          for (let i = 0; i < imageFiles.length; i += IMAGE_BATCH) {
            setUploadMsg(`Uploading images ${i + 1}-${Math.min(i + IMAGE_BATCH, imageFiles.length)} of ${imageFiles.length}...`);
            await imageApi.uploadBatch(newId, imageFiles.slice(i, i + IMAGE_BATCH));
          }
        } else {
          await datasetApi.upload(newId, files[0]);
        }
      }
      resetForm();
      load();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      const msg = (err as { message?: string })?.message;
      alert(`Upload failed: ${typeof detail === "string" ? detail : (msg || JSON.stringify(detail ?? err))}`);
    }
    finally { setUploading(false); setUploadMsg(""); }
  };

  const onFilesPicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFiles(e.target.files ? Array.from(e.target.files) : []);
  };

  return (
    <div>
      <div className="page-header" style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start"}}>
        <div>
          <h1>Datasets</h1>
          <p>Manage your data assets — tabular files and image collections</p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowUpload(true)}>+ New Dataset</button>
      </div>

      {loading ? <div className="empty-state"><h3>Loading...</h3></div> : datasets.length === 0 ? (
        <div className="empty-state">
          <h3>No datasets yet</h3>
          <p style={{marginTop: 8}}>Upload your first dataset to get started</p>
        </div>
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Rows</th><th>Created</th><th></th></tr></thead>
              <tbody>
                {datasets.map((d) => (
                  <tr key={d.id}>
                    <td style={{color:"var(--dash-text)", fontWeight: 600}}>{d.name}</td>
                    <td>
                      <span className={`badge ${d.source_type === "image" ? "badge--info" : "badge--neutral"}`}>
                        {d.source_type === "image" ? "Image" : "Tabular"}
                      </span>
                    </td>
                    <td><span className={`badge ${d.status === "ready" ? "badge--success" : "badge--info"}`}>{d.status || "uploaded"}</span></td>
                    <td>{d.source_type === "image" ? "—" : (d.row_count ?? "—")}</td>
                    <td>{new Date(d.created_at).toLocaleDateString()}</td>
                    <td><Link to={`/app/datasets/${d.id}`} className="btn btn--sm btn--secondary">View</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showUpload && (
        <div className="modal-overlay" onClick={resetForm}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>New Dataset</h2>
            <form onSubmit={handleUpload}>
              {/* Dataset type selector */}
              <div className="input-group">
                <label>Dataset type</label>
                <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10}}>
                  {([
                    { key: "tabular", title: "Tabular data", sub: "CSV, JSON, JSONL, TSV, TXT" },
                    { key: "image", title: "Image dataset", sub: "PNG, JPG, WEBP, BMP, TIFF" },
                  ] as { key: DatasetType; title: string; sub: string }[]).map((opt) => (
                    <button type="button" key={opt.key}
                      onClick={() => { setDatasetType(opt.key); setFiles([]); }}
                      style={{
                        textAlign: "left", padding: "12px 14px", borderRadius: 12,
                        border: `1.5px solid ${datasetType === opt.key ? "var(--brand-indigo)" : "var(--dash-border)"}`,
                        background: datasetType === opt.key ? "var(--dash-primary-dim)" : "#fff",
                        cursor: "pointer",
                      }}>
                      <div style={{fontWeight: 700, fontSize: 13.5, color: "var(--dash-text)"}}>{opt.title}</div>
                      <div style={{fontSize: 11.5, color: "var(--dash-text-muted)", marginTop: 2}}>{opt.sub}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="input-group">
                <label>Name (optional)</label>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My dataset" />
              </div>
              <div className="input-group">
                <label>Description (optional)</label>
                <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Dataset description" rows={2} />
              </div>

              {datasetType === "tabular" ? (
                <div className="input-group">
                  <label>File (CSV, JSON, JSONL, TSV, TXT)</label>
                  <input type="file" accept={TABULAR_ACCEPT} onChange={onFilesPicked} required />
                </div>
              ) : (
                <div className="input-group">
                  <label>Image files (select multiple)</label>
                  <input type="file" accept="image/*" multiple onChange={onFilesPicked} required />
                  {files.length > 0 && (
                    <p style={{fontSize: 12, color: "var(--dash-text-muted)", marginTop: 6}}>
                      {files.length} image{files.length === 1 ? "" : "s"} selected
                    </p>
                  )}
                </div>
              )}

              {uploadMsg && (
                <p style={{fontSize: 12.5, color: "var(--dash-primary)", marginBottom: 12}}>{uploadMsg}</p>
              )}

              <div style={{display:"flex", gap: 12, justifyContent:"flex-end"}}>
                <button type="button" className="btn btn--secondary" onClick={resetForm}>Cancel</button>
                <button type="submit" className="btn btn--primary" disabled={uploading || files.length === 0}>
                  {uploading ? "Uploading..." : "Create Dataset"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
