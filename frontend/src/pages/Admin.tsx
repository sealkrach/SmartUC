import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { Callout, PageHeader, Spinner } from "../components/ui";
import { api, type User } from "../lib/api";
import { DemoModeCard } from "../components/DemoMode";

export default function Admin() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "requester" });
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.post("/users", form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); setForm({ email: "", full_name: "", password: "", role: "requester" }); setError(null); },
    onError: (e: Error) => setError(e.message),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<User> }) => api.patch(`/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });
  return (
    <div>
      <PageHeader title="Users & roles" subtitle="Requesters create and edit their own use cases. Reviewers run gates, validate risk tiers and review relations. Admins manage users and templates." />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 card overflow-hidden">
          {isLoading ? <Spinner /> : (
            <table className="table">
              <thead><tr><th>User</th><th>Role</th><th>Active</th></tr></thead>
              <tbody>
                {data?.map((u) => (
                  <tr key={u.id}>
                    <td><div className="font-medium">{u.full_name || "—"}</div><div className="text-xs text-slate-500">{u.email}</div></td>
                    <td><select className="input py-1 w-auto" value={u.role} onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as User["role"] } })}>{["requester", "reviewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select></td>
                    <td><input type="checkbox" checked={u.is_active} onChange={(e) => update.mutate({ id: u.id, body: { is_active: e.target.checked } })} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <form className="card p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <h3 className="text-sm font-semibold flex items-center gap-2"><UserPlus className="h-4 w-4" /> Add user</h3>
          <div><label className="label">Email</label><input className="input" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
          <div><label className="label">Full name</label><input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
          <div><label className="label">Password</label><input className="input" type="password" required minLength={6} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
          <div><label className="label">Role</label><select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{["requester", "reviewer", "admin"].map((r) => <option key={r}>{r}</option>)}</select></div>
          {error && <Callout kind="warn">{error}</Callout>}
          <button className="btn-primary w-full justify-center" disabled={create.isPending}>Create</button>
        </form>
        <div className="lg:col-span-3"><DemoModeCard /></div>
      </div>
    </div>
  );
}
