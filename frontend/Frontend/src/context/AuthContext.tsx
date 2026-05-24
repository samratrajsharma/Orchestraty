import { createContext, useContext, useState, useEffect, useRef, type ReactNode } from "react";
import { authApi } from "../api/auth";

interface User {
  id: string;
  email: string;
  full_name?: string;
  role: string;
  is_active: boolean;
}

interface AuthCtx {
  user: User | null;
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, full_name: string) => Promise<void>;
  logout: () => void;
  loading: boolean;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(localStorage.getItem("orc_token"));
  const [loading, setLoading] = useState(true);

  // Guard: when login/register is in progress, the useEffect must NOT
  // run its own /me call or clear the token on failure.
  const authInProgress = useRef(false);

  // On mount (or token change from external source like another tab),
  // validate the stored token by fetching /me.
  useEffect(() => {
    // Skip if login/register is handling things
    if (authInProgress.current) return;

    if (token) {
      authApi
        .me()
        .then((r) => {
          setUser(r.data);
          setLoading(false);
        })
        .catch(() => {
          setToken(null);
          setUser(null);
          localStorage.removeItem("orc_token");
          setLoading(false);
        });
    } else {
      setUser(null);
      setLoading(false);
    }
  }, [token]);

  const login = async (email: string, password: string) => {
    authInProgress.current = true;
    try {
      const res = await authApi.login(email, password);
      const t = res.data.access_token;
      if (!t) throw new Error("No access token in response");
      localStorage.setItem("orc_token", t);
      setToken(t);

      const me = await authApi.me();
      setUser(me.data);
      setLoading(false);
    } catch (err) {
      // Clean up on failure so ProtectedRoute doesn't flicker
      localStorage.removeItem("orc_token");
      setToken(null);
      setUser(null);
      setLoading(false);
      throw err; // re-throw so the Login page shows the error
    } finally {
      authInProgress.current = false;
    }
  };

  const register = async (email: string, password: string, full_name: string) => {
    authInProgress.current = true;
    try {
      const res = await authApi.register({ email, password, full_name });
      const t = res.data.token.access_token;
      if (!t) throw new Error("No access token in response");
      localStorage.setItem("orc_token", t);
      setToken(t);
      setUser(res.data.user);
      setLoading(false);
    } catch (err) {
      localStorage.removeItem("orc_token");
      setToken(null);
      setUser(null);
      setLoading(false);
      throw err;
    } finally {
      authInProgress.current = false;
    }
  };

  const logout = () => {
    localStorage.removeItem("orc_token");
    localStorage.removeItem("orc_user");
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, login, register, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
