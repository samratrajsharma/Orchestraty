import api from "./client";

export interface StructuringRequest {
  dataset_id: string;
  version_number?: number;
  null_strategy?: string;
  remove_duplicates?: boolean;
  handle_outliers?: boolean;
}

export const structuringApi = {
  run: (data: StructuringRequest) => api.post("/structuring/run", data),
  getResults: (datasetId: string) => api.get(`/structuring/results/${datasetId}`),
};
