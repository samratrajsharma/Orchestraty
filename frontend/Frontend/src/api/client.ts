import axios from "axios";

const api = axios.create({ baseURL: "/api/v1" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("orc_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      // Don't intercept auth endpoints — let the login/register flow
      // handle its own errors without clearing tokens or redirecting.
      const url = err.config?.url || "";
      const isAuthEndpoint =
        url.includes("/auth/login") ||
        url.includes("/auth/register") ||
        url.includes("/auth/me");

      if (!isAuthEndpoint) {
        localStorage.removeItem("orc_token");
        localStorage.removeItem("orc_user");
        // Redirect to the correct login page based on current location
        const isSuperAdmin = window.location.port === "5174";
        const loginPath = isSuperAdmin ? "/" : "/app/login";
        if (window.location.pathname !== loginPath) {
          window.location.href = loginPath;
        }
      }
    }
    return Promise.reject(err);
  }
);

export default api;
