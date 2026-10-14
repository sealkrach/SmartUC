import clsx from "clsx";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { humanize, type Template, type TemplateField } from "../lib/api";
import { Spinner } from "./ui";

type Fields = Record<string, unknown>;

export function FieldValue({ field, value }: { field: TemplateField; value: unknown }) {
  if (value == null || value === "" || (Array.isArray(value) && value.length === 0))
    return <span className={clsx("text-sm", field.required ? "text-rose-400 italic" : "text-slate-300 italic")}>{field.required ? "Required — missing" : "—"}</span>;
  if (Array.isArray(value)) {
    const isEnum = field.type === "multi_enum";
    return (
      <div className="flex flex-wrap gap-1">
        {value.map((v, i) => (
          <span key={i} className={clsx("chip", field.type === "entity_list" ? "bg-indigo-50 text-indigo-800 border border-indigo-100" : "bg-slate-100 text-slate-700")}>
            {isEnum ? humanize(String(v)) : String(v)}
          </span>
        ))}
      </div>
    );
  }
  if (field.type === "enum") return <span className="chip bg-slate-100 text-slate-700">{humanize(String(value))}</span>;
  if (field.type === "number") return <span className="text-sm tabular-nums">{Number(value).toLocaleString()}</span>;
  return <p className="text-sm whitespace-pre-wrap leading-relaxed">{String(value)}</p>;
}

export function FieldEditor({ field, value, onChange }: { field: TemplateField; value: unknown; onChange: (v: unknown) => void }) {
  const t = field.type;
  if (t === "enum")
    return (
      <select className="input" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">—</option>
        {field.options?.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
      </select>
    );
  if (t === "multi_enum") {
    const arr = (value as string[]) ?? [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {field.options?.map((o) => {
          const on = arr.includes(o);
          return (
            <button key={o} type="button" onClick={() => onChange(on ? arr.filter((x) => x !== o) : [...arr, o])}
              className={clsx("chip border transition", on ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-slate-600 border-slate-300 hover:border-indigo-400")}>
              {humanize(o)}
            </button>
          );
        })}
      </div>
    );
  }
  if (t === "list" || t === "entity_list")
    return (
      <textarea className="input min-h-[72px]" placeholder="One item per line" value={Array.isArray(value) ? (value as string[]).join("\n") : ((value as string) ?? "")}
        onChange={(e) => onChange(e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
    );
  if (t === "number")
    return <input type="number" className="input" min={field.min} max={field.max} value={(value as number) ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  if (t === "longtext") return <textarea className="input min-h-[96px]" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />;
  if (t === "date") return <input type="date" className="input" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />;
  return <input className="input" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

interface CanvasProps {
  template: Template;
  fields: Fields;
  editing?: boolean;
  onChange?: (fields: Fields) => void;
  onImprove?: (fieldId: string) => Promise<unknown>;
  highlightMissing?: boolean;
  compact?: boolean;
}

/** Renders the whole template as sections. In edit mode every field becomes an editor. */
export function Canvas({ template, fields, editing, onChange, onImprove, highlightMissing, compact }: CanvasProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const sections = template.definition.sections;
  const set = (id: string, v: unknown) => onChange?.({ ...fields, [id]: v });
  const improve = async (id: string) => {
    if (!onImprove) return;
    setBusy(id);
    try { await onImprove(id); } finally { setBusy(null); }
  };
  return (
    <div className={clsx("grid gap-4", compact ? "grid-cols-1" : "grid-cols-1 xl:grid-cols-2")}>
      {sections.map((s) => {
        const filled = s.fields.filter((f) => { const v = fields[f.id]; return v != null && v !== "" && !(Array.isArray(v) && v.length === 0); }).length;
        return (
          <section key={s.id} className="card p-4">
            <header className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-800">{s.title}</h3>
              <span className="text-xs text-slate-400 tabular-nums">{filled}/{s.fields.length}</span>
            </header>
            <dl className="space-y-3">
              {s.fields.map((f) => {
                const v = fields[f.id];
                const missing = f.required && (v == null || v === "" || (Array.isArray(v) && v.length === 0));
                const improvable = !!onImprove && ["text", "longtext", "list"].includes(f.type) && !!v && !f.read_only && f.llm_exposable !== false;
                const editable = editing && !f.read_only;
                return (
                  <div key={f.id} className={clsx("rounded-lg -mx-2 px-2 py-1.5", highlightMissing && missing && "bg-rose-50")}>
                    <dt className="flex items-center justify-between gap-2">
                      <span className="label mb-0.5">{f.label}{f.required && <span className="text-rose-500"> *</span>}{f.read_only && <span className="ml-1 normal-case font-normal text-slate-400" title="Set only through reviewer validation">(validated by reviewer)</span>}{f.llm_exposable === false && <span className="ml-1 normal-case font-normal text-slate-400" title="Never sent to an AI provider">(not shared with AI)</span>}</span>
                      {improvable && (
                        <button className="btn-ghost p-1 text-indigo-600 hover:text-indigo-800" title="Improve with AI" onClick={() => improve(f.id)} disabled={busy === f.id}>
                          {busy === f.id ? <Spinner /> : <Sparkles className="h-3.5 w-3.5" />}
                        </button>
                      )}
                    </dt>
                    <dd>{editable ? <FieldEditor field={f} value={v} onChange={(nv) => set(f.id, nv)} /> : <FieldValue field={f} value={v} />}</dd>
                    {editable && f.ai_hint && <p className="text-[11px] text-slate-400 mt-1">{f.ai_hint}</p>}
                  </div>
                );
              })}
            </dl>
          </section>
        );
      })}
    </div>
  );
}
