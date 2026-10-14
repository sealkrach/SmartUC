import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Empty, PageHeader, Spinner, fmtDate } from "../components/ui";
import { api } from "../lib/api";

interface AuditEvent { id: string; ts: string; actor_email: string; channel: string; action: string; target_type: string; target_id: string; details: Record<string, unknown>; client_ip: string; request_id: string; outcome: string }

export default function Audit() {
  const [action, setAction] = useState("");
  const [target, setTarget] = useState("");
  const qs = new URLSearchParams({ limit: "300", ...(action ? { action } : {}), ...(target ? { target_id: target } : {}) });
  const { data, isLoading } = useQuery({ queryKey: ["audit", action, target], queryFn: () => api.get<AuditEvent[]>(`/audit?${qs}`) });
  return (
    <div>
      <PageHeader title="Audit trail" subtitle="Append-only record of every state change, authentication event and AI call. Also emitted as JSON lines on the server log for SIEM forwarding." />
      <div className="card p-3 mb-4 flex flex-wrap gap-2">
        <input className="input w-64" placeholder="Action (e.g. usecase.transition)" value={action} onChange={(e) => setAction(e.target.value)} />
        <input className="input w-48" placeholder="Target (e.g. UC-0012)" value={target} onChange={(e) => setTarget(e.target.value)} />
      </div>
      <div className="card overflow-hidden">
        {isLoading ? <div className="p-6"><Spinner /></div> : !data?.length ? <Empty title="No events" /> : (
          <table className="table">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Outcome</th><th>Details</th><th>Request</th></tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.id}>
                  <td className="whitespace-nowrap text-xs">{fmtDate(e.ts)}</td>
                  <td className="text-xs">{e.actor_email || <span className="text-slate-400">system</span>}<div className="text-[10px] text-slate-400">{e.channel} · {e.client_ip}</div></td>
                  <td className="mono">{e.action}</td>
                  <td className="mono text-slate-500">{e.target_type && `${e.target_type}:${e.target_id}`}</td>
                  <td><span className={`chip ${e.outcome === "success" ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"}`}>{e.outcome}</span></td>
                  <td className="text-xs text-slate-600 max-w-md truncate" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</td>
                  <td className="mono text-[10px] text-slate-400">{e.request_id.slice(0, 8)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
