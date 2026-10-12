import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Star } from "lucide-react";
import { useState } from "react";
import { Callout, PageHeader, Spinner } from "../components/ui";
import { api, humanize, type Template } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Templates() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/templates") });
  const [sel, setSel] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const t = data?.find((x) => x.id === sel) ?? data?.[0];
  const yaml = useQuery({ queryKey: ["template-yaml", t?.id], queryFn: () => api.get<string>(`/templates/${t!.id}/yaml`), enabled: !!t && showYaml });
  const sync = useMutation({ mutationFn: () => api.post("/templates/sync"), onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }) });
  const setDefault = useMutation({ mutationFn: (id: string) => api.post(`/templates/${id}/default`), onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }) });
  if (isLoading || !t) return <Spinner />;
  const fields = t.definition.sections.flatMap((s) => s.fields);
  return (
    <div>
      <PageHeader title="Templates" subtitle="Templates are versioned YAML files in backend/app/templates. Add a file, bump the version, click Sync."
        actions={user?.role === "admin" && <button className="btn-secondary" onClick={() => sync.mutate()} disabled={sync.isPending}><RefreshCw className="h-4 w-4" /> Sync from disk</button>} />
      <div className="grid lg:grid-cols-4 gap-4">
        <div className="card divide-y divide-slate-100">
          {data!.map((x) => (
            <button key={x.id} onClick={() => setSel(x.id)} className={`w-full text-left px-4 py-3 hover:bg-slate-50 ${x.id === t.id ? "bg-indigo-50" : ""}`}>
              <div className="flex items-center gap-2 font-medium text-sm">{x.name} {x.is_default && <Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500" />}</div>
              <div className="text-xs text-slate-500 mono">{x.key} · v{x.version}</div>
            </button>
          ))}
        </div>
        <div className="lg:col-span-3 space-y-4">
          <div className="card p-4">
            <div className="flex items-start justify-between gap-3">
              <div><h2 className="font-semibold">{t.name} <span className="text-slate-400 font-normal">v{t.version}</span></h2><p className="text-sm text-slate-600 mt-1">{t.description}</p></div>
              <div className="flex gap-2">
                {!t.is_default && user?.role === "admin" && <button className="btn-secondary" onClick={() => setDefault.mutate(t.id)}><Star className="h-4 w-4" /> Make default</button>}
                <button className="btn-secondary" onClick={() => setShowYaml(!showYaml)}>{showYaml ? "Hide" : "Show"} YAML</button>
              </div>
            </div>
            <div className="text-xs text-slate-500 mt-2">{t.definition.sections.length} sections · {fields.length} fields · {fields.filter((f) => f.required).length} required · submission needs ≥ {Math.round(((t.definition.submission_min_completeness as number) ?? 0) * 100)}% completeness</div>
          </div>
          {showYaml && <pre className="card p-4 text-xs font-mono overflow-auto max-h-[60vh] whitespace-pre">{yaml.data ?? "…"}</pre>}
          {!showYaml && t.definition.sections.map((s) => (
            <div key={s.id} className="card p-4">
              <h3 className="text-sm font-semibold mb-2">{s.title}</h3>
              <table className="table">
                <thead><tr><th>Field</th><th>Type</th><th>Req.</th><th>Weight</th><th>Options / hint</th></tr></thead>
                <tbody>
                  {s.fields.map((f) => (
                    <tr key={f.id}>
                      <td><div className="font-medium">{f.label}</div><div className="mono text-slate-400">{f.id}</div></td>
                      <td><span className="chip bg-slate-100 text-slate-700">{f.type}{f.entity_kind && ` · ${f.entity_kind}`}</span></td>
                      <td>{f.required ? "✓" : ""}</td><td className="tabular-nums">{f.weight ?? 1}</td>
                      <td className="text-xs text-slate-500 max-w-md">{f.options ? f.options.map(humanize).join(", ") : f.ai_hint}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <Callout>To add your own template (e.g. your company's standard sheet), drop a YAML next to <span className="mono">default.yaml</span> with a new <span className="mono">key</span> or a higher <span className="mono">version</span>, then Sync. Existing use cases keep the template version they were created with.</Callout>
        </div>
      </div>
    </div>
  );
}
