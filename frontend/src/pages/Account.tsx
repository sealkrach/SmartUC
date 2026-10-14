import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { Callout, PageHeader } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Account() {
  const { user, logout } = useAuth();
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [error, setError] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: () => api.post("/auth/me/password", { current_password: form.current_password, new_password: form.new_password }),
    onSuccess: () => { setError(null); logout(); },
    onError: (e: Error) => setError(e.message),
  });
  return (
    <div className="max-w-lg">
      <PageHeader title="My account" subtitle={`${user?.full_name || user?.email} · ${user?.role}`} />
      {user?.must_change_password && <div className="mb-4"><Callout kind="warn">Your password was set by an administrator. Please choose a new one now.</Callout></div>}
      <form className="card p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); if (form.new_password !== form.confirm) { setError("Passwords do not match"); return; } change.mutate(); }}>
        <h3 className="text-sm font-semibold flex items-center gap-2"><KeyRound className="h-4 w-4" /> Change password</h3>
        <div><label className="label">Current password</label><input className="input" type="password" autoComplete="current-password" required value={form.current_password} onChange={(e) => setForm({ ...form, current_password: e.target.value })} /></div>
        <div><label className="label">New password (12+ characters)</label><input className="input" type="password" autoComplete="new-password" required minLength={12} value={form.new_password} onChange={(e) => setForm({ ...form, new_password: e.target.value })} /></div>
        <div><label className="label">Confirm</label><input className="input" type="password" autoComplete="new-password" required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} /></div>
        {error && <Callout kind="warn">{error}</Callout>}
        <p className="text-xs text-slate-500">Changing your password signs out every active session, including API clients using a session token (personal API keys stay valid).</p>
        <button className="btn-primary" disabled={change.isPending}>Update password</button>
      </form>
    </div>
  );
}
