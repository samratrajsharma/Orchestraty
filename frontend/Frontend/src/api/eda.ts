import api from "./client";

export interface EDARequest {
  dataset_id: string;
  version_number?: number;
  run_profiling?: boolean;
  run_embeddings?: boolean;
  run_clustering?: boolean;
  n_clusters?: number;
}

export const edaApi = {
  run: (data: EDARequest) => api.post("/eda/run", data),
  getResults: (datasetId: string) => api.get(`/eda/results/${datasetId}`),
  getProfile: (datasetId: string) => api.get(`/eda/profile/${datasetId}`),
};
