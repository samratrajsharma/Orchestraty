import api from "./client";

export interface DatasetCreatePayload {
  name: string;
  description?: string;
  source_type: "csv" | "json" | "api" | "database" | "image";
}

export const datasetApi = {
  list: (params?: { skip?: number; limit?: number }) =>
    api.get("/datasets", { params }),
  get: (id: string) => api.get(`/datasets/${id}`),
  create: (data: DatasetCreatePayload) => api.post("/datasets", data),
  upload: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/datasets/${id}/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  getVersions: (id: string) => api.get(`/datasets/${id}/versions`),
  getPermissions: (id: string) => api.get(`/datasets/${id}/permissions`),
  getColumns: (id: string) => api.get<{ columns: string[] }>(`/datasets/${id}/columns`),
};

export function inferSourceTypeFromFile(file: File): DatasetCreatePayload["source_type"] {
  const name = file.name.toLowerCase();
  if (name.endsWith(".json") || name.endsWith(".jsonl")) return "json";
  if (name.match(/\.(png|jpe?g|webp|tiff?|bmp)$/)) return "image";
  return "csv";
}
