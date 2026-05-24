import api from "./client";

export const adminApi = {
  getUsers: (params?: { role?: string; is_active?: boolean }) =>
    api.get("/admin/users", { params }),
  updateRole: (userId: string, role: string) =>
    api.put(`/admin/users/${userId}/role`, { role }),
  updateStatus: (userId: string, is_active: boolean) =>
    api.put(`/admin/users/${userId}/status`, { is_active }),
  deleteUser: (userId: string) => api.delete(`/admin/users/${userId}`),
  resetPassword: (userId: string, new_password: string) =>
    api.put(`/admin/users/${userId}/reset-password`, { new_password }),
  getStats: () => api.get("/admin/stats"),
};
