import { useState, type FormEvent } from "react";
import { useAuth } from "../../context/AuthContext";
import OrchestrateIcon from "../../components/Logo/AntigravityLogo";

export default function SALogin() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      // After login, the SuperAdminApp component will check role
    } catch {
      setError("Invalid credentials. Only the SuperAdmin can access this panel.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="sa-login">
      <div className="sa-login__card">
        <div className="sa-login__brand">
          <OrchestrateIcon size={40} />
          <div>
            <h2>Orchestraty</h2>
            <p>SuperAdmin Panel</p>
          </div>
        </div>

        <form className="sa-login__form" onSubmit={handleSubmit}>
          {error && <div className="sa-login__error">{error}</div>}

          <div className="sa-login__field">
            <label>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@orchestraty.com"
              required
              autoFocus
            />
          </div>

          <div className="sa-login__field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter SuperAdmin password"
              required
            />
          </div>

          <button type="submit" className="sa-login__submit" disabled={loading}>
            {loading ? "Authenticating..." : "Sign In to SuperAdmin"}
          </button>
        </form>

        <p style={{ textAlign: "center", marginTop: 24, fontSize: 13, color: "#9ca3af" }}>
          Not the SuperAdmin?{" "}
          <a href="http://localhost:5173/app/login" style={{ color: "#8b5cf6", fontWeight: 600 }}>
            Go to main app →
          </a>
        </p>
      </div>
    </div>
  );
}
