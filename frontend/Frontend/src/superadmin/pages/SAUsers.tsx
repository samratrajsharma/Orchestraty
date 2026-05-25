import { useState, useEffect, useCallback, useMemo } from "react";
import { adminApi } from "../../api/admin";
import "../../pages/admin/Admin.css";

interface UserItem {
  id: string;
  email: string;
  full_name?: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

const ROLE_OPTIONS = ["admin", "reviewer", "annotator", "viewer"];
const ROLE_ORDER = ["superadmin", "admin", "reviewer", "annotator", "viewer"];

const AVATAR_GRADIENTS = [
  "linear-gradient(135deg, #6366f1, #8b5cf6)",
  "linear-gradient(135deg, #06b6d4, #0891b2)",
  "linear-gradient(135deg, #f59e0b, #f97316)",
  "linear-gradient(135deg, #10b981, #059669)",
  "linear-gradient(135deg, #ec4899, #db2777)",
  "linear-gradient(135deg, #3b82f6, #6366f1)",
];
function avatarGradient(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  return AVATAR_GRADIENTS[Math.abs(hash) % AVATAR_GRADIENTS.length];
}

export default function SAUsers() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleFilter, setRoleFilter] = useState("");
  const [search, setSearch] = useState("");
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

  const errDetail = (err: unknown): string => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === "string" ? d : d ? JSON.stringify(d) : "Request failed";
  };

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      const res = await adminApi.updateRole(userId, newRole);
      showMsg(res.data?.message || "Role updated");
      load();
    } catch (err) { alert(errDetail(err)); }
  };

  const handleToggleActive = async (u: UserItem) => {
    try {
      await adminApi.updateStatus(u.id, !u.is_active);
      showMsg(`${u.email} ${u.is_active ? "deactivated" : "activated"}`);
      load();
    } catch (err) { alert(errDetail(err)); }
  };

  const handleDelete = async (u: UserItem) => {
    if (!confirm(`Permanently delete user ${u.email}? This cannot be undone.`)) return;
    try {
      await adminApi.deleteUser(u.id);
      showMsg(`${u.email} deleted`);
      load();
    } catch (err) { alert(errDetail(err)); }
  };

  const handleResetPassword = async () => {
    if (!resetModal || !newPassword) return;
    try {
      await adminApi.resetPassword(resetModal.id, newPassword);
      showMsg(`Password reset for ${resetModal.email}`);
      setResetModal(null);
      setNewPassword("");
    } catch (err) { alert(errDetail(err)); }
  };

  const visibleUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? users.filter((u) =>
          (u.full_name || "").toLowerCase().includes(q) ||
          u.email.toLowerCase().includes(q))
      : users;
    return [...filtered].sort(
      (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role)
    );
  }, [users, search]);

  const activeCount = useMemo(() => users.filter((u) => u.is_active).length, [users]);

  return (
    <div>
      <div className="page-header">
        <h1>User Management</h1>
        <p>Manage all users — promote admins, reset passwords, deactivate or delete accounts</p>
      </div>

      {actionMsg && (
        <div style={{
          padding: "10px 16px", background: "rgba(5,150,105,0.1)", color: "#047857",
          borderRadius: 10, marginBottom: 16, fontWeight: 600, fontSize: 13,
          border: "1px solid rgba(5,150,105,0.2)",
        }}>
          {actionMsg}
        </div>
      )}

      <div className="um-stats">
        <div className="um-stat">
          <div className="um-stat__value">{users.length}</div>
          <div className="um-stat__label">Total Users</div>
        </div>
        <div className="um-stat">
          <div className="um-stat__value" style={{color: "var(--dash-success)"}}>{activeCount}</div>
          <div className="um-stat__label">Active</div>
        </div>
        <div className="um-stat">
          <div className="um-stat__value" style={{color: "var(--dash-text-muted)"}}>{users.length - activeCount}</div>
          <div className="um-stat__label">Inactive</div>
        </div>
        <div className="um-stat">
          <div className="um-stat__value" style={{color: "var(--dash-primary)"}}>
            {users.filter((u) => u.role === "admin" || u.role === "superadmin").length}
          </div>
          <div className="um-stat__label">Admins</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header" style={{marginBottom: 16}}>
          <h3>All Users</h3>
        </div>

        <div className="um-toolbar" style={{marginBottom: 18}}>
          <input className="um-search" value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or email..." />
          <select className="um-select" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
            <option value="">All roles</option>
            <option value="superadmin">SuperAdmin</option>
            <option value="admin">Admin</option>
            <option value="reviewer">Reviewer</option>
            <option value="annotator">Annotator</option>
            <option value="viewer">Viewer</option>
          </select>
        </div>

        {loading ? (
          <div className="empty-state"><h3>Loading users...</h3></div>
        ) : visibleUsers.length === 0 ? (
          <div className="empty-state"><h3>No users found</h3><p>Try adjusting your search or filter</p></div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>User</th><th>Role</th><th>Status</th><th>Joined</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {visibleUsers.map((u) => {
                  const initial = (u.full_name?.[0] || u.email[0] || "U").toUpperCase();
                  return (
                    <tr key={u.id}>
                      <td>
                        <div className="um-user">
                          <div className="um-avatar" style={{background: avatarGradient(u.id)}}>{initial}</div>
                          <div>
                            <div className="um-user__name">{u.full_name || "Unnamed user"}</div>
                            <div className="um-user__email">{u.email}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        {u.role === "superadmin" ? (
                          <span className="um-role um-role--superadmin">
                            <span className="um-role__dot" />superadmin
                          </span>
                        ) : (
                          <select className="um-role-select" value={u.role}
                            onChange={(e) => handleRoleChange(u.id, e.target.value)}>
                            {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                          </select>
                        )}
                      </td>
                      <td>
                        <span className={`um-status um-status--${u.is_active ? "active" : "inactive"}`}>
                          <span className="um-status__dot" />
                          {u.is_active ? "Active" : "Inactive"}
                        </span>
                      </td>
                      <td style={{color: "var(--dash-text-muted)", fontSize: 12.5}}>
                        {new Date(u.created_at).toLocaleDateString(undefined, {
                          year: "numeric", month: "short", day: "numeric",
                        })}
                      </td>
                      <td>
                        {u.role === "superadmin" ? (
                          <span className="um-protected">Protected account</span>
                        ) : (
                          <div className="um-actions">
                            <button className="btn btn--sm btn--secondary" onClick={() => handleToggleActive(u)}>
                              {u.is_active ? "Deactivate" : "Activate"}
                            </button>
                            <button className="btn btn--sm btn--secondary"
                              onClick={() => { setResetModal(u); setNewPassword(""); }}>
                              Reset PW
                            </button>
                            <button className="btn btn--sm btn--danger" onClick={() => handleDelete(u)}>Delete</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
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
              placeholder="New password (min 8 characters)"
              className="um-search"
              style={{ width: "100%", marginBottom: 16 }}
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
