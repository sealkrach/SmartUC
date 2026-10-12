import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, Play, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Callout, PageHeader, Spinner, fmtDate } from "../components/ui";
import { api, type ImportJob, type Template } from "../lib/api";

export default function Import() {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/templates") });
  const jobs = useQuery({ queryKey: ["imports"], queryFn: () => api.get<ImportJob[]>("/imports") });
  const job = useQuery({ queryKey: ["import", jobId], queryFn: () => api.get<ImportJob>(`/imports/${jobId}`), enabled: !!jobId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false) });
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [enrich, setEnrich] = useState(true);
  useEffect(() => { if (job.data) { setMapping(job.data.mapping); setEnrich(job.data.ai_enrich); } }, [job.data?.id]);

  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.post<ImportJob>("/imports", fd); },
    onSuccess: (j) => { setJobId(j.id); setError(null); qc.invalidateQueries({ queryKey: ["imports"] }); },
    onError: (e: Error) => setError(e.message),
  });
  const run = useMutation({
    mutationFn: async () => { await api.put(`/imports/${jobId}/mapping`, { mapping, ai_enrich: enrich }); return api.post<ImportJob>(`/imports/${jobId}/run`); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["import", jobId] }); },
    onError: (e: Error) => setError(e.message),
  });
  useEffect(() => { if (job.data?.status === "done") { qc.invalidateQueries({ queryKey: ["usecases"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); } }, [job.data?.status]);

  const template = templates.data?.find((t) => t.id === job.data?.template_id);
  const fields = template?.definition.sections.flatMap((s) => s.fields) ?? [];
  const j = job.data;

  return (
    <div>
      <PageHeader title="Import use cases" subtitle="Upload an existing inventory (CSV or Excel). The AI proposes a column mapping, you confirm, each row is normalised into the template." />
      {error && <div className="mb-3"><Callout kind="warn">{error}</Callout></div>}

      {!j && (
        <label className="card p-10 flex flex-col items-center justify-center gap-3 border-dashed border-2 cursor-pointer hover:border-indigo-400 transition">
          <Upload className="h-8 w-8 text-indigo-600" />
          <div className="font-medium">{upload.isPending ? "Uploading…" : "Drop or choose a .csv / .xlsx file"}</div>
          <div className="text-xs text-slate-500">First row must contain column headers.</div>
          <input type="file" accept=".csv,.xlsx,.xlsm,.tsv,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        </label>
      )}

      {j && (
        <div className="space-y-4">
          <div className="card p-4 flex items-center gap-3">
            <FileSpreadsheet className="h-6 w-6 text-emerald-600" />
            <div className="flex-1"><div className="font-medium">{j.filename}</div><div className="text-xs text-slate-500">{j.row_count} rows · {j.columns.length} columns · template {template?.name}</div></div>
            <span className="chip bg-slate-100 text-slate-700 capitalize">{j.status}</span>
            <button className="btn-ghost" onClick={() => setJobId(null)}>Start over</button>
          </div>

          {j.status !== "done" && j.status !== "running" && (
            <div className="card p-4">
              <h3 className="text-sm font-semibold mb-1">Column mapping</h3>
              <p className="text-xs text-slate-500 mb-3">Unmapped columns are not lost: with AI enrichment on, their content is fed to the normaliser as free text.</p>
              <table className="table">
                <thead><tr><th>Column</th><th>Sample values</th><th>Template field</th></tr></thead>
                <tbody>
                  {j.columns.map((c) => (
                    <tr key={c}>
                      <td className="font-medium">{c}</td>
                      <td className="text-xs text-slate-500 max-w-md truncate">{j.sample_rows.map((r) => r[c]).filter(Boolean).slice(0, 2).join(" · ")}</td>
                      <td>
                        <select className="input py-1" value={mapping[c] ?? ""} onChange={(e) => setMapping({ ...mapping, [c]: e.target.value || null })}>
                          <option value="">— not mapped —</option>
                          {fields.map((f) => <option key={f.id} value={f.id}>{f.label} ({f.type})</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex items-center justify-between mt-4">
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} /> AI enrichment (normalise text, infer categories, flag open questions) — needs a configured LLM</label>
                <button className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}><Play className="h-4 w-4" /> Import {j.row_count} rows</button>
              </div>
            </div>
          )}

          {j.status === "running" && <div className="card p-4"><div className="flex items-center gap-3 mb-2"><Spinner /> Importing… {j.progress}%</div><div className="h-2 bg-slate-200 rounded-full overflow-hidden"><div className="h-full bg-indigo-600 transition-all" style={{ width: `${j.progress}%` }} /></div></div>}
          {j.status === "done" && (
            <Callout kind="ok">
              Imported {j.created_ids.length} use cases as <b>submitted</b>. Portfolio analysis ran afterwards — check the <Link to="/relations" className="underline">relations inbox</Link>.
              {j.errors.length > 0 && <div className="mt-2 text-rose-700">{j.errors.length} rows failed: {j.errors.map((e) => `row ${e.row}: ${e.error}`).join("; ")}</div>}
            </Callout>
          )}
          {j.status === "failed" && <Callout kind="warn">Import failed: {j.errors.map((e) => e.error).join("; ")}</Callout>}
        </div>
      )}

      {!!jobs.data?.length && !j && (
        <div className="mt-8">
          <h3 className="text-sm font-semibold mb-2">Previous imports</h3>
          <div className="card divide-y divide-slate-100">
            {jobs.data.map((x) => (
              <button key={x.id} onClick={() => setJobId(x.id)} className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 text-sm text-left">
                <span className="flex-1 font-medium truncate">{x.filename}</span><span className="text-xs text-slate-500">{x.row_count} rows</span>
                <span className="chip bg-slate-100 text-slate-700 capitalize">{x.status}</span><span className="text-xs text-slate-400 w-36 text-right">{fmtDate(x.created_at)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
