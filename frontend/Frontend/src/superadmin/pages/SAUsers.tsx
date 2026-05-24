import { useState, useEffect, useCallback } from "react";
import { adminApi } from "../../api/admin";

interface UserItem {
  id: string;
  email: string;
  full_name?: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

const ROLE_OPTIONS = ["admin", "reviewer", "annotator", "viewer"];

export default function SAUsers() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleFilter, setRoleFilter] = useState("");
  const [resetModal, setResetModal] = useState<UserItem | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [actionMsg, setActionMsg] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    adminApi.getUsers(roleFilter ? { role: roleFilter } : undefined)
      .then((r) => setUsers(Array.isArray(r.data) ? r.data : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [roleFilter]);

  useEffect(() => { load(); }, [load]);

  const showMsg = (msg: string) => {
    setActionMsg(msg);
    setTimeout(() => setActionMsg(""), 3000);
  };

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      const res = await adminApi.updateRole(userId, newRole);
      showMsg(res.data.message || "Role updated");
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to update role");
    }
  };

  const handleToggleActive = async (u: UserItem) => {
    try {
      await adminApi.updateStatus(u.id, !u.is_active);
      showMsg(`${u.email} ${u.is_active ? "deactivated" : "activated"}`);
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed");
    }
  };

  const handleDelete = async (u: UserItem) => {
    if (!confirm(`Permanently delete user ${u.email}? This cannot be undone.`)) return;
    try {
      await adminApi.deleteUser(u.id);
      showMsg(`${u.email} deleted`);
      load();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed");
    }
  };

  const handleResetPassword = async () => {
    if (!resetModal || !newPassword) return;
    try {
      await adminApi.resetPassword(resetModal.id, newPassword);
      showMsg(`Password reset for ${resetModal.email}`);
      setResetModal(null);
      setNewPassword("");
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed");
    }
  };

  const roleBadge = (role: string) => (
    <span className={`sa-role-badge sa-role-badge--${role}`}>{role}</span>
  );

  return (
    <div>
      <div className="page-header">
        <h1>User Management</h1>
        <p>Manage all users — promote admins, reset passwords, deactivate or delete accounts</p>
      </div>

      {actionMsg && (
        <div style={{
          padding: "10px 16px", background: "rgba(5,150,105,0.08)", color: "#059669",
          borderRadius: 10, marginBottom: 16, fontWeight: 600, fontSize: 13
        }}>
          {actionMsg}
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3>All Users ({users.length})</h3>
          <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}
            style={{ padding: "6px 12px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 8, color: "var(--dash-text)", fontSize: 13 }}>
            <option value="">All roles</option>
            <option value="superadmin">SuperAdmin</option>
            <option value="admin">Admin</option>
            <option value="reviewer">Reviewer</option>
            <option value="annotator">Annotator</option>
            <option value="viewer">Viewer</option>
          </select>
        </div>

        {loading ? (
          <div className="empty-state"><orc-spinner size="lg" /><h3>Loading users...</h3></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>User</th><th>Email</th><th>Role</th><th>Status</th><th>Created</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td style={{ color: "var(--dash-text)", fontWeight: 600 }}>{u.full_name || "—"}</td>
                    <td>{u.email}</td>
                    <td>
                      {u.role === "superadmin" ? (
                        roleBadge("superadmin")
                      ) : (
                        <select value={u.role} onChange={(e) => handleRoleChange(u.id, e.target.value)}
                          style={{ padding: "4px 8px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 6, color: "var(--dash-text)", fontSize: 12 }}>
                          {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                        </select>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${u.is_active ? "badge--success" : "badge--danger"}`}>
                        {u.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td>{new Date(u.created_at).toLocaleDateString()}</td>
                    <td>
                      {u.role !== "superadmin" && (
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <button className="btn btn--sm btn--secondary" onClick={() => handleToggleActive(u)}>
                            {u.is_active ? "Deactivate" : "Activate"}
                          </button>
                          <button className="btn btn--sm btn--secondary" onClick={() => { setResetModal(u); setNewPassword(""); }}>
                            Reset PW
                          </button>
                          <button className="btn btn--sm btn--danger" onClick={() => handleDelete(u)}>
                            Delete
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Password Reset Modal */}
      {resetModal && (
        <div className="sa-modal-overlay" onClick={() => setResetModal(null)}>
          <div className="sa-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Reset Password</h3>
            <p style={{ color: "var(--dash-text-muted)", fontSize: 13, marginBottom: 16 }}>
              Set a new password for <strong>{resetModal.email}</strong>
            </p>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="New password"
              style={{
                width: "100%", padding: "10px 14px", border: "1.5px solid var(--dash-border)",
                borderRadius: 10, fontSize: 14, marginBottom: 16, boxSizing: "border-box"
              }}
              autoFocus
            />
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button className="btn btn--secondary" onClick={() => setResetModal(null)}>Cancel</button>
              <button className="btn btn--primary" onClick={handleResetPassword} disabled={!newPassword}>
                Reset Password
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
