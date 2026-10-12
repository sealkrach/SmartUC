import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowDown, ArrowUp, GitFork, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, STATUS_LABEL, STATUS_ORDER, humanize, type UseCaseListItem } from "../lib/api";
import { Completeness, Empty, PageHeader, Score, Spinner, StatusBadge, TierBadge } from "../components/ui";

type SortKey = "priority_score" | "updated_at" | "title" | "completeness" | "value_score" | "feasibility_score";

export default function Registry() {
  const [q, setQ] = useState("");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [bu, setBu] = useState("");
  const [cap, setCap] = useState("");
  const [tier, setTier] = useState("");
  const [sort, setSort] = useState<SortKey>("updated_at");
  const [desc, setDesc] = useState(true);
  const { data, isLoading } = useQuery({ queryKey: ["usecases"], queryFn: () => api.get<UseCaseListItem[]>("/usecases?limit=1000") });

  const bus = useMemo(() => Array.from(new Set((data ?? []).map((u) => u.business_unit).filter(Boolean))).sort(), [data]);
  const caps = useMemo(() => Array.from(new Set((data ?? []).map((u) => u.ai_capability).filter(Boolean) as string[])).sort(), [data]);

  const rows = useMemo(() => {
    let r = data ?? [];
    const needle = q.trim().toLowerCase();
    if (needle) r = r.filter((u) => `${u.ref} ${u.title} ${u.summary} ${u.business_unit} ${u.tags.join(" ")}`.toLowerCase().includes(needle));
    if (statuses.length) r = r.filter((u) => statuses.includes(u.status));
    if (bu) r = r.filter((u) => u.business_unit === bu);
    if (cap) r = r.filter((u) => u.ai_capability === cap);
    if (tier) r = r.filter((u) => (tier === "unclassified" ? !u.risk_tier : u.risk_tier === tier));
    r = [...r].sort((a, b) => {
      const av = a[sort] as string | number | null, bv = b[sort] as string | number | null;
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const c = typeof av === "number" ? av - (bv as number) : String(av).localeCompare(String(bv));
      return desc ? -c : c;
    });
    return r;
  }, [data, q, statuses, bu, cap, tier, sort, desc]);

  const toggleSort = (k: SortKey) => { if (sort === k) setDesc(!desc); else { setSort(k); setDesc(true); } };
  const Th = ({ k, children, right }: { k: SortKey; children: React.ReactNode; right?: boolean }) => (
    <th className={clsx("cursor-pointer select-none hover:text-slate-800", right && "text-right")} onClick={() => toggleSort(k)}>
      <span className="inline-flex items-center gap-1">{children}{sort === k && (desc ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}</span>
    </th>
  );

  return (
    <div>
      <PageHeader title="Registry" subtitle={`${rows.length} of ${data?.length ?? 0} use cases`}
        actions={<Link to="/intake" className="btn-primary"><Plus className="h-4 w-4" /> New use case</Link>} />

      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-[220px]">
          <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-slate-400" />
          <input className="input pl-8" placeholder="Search ref, title, summary, tags…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="input w-auto" value={bu} onChange={(e) => setBu(e.target.value)}><option value="">All business units</option>{bus.map((b) => <option key={b}>{b}</option>)}</select>
        <select className="input w-auto" value={cap} onChange={(e) => setCap(e.target.value)}><option value="">All capabilities</option>{caps.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</select>
        <select className="input w-auto" value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="">All risk tiers</option>
          {["high_risk", "limited_risk", "minimal_risk", "gpai_obligations_only", "prohibited", "unclassified"].map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-4">
        {STATUS_ORDER.map((s) => {
          const on = statuses.includes(s);
          const n = (data ?? []).filter((u) => u.status === s).length;
          return (
            <button key={s} onClick={() => setStatuses(on ? statuses.filter((x) => x !== s) : [...statuses, s])}
              className={clsx("chip border transition", on ? "bg-slate-800 text-white border-slate-800" : "bg-white text-slate-600 border-slate-300 hover:border-slate-500")}>
              {STATUS_LABEL[s]} <span className="ml-1 opacity-60 tabular-nums">{n}</span>
            </button>
          );
        })}
      </div>

      <div className="card overflow-hidden">
        {isLoading ? <div className="p-6"><Spinner /></div> : rows.length === 0 ? <Empty title="No use cases match" /> : (
          <table className="table">
            <thead>
              <tr>
                <th>Ref</th><Th k="title">Use case</Th><th>Status</th><th>Risk</th><Th k="completeness">Complete</Th>
                <Th k="value_score" right>Value</Th><Th k="feasibility_score" right>Feas.</Th><Th k="priority_score" right>Priority</Th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id}>
                  <td className="mono text-slate-500 whitespace-nowrap">{u.ref}</td>
                  <td className="max-w-md">
                    <Link to={`/usecases/${u.id}`} className="font-medium hover:text-indigo-700">{u.title}</Link>
                    <div className="text-xs text-slate-500 truncate">{u.business_unit}{u.ai_capability && <> · {humanize(u.ai_capability)}</>}</div>
                  </td>
                  <td><StatusBadge status={u.status} /></td>
                  <td><TierBadge tier={u.risk_tier} /></td>
                  <td><Completeness value={u.completeness} size="sm" /></td>
                  <td className="text-right"><Score value={u.value_score} /></td>
                  <td className="text-right"><Score value={u.feasibility_score} /></td>
                  <td className="text-right"><Score value={u.priority_score} /></td>
                  <td>{u.suggested_relations > 0 && <Link to={`/usecases/${u.id}?tab=relations`} className="chip bg-amber-100 text-amber-800" title="AI-suggested relations to review"><GitFork className="h-3 w-3 mr-1" />{u.suggested_relations}</Link>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
