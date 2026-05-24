import { useState, useEffect, useCallback } from "react";
import { adminApi } from "../../api/admin";
import { useAuth } from "../../context/AuthContext";

interface UserItem {
  id: string; email: string; full_name?: string; role: string;
  is_active: boolean; created_at: string;
}

export default function Admin() {
  const { user } = useAuth();
  const [users, setUsers] = useState<UserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleFilter, setRoleFilter] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    adminApi.getUsers(roleFilter ? { role: roleFilter } : undefined)
      .then((r) => setUsers(Array.isArray(r.data) ? r.data : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [roleFilter]);

  useEffect(() => { load(); }, [load]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    try { await adminApi.updateRole(userId, newRole); load(); }
    catch { alert("Failed"); }
  };

  const handleToggleActive = async (u: UserItem) => {
    try { await adminApi.updateStatus(u.id, !u.is_active); load(); }
    catch { alert("Failed"); }
  };

  const handleDelete = async (u: UserItem) => {
    if (!confirm(`Delete user ${u.email}?`)) return;
    try { await adminApi.deleteUser(u.id); load(); }
    catch { alert("Failed"); }
  };

  const isAdmin = user?.role === "admin" || user?.role === "superadmin";
  if (!isAdmin) {
    return <div className="empty-state"><h3>Access Denied</h3><p>Admin role required</p></div>;
  }

  // Admins can only assign these roles (superadmin can also assign admin via :5174)
  const assignableRoles = user?.role === "superadmin"
    ? ["admin", "reviewer", "annotator", "viewer"]
    : ["reviewer", "annotator", "viewer"];

  return (
    <div>
      <div className="page-header">
        <h1>User Management</h1>
        <p>Manage users, roles, and permissions</p>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Users</h3>
          <div style={{display:"flex", gap: 8}}>
            <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}
              style={{padding: "6px 12px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 8, color: "var(--dash-text)", fontSize: 13}}>
              <option value="">All roles</option>
              <option value="superadmin">SuperAdmin</option>
              <option value="admin">Admin</option>
              <option value="reviewer">Reviewer</option>
              <option value="annotator">Annotator</option>
              <option value="viewer">Viewer</option>
            </select>
          </div>
        </div>

        {loading ? <div className="empty-state"><h3>Loading...</h3></div> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>User</th><th>Email</th><th>Role</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td style={{color: "var(--dash-text)", fontWeight: 600}}>{u.full_name || "\u2014"}</td>
                    <td>{u.email}</td>
                    <td>
                      {u.role === "superadmin" || (u.role === "admin" && user?.role !== "superadmin") ? (
                        <span className={`badge ${u.role === "superadmin" ? "badge--info" : "badge--warning"}`}>{u.role}</span>
                      ) : (
                        <select value={u.role} onChange={(e) => handleRoleChange(u.id, e.target.value)}
                          style={{padding: "4px 8px", background: "var(--dash-bg)", border: "1px solid var(--dash-border)", borderRadius: 6, color: "var(--dash-text)", fontSize: 12}}>
                          {assignableRoles.map((r) => <option key={r} value={r}>{r}</option>)}
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
                      {u.role === "superadmin" ? (
                        <span style={{fontSize: 12, color: "var(--dash-text-muted)"}}>Protected</span>
                      ) : (
                        <div style={{display:"flex", gap: 6}}>
                          {(user?.role === "superadmin" || u.role !== "admin") && (
                            <button className="btn btn--sm btn--secondary" onClick={() => handleToggleActive(u)}>
                              {u.is_active ? "Deactivate" : "Activate"}
                            </button>
                          )}
                          {user?.role === "superadmin" && (
                            <button className="btn btn--sm btn--danger" onClick={() => handleDelete(u)}>Delete</button>
                          )}
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
    </div>
  );
}
