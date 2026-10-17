import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { guestLogin, login } from "../lib/api";
import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useAuth } from "../lib/auth";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const { refresh } = useAuth();
  const pub = useQuery({ queryKey: ["demo-public"], queryFn: async () => { const r = await fetch(`${(import.meta.env.VITE_API_URL as string) || "/api"}/demo/public`); return r.json() as Promise<{ enabled: boolean; count: number }>; } });
  const [guestBusy, setGuestBusy] = useState(false);
  const enterDemo = async () => {
    setGuestBusy(true); setError(null);
    try { await guestLogin(); await refresh(); nav("/"); } catch (err) { setError((err as Error).message); } finally { setGuestBusy(false); }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await login(email, password); await refresh(); nav("/"); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-full grid place-items-center bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 p-4">
      <form onSubmit={submit} className="card w-full max-w-sm p-6 space-y-4">
        <div className="flex items-center gap-2">
          <div className="h-9 w-9 rounded-lg bg-indigo-600 grid place-items-center font-bold text-white text-lg">U</div>
          <div>
            <h1 className="font-semibold text-lg leading-tight">UseCase Hub</h1>
            <p className="text-xs text-slate-500">Sign in to the AI use case registry</p>
          </div>
        </div>
        <div>
          <label className="label">Email</label>
          <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </div>
        <div>
          <label className="label">Password</label>
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {error && <p className="text-sm text-rose-600">{error}</p>}
        <button className="btn-primary w-full justify-center" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        {pub.data?.enabled && (
          <div className="pt-2 border-t border-slate-100">
            <button type="button" onClick={enterDemo} disabled={guestBusy} className="btn-secondary w-full justify-center border-amber-300 text-amber-900 hover:bg-amber-50">
              <FlaskConical className="h-4 w-4" /> {guestBusy ? "Opening…" : `Explore the demo (${pub.data.count} sample use cases, read-only, no account)`}
            </button>
          </div>
        )}
        <p className="text-[11px] text-slate-400 leading-relaxed">Access is nominative and logged. Contact an administrator for an account.</p>
      </form>
    </div>
  );
}
