import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, CheckCircle2, ClipboardPaste, History, Send, Sparkles, User as UserIcon, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Canvas } from "../components/Canvas";
import { Callout, Completeness, Modal, PageHeader, Spinner, fmtDate } from "../components/ui";
import { api, humanize, type IntakeSession, type Template } from "../lib/api";

export default function Intake() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/templates") });
  const sessions = useQuery({ queryKey: ["intakes"], queryFn: () => api.get<IntakeSession[]>("/intake"), enabled: !id });
  const session = useQuery({ queryKey: ["intake", id], queryFn: () => api.get<IntakeSession>(`/intake/${id}`), enabled: !!id });
  const template = templates.data?.find((t) => t.id === session.data?.template_id) ?? templates.data?.find((t) => t.is_default);

  useEffect(() => { if (session.data) setDraft(session.data.draft); }, [session.data]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [session.data?.messages.length]);

  const start = useMutation({
    mutationFn: (initial?: string) => api.post<IntakeSession>("/intake", { initial_text: initial || null, template_id: template?.id }),
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["intakes"] }); nav(`/intake/${s.id}`); },
    onError: (e: Error) => setError(e.message),
  });
  const send = useMutation({
    mutationFn: (content: string) => api.post<IntakeSession>(`/intake/${id}/messages`, { content }),
    onSuccess: (s) => { qc.setQueryData(["intake", id], s); setText(""); setError(null); },
    onError: (e: Error) => setError(e.message),
  });
  const saveDraft = useMutation({
    mutationFn: () => api.put<IntakeSession>(`/intake/${id}/draft`, { draft }),
    onSuccess: (s) => { qc.setQueryData(["intake", id], s); setEditMode(false); },
    onError: (e: Error) => setError(e.message),
  });
  const convert = useMutation({
    mutationFn: (submit: boolean) => api.post<IntakeSession>(`/intake/${id}/convert?submit=${submit}`),
    onSuccess: (s) => { qc.invalidateQueries({ queryKey: ["usecases"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); nav(`/usecases/${s.use_case_id}`); },
    onError: (e: Error) => setError(e.message),
  });

  // ---------------------------------------------------------------- landing: new or resume
  if (!id) {
    return (
      <div>
        <PageHeader title="New use case" subtitle="Describe your idea in plain words — the assistant structures it into the canvas and asks for what's missing." />
        {error && <div className="mb-3"><Callout kind="warn">{error}</Callout></div>}
        <div className="grid md:grid-cols-3 gap-4">
          <button onClick={() => start.mutate(undefined)} disabled={start.isPending} className="card p-5 text-left hover:border-indigo-400 hover:shadow transition group">
            <Sparkles className="h-6 w-6 text-indigo-600 mb-3" />
            <div className="font-semibold group-hover:text-indigo-700">Guided interview</div>
            <p className="text-sm text-slate-500 mt-1">Chat with the intake assistant. Best when the idea is still fuzzy.</p>
          </button>
          <button onClick={() => setPasteOpen(true)} className="card p-5 text-left hover:border-indigo-400 hover:shadow transition group">
            <ClipboardPaste className="h-6 w-6 text-indigo-600 mb-3" />
            <div className="font-semibold group-hover:text-indigo-700">Paste a brief</div>
            <p className="text-sm text-slate-500 mt-1">Email, slide notes, a one-pager — the AI normalises it, then asks only for the gaps.</p>
          </button>
          <Link to="/usecases/new" className="card p-5 text-left hover:border-indigo-400 hover:shadow transition group">
            <Wand2 className="h-6 w-6 text-indigo-600 mb-3" />
            <div className="font-semibold group-hover:text-indigo-700">Blank canvas</div>
            <p className="text-sm text-slate-500 mt-1">Fill the template yourself; AI polish available per field.</p>
          </Link>
        </div>
        {template && <p className="text-xs text-slate-400 mt-3">Template: {template.name} v{template.version}</p>}

        {!!sessions.data?.length && (
          <div className="mt-8">
            <h3 className="text-sm font-semibold mb-2 flex items-center gap-2"><History className="h-4 w-4" /> Recent intakes</h3>
            <div className="card divide-y divide-slate-100">
              {sessions.data.map((s) => (
                <Link key={s.id} to={s.phase === "converted" && s.use_case_id ? `/usecases/${s.use_case_id}` : `/intake/${s.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 text-sm">
                  <span className="flex-1 truncate font-medium">{(s.draft.title as string) || <span className="text-slate-400 italic">Untitled intake</span>}</span>
                  <Completeness value={s.completeness} size="sm" />
                  <span className={clsx("chip", s.phase === "converted" ? "bg-emerald-100 text-emerald-800" : s.phase === "ready" ? "bg-indigo-100 text-indigo-800" : "bg-slate-100 text-slate-600")}>{s.phase}</span>
                  <span className="text-xs text-slate-400 w-36 text-right">{fmtDate(s.updated_at)}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
        {pasteOpen && <PasteModal onClose={() => setPasteOpen(false)} onSubmit={(t) => { setPasteOpen(false); start.mutate(t); }} busy={start.isPending} />}
      </div>
    );
  }

  // ---------------------------------------------------------------- conversation
  if (session.isLoading || !session.data || !template) return <Spinner />;
  const s = session.data;
  const ready = s.phase === "ready" || (s.missing_required.length === 0 && s.completeness >= 0.5);

  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-xs text-slate-500"><Link to="/intake" className="hover:underline">New use case</Link> / interview</div>
          <h1 className="text-lg font-semibold">{(s.draft.title as string) || "Untitled use case"}</h1>
        </div>
        <div className="flex items-center gap-3">
          <Completeness value={s.completeness} />
          {s.missing_required.length > 0 ? <span className="text-xs text-rose-600">{s.missing_required.length} required missing</span> : <span className="text-xs text-emerald-700 flex items-center gap-1"><CheckCircle2 className="h-3.5 w-3.5" /> required complete</span>}
          <button className="btn-secondary" disabled={convert.isPending} onClick={() => convert.mutate(false)}>Save as draft</button>
          <button className="btn-primary" disabled={!ready || convert.isPending} title={ready ? "Create and submit for qualification" : "Fill required fields first"} onClick={() => convert.mutate(true)}>Create & submit</button>
        </div>
      </div>
      {error && <div className="mb-3"><Callout kind="warn">{error}</Callout></div>}

      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2 card flex flex-col min-h-0">
          <div className="flex-1 overflow-auto p-4 space-y-3">
            {s.messages.map((m, i) => (
              <div key={i} className={clsx("flex gap-2", m.role === "user" && "flex-row-reverse")}>
                <div className={clsx("h-7 w-7 rounded-full grid place-items-center shrink-0", m.role === "user" ? "bg-slate-200" : "bg-indigo-100 text-indigo-700")}>{m.role === "user" ? <UserIcon className="h-4 w-4" /> : <Bot className="h-4 w-4" />}</div>
                <div className={clsx("rounded-xl px-3 py-2 text-sm max-w-[85%] whitespace-pre-wrap leading-relaxed", m.role === "user" ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-800")}>{m.content}</div>
              </div>
            ))}
            {send.isPending && <div className="flex gap-2"><div className="h-7 w-7 rounded-full bg-indigo-100 grid place-items-center"><Bot className="h-4 w-4 text-indigo-700" /></div><div className="rounded-xl bg-slate-100 px-3 py-2"><Spinner /></div></div>}
            <div ref={bottomRef} />
          </div>
          <form className="border-t border-slate-200 p-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(text.trim()); }}>
            <textarea className="input min-h-[44px] max-h-40" rows={2} placeholder="Answer, paste text, or say “that's all” to wrap up…" value={text} disabled={s.phase === "converted" || send.isPending}
              onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (text.trim()) send.mutate(text.trim()); } }} />
            <button className="btn-primary self-end" disabled={!text.trim() || send.isPending}><Send className="h-4 w-4" /></button>
          </form>
        </div>

        <div className="lg:col-span-3 min-h-0 overflow-auto">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-slate-600">Live canvas — <span className="text-slate-400">{template.name}</span></div>
            {editMode ? (
              <div className="flex gap-2"><button className="btn-ghost" onClick={() => { setEditMode(false); setDraft(s.draft); }}>Cancel</button><button className="btn-primary" onClick={() => saveDraft.mutate()} disabled={saveDraft.isPending}>Save edits</button></div>
            ) : <button className="btn-secondary" onClick={() => setEditMode(true)}>Edit fields</button>}
          </div>
          {s.assumptions.length > 0 && <div className="mb-3"><Callout>Assumptions to confirm: <ul className="list-disc ml-4 mt-1">{s.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></Callout></div>}
          {s.missing_required.length > 0 && <p className="text-xs text-slate-500 mb-2">Still required: {s.missing_required.map(humanize).join(", ")}</p>}
          <Canvas template={template} fields={editMode ? draft : s.draft} editing={editMode} onChange={setDraft} highlightMissing compact />
        </div>
      </div>
    </div>
  );
}

function PasteModal({ onClose, onSubmit, busy }: { onClose: () => void; onSubmit: (t: string) => void; busy: boolean }) {
  const [t, setT] = useState("");
  return (
    <Modal title="Paste a brief" onClose={onClose} wide>
      <p className="text-sm text-slate-500 mb-2">Any language, any format. The assistant extracts what it can and asks about the rest.</p>
      <textarea className="input min-h-[260px] font-mono text-xs" value={t} onChange={(e) => setT(e.target.value)} placeholder="Subject: idea for AI in accounts payable…" autoFocus />
      <div className="flex justify-end gap-2 mt-3"><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={!t.trim() || busy} onClick={() => onSubmit(t)}>{busy ? <Spinner /> : <Sparkles className="h-4 w-4" />} Normalise</button></div>
    </Modal>
  );
}
