import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine } from "recharts";
import { api, STATUS_LABEL, STATUS_ORDER, humanize } from "../lib/api";
import { isReviewer, useAuth } from "../lib/auth";
import { PageHeader, Spinner, Stat, StatusBadge, TierBadge, fmtDate, Score } from "../components/ui";

interface Dash {
  total: number; by_status: Record<string, number>; by_risk_tier: Record<string, number>; by_business_unit: Record<string, number>;
  by_capability: Record<string, number>; avg_completeness: number; suggested_relations: number; relations_by_type: Record<string, number>;
  top_entities: { kind: string; name: string; use_cases: number }[];
  top_priority: { id: string; ref: string; title: string; priority_score: number; value_score: number; feasibility_score: number; status: string }[];
  matrix: { id: string; ref: string; title: string; value: number; feasibility: number; status: string; risk_tier: string | null }[];
  last_analysis: { status: string; started_at: string; stats: Record<string, number> } | null;
}

const STATUS_HEX: Record<string, string> = { draft: "#94a3b8", submitted: "#0ea5e9", qualified: "#8b5cf6", approved: "#4f46e5", in_build: "#f59e0b", in_production: "#10b981", retired: "#64748b", rejected: "#e11d48" };

export default function Dashboard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dash>("/portfolio/dashboard") });
  const analyze = useMutation({
    mutationFn: () => api.post("/portfolio/analyze"),
    onSuccess: () => setTimeout(() => qc.invalidateQueries(), 4000),
  });
  if (isLoading || !data) return <Spinner />;
  if (data.total === 0) return (
    <div>
      <PageHeader title="Portfolio overview" subtitle="The registry is empty." />
      <div className="card p-8 text-center text-slate-600">
        <p className="font-medium text-slate-800">No use case yet.</p>
        <p className="text-sm mt-1">Start with <Link to="/intake" className="text-indigo-600 hover:underline">New use case</Link>{user?.role === "admin" && <> or load the sample portfolio from <Link to="/admin" className="text-indigo-600 hover:underline">Users → Demo mode</Link></>}.</p>
      </div>
    </div>
  );

  const statusData = STATUS_ORDER.filter((s) => data.by_status[s]).map((s) => ({ name: STATUS_LABEL[s], key: s, n: data.by_status[s] }));
  const tiers = Object.entries(data.by_risk_tier);

  return (
    <div>
      <PageHeader title="Portfolio overview" subtitle={`${data.total} AI use cases · average completeness ${Math.round(data.avg_completeness * 100)}%`}
        actions={isReviewer(user) && (
          <button className="btn-secondary" onClick={() => analyze.mutate()} disabled={analyze.isPending || data.last_analysis?.status === "running"}>
            <Play className="h-4 w-4" /> {data.last_analysis?.status === "running" ? "Analysis running…" : "Analyze portfolio"}
          </button>
        )} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Stat label="Use cases" value={data.total} hint={`${data.by_status.in_production ?? 0} in production`} />
        <Stat label="Pending review" value={(data.by_status.submitted ?? 0) + (data.by_status.qualified ?? 0)} hint="submitted + qualified" />
        <Stat label="Relations to review" value={data.suggested_relations} hint={<Link to="/relations" className="text-indigo-600 hover:underline">open inbox</Link>} />
        <Stat label="High-risk (EU AI Act)" value={data.by_risk_tier.high_risk ?? 0} hint={`${data.by_risk_tier.unclassified ?? 0} unclassified`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">
        <div className="card p-4 lg:col-span-2">
          <h3 className="text-sm font-semibold mb-1">Value × Feasibility</h3>
          <p className="text-xs text-slate-500 mb-3">Top-right quadrant = quick wins. Size is constant; colour is lifecycle status.</p>
          <div className="h-72">
            <ResponsiveContainer>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" dataKey="feasibility" name="Feasibility" domain={[0, 100]} tick={{ fontSize: 11 }} label={{ value: "Feasibility", position: "insideBottom", offset: -5, fontSize: 11 }} />
                <YAxis type="number" dataKey="value" name="Value" domain={[0, 100]} tick={{ fontSize: 11 }} label={{ value: "Value", angle: -90, position: "insideLeft", fontSize: 11 }} />
                <ZAxis range={[90, 90]} />
                <ReferenceLine x={50} stroke="#cbd5e1" /><ReferenceLine y={50} stroke="#cbd5e1" />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ payload }) => {
                  const p = payload?.[0]?.payload as Dash["matrix"][number] | undefined;
                  if (!p) return null;
                  return <div className="card p-2 text-xs shadow-lg"><div className="mono text-slate-500">{p.ref}</div><div className="font-medium">{p.title}</div><div className="mt-1">Value {Math.round(p.value)} · Feasibility {Math.round(p.feasibility)}</div></div>;
                }} />
                <Scatter data={data.matrix}>
                  {data.matrix.map((m) => <Cell key={m.id} fill={STATUS_HEX[m.status] ?? "#64748b"} fillOpacity={0.85} />)}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card p-4">
          <h3 className="text-sm font-semibold mb-3">Pipeline</h3>
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={statusData} layout="vertical" margin={{ left: 10, right: 20 }}>
                <XAxis type="number" hide /><YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 11 }} />
                <Tooltip cursor={{ fill: "#f1f5f9" }} />
                <Bar dataKey="n" radius={[0, 4, 4, 0]}>{statusData.map((d) => <Cell key={d.key} fill={STATUS_HEX[d.key]} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <h3 className="text-sm font-semibold mt-3 mb-2">EU AI Act tiers</h3>
          <div className="flex flex-wrap gap-1.5">{tiers.map(([t, n]) => <span key={t} className="inline-flex items-center gap-1"><TierBadge tier={t === "unclassified" ? null : t} /><span className="text-xs text-slate-500">{n}</span></span>)}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-4 lg:col-span-2">
          <h3 className="text-sm font-semibold mb-3">Top priority</h3>
          <table className="table">
            <thead><tr><th>Ref</th><th>Use case</th><th>Status</th><th className="text-right">Value</th><th className="text-right">Feas.</th><th className="text-right">Priority</th></tr></thead>
            <tbody>
              {data.top_priority.map((u) => (
                <tr key={u.id}>
                  <td className="mono text-slate-500 whitespace-nowrap">{u.ref}</td>
                  <td><Link to={`/usecases/${u.id}`} className="font-medium hover:text-indigo-700">{u.title}</Link></td>
                  <td><StatusBadge status={u.status} /></td>
                  <td className="text-right"><Score value={u.value_score} /></td>
                  <td className="text-right"><Score value={u.feasibility_score} /></td>
                  <td className="text-right"><Score value={u.priority_score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="space-y-4">
          <div className="card p-4">
            <h3 className="text-sm font-semibold mb-2">Most shared entities</h3>
            <ul className="space-y-1.5">
              {data.top_entities.slice(0, 8).map((e) => (
                <li key={e.kind + e.name} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 min-w-0"><span className="chip bg-slate-100 text-slate-500 text-[10px] uppercase">{e.kind}</span><span className="truncate">{e.name}</span></span>
                  <span className="text-xs text-slate-500 tabular-nums">{e.use_cases} UC</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="card p-4">
            <h3 className="text-sm font-semibold mb-2">Relations detected</h3>
            <ul className="space-y-1 text-sm">
              {Object.entries(data.relations_by_type).map(([t, n]) => <li key={t} className="flex justify-between"><span>{humanize(t)}</span><span className="tabular-nums text-slate-500">{n}</span></li>)}
            </ul>
            {data.last_analysis && <p className="text-[11px] text-slate-400 mt-3">Last analysis: {data.last_analysis.status} · {fmtDate(data.last_analysis.started_at)}</p>}
          </div>
          <div className="card p-4">
            <h3 className="text-sm font-semibold mb-2">By capability</h3>
            <ul className="space-y-1 text-sm">
              {Object.entries(data.by_capability).slice(0, 8).map(([t, n]) => <li key={t} className="flex justify-between"><span>{humanize(t)}</span><span className="tabular-nums text-slate-500">{n}</span></li>)}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
