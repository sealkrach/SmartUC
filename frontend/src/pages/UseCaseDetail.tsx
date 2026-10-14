import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowRight, Check, Pencil, RefreshCw, Save, ShieldCheck, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Canvas } from "../components/Canvas";
import { Callout, Completeness, Empty, Modal, RelationBadge, Score, Spinner, StatusBadge, TierBadge, fmtDate } from "../components/ui";
import { api, humanize, STATUS_LABEL, type Decision, type Relation, type Template, type UseCase, type Version } from "../lib/api";
import { isReviewer, useAuth } from "../lib/auth";

const TABS = ["canvas", "relations", "entities", "history"] as const;

export default function UseCaseDetail() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as typeof TABS[number]) || "canvas";
  const qc = useQueryClient();
  const { user } = useAuth();

  const uc = useQuery({ queryKey: ["usecase", id], queryFn: () => api.get<UseCase>(`/usecases/${id}`) });
  const tpl = useQuery({ queryKey: ["template", uc.data?.template_id], enabled: !!uc.data, queryFn: () => api.get<Template>(`/templates/${uc.data!.template_id}`) });

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [transition, setTransition] = useState<{ to: string; gate: string | null } | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [riskModal, setRiskModal] = useState(false);
  useEffect(() => { if (uc.data) setDraft(uc.data.fields); }, [uc.data]);

  const invalidate = () => { qc.invalidateQueries({ queryKey: ["usecase", id] }); qc.invalidateQueries({ queryKey: ["usecases"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); };

  const save = useMutation({
    mutationFn: () => api.put<UseCase>(`/usecases/${id}`, { fields: draft, change_note: "Edited in canvas" }),
    onSuccess: () => { setEditing(false); invalidate(); setTimeout(() => qc.invalidateQueries({ queryKey: ["relations", id] }), 3000); },
    onError: (e: Error) => setError(e.message),
  });
  const doTransition = useMutation({
    mutationFn: (to: string) => api.post<UseCase>(`/usecases/${id}/transition`, { to, comment }),
    onSuccess: () => { setTransition(null); setComment(""); setError(null); invalidate(); qc.invalidateQueries({ queryKey: ["decisions", id] }); },
    onError: (e: Error) => setError(e.message),
  });
  const analyze = useMutation({
    mutationFn: () => api.post(`/usecases/${id}/analyze`),
    onSuccess: () => { invalidate(); qc.invalidateQueries({ queryKey: ["relations", id] }); qc.invalidateQueries({ queryKey: ["entities", id] }); },
    onError: (e: Error) => setError(e.message),
  });
  const improve = async (fieldId: string) => {
    const r = await api.post<{ value: unknown; notes: string }>(`/usecases/${id}/ai/improve`, { field_id: fieldId, value: draft[fieldId] });
    setDraft((d) => ({ ...d, [fieldId]: r.value }));
    if (!editing) setEditing(true);
  };

  if (uc.isLoading || !uc.data || !tpl.data) return <Spinner />;
  const u = uc.data;
  const canEdit = isReviewer(user) || u.owner?.id === user?.id;
  const suggestedTier = (u.ai_suggestions?.risk_tier as string | undefined) || null;

  return (
    <div>
      <div className="mb-1 text-xs text-slate-500"><Link to="/usecases" className="hover:underline">Registry</Link> / <span className="mono">{u.ref}</span></div>
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{u.title}</h1>
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <StatusBadge status={u.status} />
            <TierBadge tier={u.risk_tier ?? suggestedTier} suggested={!u.risk_tier && !!suggestedTier} />
            {u.business_unit && <span className="chip bg-slate-100 text-slate-600">{u.business_unit}</span>}
            {u.ai_capability && <span className="chip bg-slate-100 text-slate-600">{humanize(u.ai_capability)}</span>}
            <span className="text-xs text-slate-400">· owner {u.owner?.full_name || "—"} · via {u.source}</span>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="label mb-1">Scores</div>
            <div className="flex gap-1"><Score value={u.value_score} label="value" /><Score value={u.feasibility_score} label="feas." /><Score value={u.priority_score} label="prio" /></div>
          </div>
          <div><div className="label mb-1">Completeness</div><Completeness value={u.completeness} /></div>
        </div>
      </div>

      {error && <div className="mb-3"><Callout kind="warn">{error} <button className="underline ml-2" onClick={() => setError(null)}>dismiss</button></Callout></div>}
      {u.risk_stale && (
        <div className="mb-3"><Callout kind="warn">Risk-relevant fields changed since the EU AI Act tier was validated. A reviewer must re-validate before approval.</Callout></div>
      )}
      {u.missing_required.length > 0 && (
        <div className="mb-3"><Callout kind="warn">Required fields missing: {u.missing_required.map((f) => humanize(f)).join(", ")}. The use case cannot be submitted yet.</Callout></div>
      )}
      {!!(u.ai_suggestions?.assumptions as string[] | undefined)?.length && (
        <div className="mb-3"><Callout>AI assumptions made during intake: <ul className="list-disc ml-4 mt-1">{(u.ai_suggestions.assumptions as string[]).map((a, i) => <li key={i}>{a}</li>)}</ul></Callout></div>
      )}

      {/* Workflow bar */}
      <div className="card p-3 mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 mr-1">Workflow</span>
        {u.allowed_transitions.length === 0 && <span className="text-sm text-slate-400">No transition available for your role.</span>}
        {u.allowed_transitions.map((t) => (
          <button key={t.to} className={clsx(t.to === "rejected" ? "btn-secondary text-rose-700" : "btn-secondary")} onClick={() => setTransition(t)}>
            <ArrowRight className="h-3.5 w-3.5" /> {STATUS_LABEL[t.to]}
          </button>
        ))}
        <span className="flex-1" />
        {isReviewer(user) && <button className="btn-secondary" onClick={() => setRiskModal(true)}><ShieldCheck className="h-4 w-4" /> {u.risk_tier ? "Re-validate risk tier" : "Validate risk tier"}</button>}
        <button className="btn-secondary" onClick={() => analyze.mutate()} disabled={analyze.isPending}><RefreshCw className={clsx("h-4 w-4", analyze.isPending && "animate-spin")} /> Re-analyze</button>
      </div>

      <div className="flex items-center gap-1 border-b border-slate-200 mb-4">
        {TABS.map((t) => (
          <button key={t} onClick={() => setParams({ tab: t })} className={clsx("px-3 py-2 text-sm border-b-2 -mb-px capitalize", tab === t ? "border-indigo-600 text-indigo-700 font-medium" : "border-transparent text-slate-500 hover:text-slate-800")}>
            {t}{t === "relations" && u.suggested_relations > 0 && <span className="ml-1.5 rounded-full bg-amber-100 text-amber-800 px-1.5 text-[10px] font-bold">{u.suggested_relations}</span>}
          </button>
        ))}
        <span className="flex-1" />
        {tab === "canvas" && canEdit && (editing ? (
          <>
            <button className="btn-ghost" onClick={() => { setEditing(false); setDraft(u.fields); }}><X className="h-4 w-4" /> Cancel</button>
            <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}><Save className="h-4 w-4" /> Save</button>
          </>
        ) : <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" /> Edit</button>)}
      </div>

      {tab === "canvas" && <Canvas template={tpl.data} fields={editing ? draft : u.fields} editing={editing} onChange={setDraft} onImprove={canEdit ? improve : undefined} highlightMissing />}
      {tab === "relations" && <RelationsTab id={id} />}
      {tab === "entities" && <EntitiesTab id={id} />}
      {tab === "history" && <HistoryTab id={id} />}

      {transition && (
        <Modal title={`Move to ${STATUS_LABEL[transition.to]}`} onClose={() => setTransition(null)}>
          {transition.gate && <div className="mb-3"><Callout>{transition.gate}</Callout></div>}
          <label className="label">Decision comment</label>
          <textarea className="input min-h-[90px]" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Why, conditions, next steps…" />
          <div className="flex justify-end gap-2 mt-4">
            <button className="btn-secondary" onClick={() => setTransition(null)}>Cancel</button>
            <button className="btn-primary" onClick={() => doTransition.mutate(transition.to)} disabled={doTransition.isPending}><Check className="h-4 w-4" /> Confirm</button>
          </div>
        </Modal>
      )}
      {riskModal && <RiskModal id={id} current={u.risk_tier} suggestion={u.ai_suggestions} onClose={() => { setRiskModal(false); invalidate(); }} />}
    </div>
  );
}

function RiskModal({ id, current, suggestion, onClose }: { id: string; current: string | null; suggestion: Record<string, unknown>; onClose: () => void }) {
  const [tier, setTier] = useState<string>(current || (suggestion.risk_tier as string) || "");
  const [rationale, setRationale] = useState<string>((suggestion.risk_rationale as string) || "");
  const [sug, setSug] = useState(suggestion);
  const [err, setErr] = useState<string | null>(null);
  const ask = useMutation({
    mutationFn: () => api.post<Record<string, unknown>>(`/usecases/${id}/ai/risk`),
    onSuccess: (r) => { setSug(r); if (!current) setTier(r.risk_tier as string); setRationale(r.risk_rationale as string); },
    onError: (e: Error) => setErr(e.message),
  });
  const validate = useMutation({
    mutationFn: () => api.post(`/usecases/${id}/risk/validate`, { risk_tier: tier, rationale }),
    onSuccess: onClose, onError: (e: Error) => setErr(e.message),
  });
  return (
    <Modal title="EU AI Act risk tier" onClose={onClose}>
      <div className="space-y-3">
        <button className="btn-secondary" onClick={() => ask.mutate()} disabled={ask.isPending}>{ask.isPending ? <Spinner /> : <Sparkles className="h-4 w-4" />} Ask AI for a suggestion</button>
        {!!sug.risk_tier && (
          <Callout>
            <div className="flex items-center gap-2 mb-1"><span className="font-medium">AI suggests</span> <TierBadge tier={sug.risk_tier as string} suggested /> <span className="text-xs">confidence {Math.round(((sug.confidence as number) || 0) * 100)}%</span></div>
            <p>{sug.risk_rationale as string}</p>
            {!!(sug.mitigations as string[] | undefined)?.length && <ul className="list-disc ml-4 mt-1">{(sug.mitigations as string[]).map((m, i) => <li key={i}>{m}</li>)}</ul>}
          </Callout>
        )}
        <div><label className="label">Validated tier</label>
          <select className="input" value={tier} onChange={(e) => setTier(e.target.value)}>
            <option value="">—</option>
            {["minimal_risk", "limited_risk", "high_risk", "prohibited", "gpai_obligations_only"].map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
          </select></div>
        <div><label className="label">Rationale</label><textarea className="input min-h-[90px]" value={rationale} onChange={(e) => setRationale(e.target.value)} /></div>
        {err && <p className="text-sm text-rose-600">{err}</p>}
        <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!tier || validate.isPending} onClick={() => validate.mutate()}><ShieldCheck className="h-4 w-4" /> Validate</button></div>
      </div>
    </Modal>
  );
}

export function RelationRow({ r, selfId, onReview }: { r: Relation; selfId?: string; onReview?: (status: string, type?: string) => void }) {
  const otherIsTarget = selfId ? r.source_id === selfId : true;
  const other = otherIsTarget ? { id: r.target_id, ref: r.target_ref, title: r.target_title } : { id: r.source_id, ref: r.source_ref, title: r.source_title };
  const direction = r.type === "depends_on" ? (otherIsTarget ? "depends on" : "is required by") : null;
  return (
    <div className={clsx("card p-3 flex gap-3", r.status === "suggested" && "border-amber-200 bg-amber-50/40", r.status === "dismissed" && "opacity-50")}>
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <RelationBadge type={r.type} />
          {direction && <span className="text-xs text-slate-500">{direction}</span>}
          {!selfId && <Link to={`/usecases/${r.source_id}`} className="text-sm font-medium hover:text-indigo-700"><span className="mono text-slate-500 mr-1">{r.source_ref}</span>{r.source_title}</Link>}
          {!selfId && <ArrowRight className="h-3.5 w-3.5 text-slate-400" />}
          <Link to={`/usecases/${other.id}`} className="text-sm font-medium hover:text-indigo-700"><span className="mono text-slate-500 mr-1">{other.ref}</span>{other.title}</Link>
          {r.entity && <span className="chip bg-indigo-50 text-indigo-800">{r.entity.kind}: {r.entity.name}</span>}
        </div>
        <p className="text-sm text-slate-700 mt-1.5">{r.explanation}</p>
        {r.suggested_action && <p className="text-xs text-slate-500 mt-1"><span className="font-semibold">Suggested action:</span> {r.suggested_action}</p>}
        <div className="text-[11px] text-slate-400 mt-1.5 flex gap-3">
          <span>origin: {r.origin}</span>
          {r.origin === "ai" && <span>confidence {Math.round(r.confidence * 100)}%</span>}
          {r.similarity != null && <span>similarity {r.similarity.toFixed(2)}</span>}
          <span className="capitalize">{r.status}</span>
        </div>
      </div>
      {onReview && r.status === "suggested" && (
        <div className="flex flex-col gap-1 shrink-0">
          <button className="btn-primary py-1" onClick={() => onReview("confirmed")}><Check className="h-3.5 w-3.5" /> Confirm</button>
          <button className="btn-secondary py-1" onClick={() => onReview("dismissed")}><X className="h-3.5 w-3.5" /> Dismiss</button>
          <select className="input py-1 text-xs" value={r.type} onChange={(e) => onReview("confirmed", e.target.value)} title="Confirm with a different type">
            {["duplicate", "overlap", "complementary", "depends_on", "enables"].map((t) => <option key={t} value={t}>as {humanize(t)}</option>)}
          </select>
        </div>
      )}
    </div>
  );
}

function RelationsTab({ id }: { id: string }) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data, isLoading } = useQuery({ queryKey: ["relations", id], queryFn: () => api.get<Relation[]>(`/usecases/${id}/relations`) });
  const review = useMutation({
    mutationFn: ({ rid, status, type }: { rid: string; status: string; type?: string }) => api.patch(`/usecases/relations/${rid}`, { status, type }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["relations", id] }); qc.invalidateQueries({ queryKey: ["usecase", id] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); },
  });
  const [showStructural, setShowStructural] = useState(false);
  if (isLoading || !data) return <Spinner />;
  const semantic = data.filter((r) => r.type !== "shares_entity");
  const structural = data.filter((r) => r.type === "shares_entity");
  return (
    <div className="space-y-3">
      {semantic.length === 0 && <Empty title="No overlaps or dependencies detected">Run “Re-analyze” after editing, or add a relation manually from another use case.</Empty>}
      {semantic.map((r) => <RelationRow key={r.id} r={r} selfId={id} onReview={isReviewer(user) ? (status, type) => review.mutate({ rid: r.id, status, type }) : undefined} />)}
      {structural.length > 0 && (
        <div>
          <button className="btn-ghost text-xs" onClick={() => setShowStructural(!showStructural)}>{showStructural ? "Hide" : "Show"} {structural.length} shared-entity links</button>
          {showStructural && <div className="space-y-2 mt-2">{structural.map((r) => <RelationRow key={r.id} r={r} selfId={id} />)}</div>}
        </div>
      )}
    </div>
  );
}

function EntitiesTab({ id }: { id: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["entities", id], queryFn: () => api.get<{ entity: { id: string; kind: string; name: string }; field_id: string; role: string; shared_with: number }[]>(`/usecases/${id}/entities`) });
  if (isLoading || !data) return <Spinner />;
  if (data.length === 0) return <Empty title="No entities extracted yet">Fill data sources, systems, teams or models and re-analyze.</Empty>;
  const kinds = ["system", "data", "team", "model"];
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-4">
      {kinds.map((k) => (
        <div key={k} className="card p-4">
          <h3 className="text-sm font-semibold capitalize mb-2">{k}s</h3>
          <ul className="space-y-1.5">
            {data.filter((e) => e.entity.kind === k).map((e) => (
              <li key={e.entity.id + e.field_id} className="flex items-center justify-between text-sm">
                <span>{e.entity.name} <span className="text-[10px] text-slate-400">{e.role}</span></span>
                {e.shared_with > 0 ? <span className="chip bg-indigo-50 text-indigo-800" title="Other use cases using this entity">+{e.shared_with} UC</span> : <span className="text-xs text-slate-300">unique</span>}
              </li>
            ))}
            {data.filter((e) => e.entity.kind === k).length === 0 && <li className="text-xs text-slate-300">—</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}

function HistoryTab({ id }: { id: string }) {
  const decisions = useQuery({ queryKey: ["decisions", id], queryFn: () => api.get<Decision[]>(`/usecases/${id}/decisions`) });
  const versions = useQuery({ queryKey: ["versions", id], queryFn: () => api.get<Version[]>(`/usecases/${id}/versions`), retry: false });
  return (
    <div className="grid lg:grid-cols-2 gap-4">
      <div className="card p-4">
        <h3 className="text-sm font-semibold mb-3">Decisions</h3>
        {decisions.data?.length === 0 && <p className="text-sm text-slate-400">No decision yet.</p>}
        <ol className="space-y-3">
          {decisions.data?.map((d) => (
            <li key={d.id} className="text-sm">
              <div className="flex items-center gap-2"><StatusBadge status={d.from_status} /><ArrowRight className="h-3 w-3 text-slate-400" /><StatusBadge status={d.to_status} /><span className="text-xs text-slate-400 ml-auto">{fmtDate(d.created_at)}</span></div>
              <div className="text-slate-600 mt-1">{d.comment || <span className="italic text-slate-400">no comment</span>} <span className="text-xs text-slate-400">— {d.decided_by?.full_name}</span></div>
            </li>
          ))}
        </ol>
      </div>
      <div className="card p-4">
        <h3 className="text-sm font-semibold mb-3">Versions</h3>
        {versions.isError && <p className="text-sm text-slate-400">Version history is visible to the owner and reviewers only.</p>}
        <ol className="space-y-2">
          {versions.data?.map((v) => (
            <li key={v.id} className="text-sm flex items-center justify-between">
              <span><span className="mono text-slate-500 mr-2">v{v.version}</span>{v.change_note}</span>
              <span className="text-xs text-slate-400">{fmtDate(v.created_at)} · {Object.keys(v.fields).length} fields</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
