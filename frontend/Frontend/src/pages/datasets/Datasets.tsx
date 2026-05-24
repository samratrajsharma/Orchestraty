import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { datasetApi, inferSourceTypeFromFile } from "../../api/datasets";

interface Dataset {
  id: string; name: string; description?: string; status?: string;
  row_count?: number; created_at: string;
}

export default function Datasets() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    datasetApi.list({ limit: 100 }).then((r) => {
      setDatasets(Array.isArray(r.data) ? r.data : r.data.datasets || []);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    try {
      const created = await datasetApi.create({
        name: name || file.name.replace(/\.[^.]+$/, ""),
        description: desc || undefined,
        source_type: inferSourceTypeFromFile(file),
      });
      const newId = created.data?.id;
      if (newId) {
        await datasetApi.upload(newId, file);
      }
      setShowUpload(false);
      setFile(null); setName(""); setDesc("");
      load();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      alert(`Upload failed: ${typeof detail === "string" ? detail : JSON.stringify(detail ?? err)}`);
    }
    finally { setUploading(false); }
  };

  return (
    <div>
      <div className="page-header" style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start"}}>
        <div>
          <h1>Datasets</h1>
          <p>Manage your data assets</p>
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
              <thead><tr><th>Name</th><th>Status</th><th>Rows</th><th>Created</th><th></th></tr></thead>
              <tbody>
                {datasets.map((d) => (
                  <tr key={d.id}>
                    <td style={{color:"var(--dash-text)", fontWeight: 600}}>{d.name}</td>
                    <td><span className={`badge ${d.status === "ready" ? "badge--success" : "badge--info"}`}>{d.status || "uploaded"}</span></td>
                    <td>{d.row_count ?? "\u2014"}</td>
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
        <div className="modal-overlay" onClick={() => setShowUpload(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Upload Dataset</h2>
            <form onSubmit={handleUpload}>
              <div className="input-group">
                <label>Name (optional)</label>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My dataset" />
              </div>
              <div className="input-group">
                <label>Description (optional)</label>
                <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Dataset description" rows={2} />
              </div>
              <div className="input-group">
                <label>File (CSV, JSON, JSONL, TSV, TXT)</label>
                <input type="file" accept=".csv,.json,.jsonl,.tsv,.txt" onChange={(e) => setFile(e.target.files?.[0] || null)} required />
              </div>
              <div style={{display:"flex", gap: 12, justifyContent:"flex-end"}}>
                <button type="button" className="btn btn--secondary" onClick={() => setShowUpload(false)}>Cancel</button>
                <button type="submit" className="btn btn--primary" disabled={uploading}>
                  {uploading ? "Uploading..." : "Upload"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
