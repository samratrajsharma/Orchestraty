import api from "./client";

export const auditApi = {
  list: (params?: {
    user_id?: string; action?: string; resource_type?: string;
    start_date?: string; end_date?: string; skip?: number; limit?: number;
  }) => api.get("/audit-logs", { params }),
};
