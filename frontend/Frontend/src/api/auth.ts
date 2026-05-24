import api from "./client";

export const authApi = {
  login: (username: string, password: string) => {
    const form = new URLSearchParams();
    form.append("username", username);
    form.append("password", password);
    return api.post("/auth/login", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
  },
  register: (data: { email: string; password: string; full_name: string }) =>
    api.post("/auth/register", data),
  me: () => api.get("/auth/me"),
};
