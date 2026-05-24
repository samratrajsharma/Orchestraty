import api from "./client";

export interface RuleDefinition {
  column: string;
  operator: string;
  value: string;
  label: string;
  priority?: number;
}

export interface LabelingRequest {
  dataset_id: string;
  version_number?: number;
  rules?: RuleDefinition[];
  conflict_strategy?: string;
}

export const labelingApi = {
  run: (data: LabelingRequest) => api.post("/labeling/run", data),
  getResults: (datasetId: string) => api.get(`/labeling/results/${datasetId}`),
  getOperators: () => api.get("/labeling/rules/operators"),
};
