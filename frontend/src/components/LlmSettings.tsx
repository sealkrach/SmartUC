import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Activity, Cpu, Settings2, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api, humanize } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Callout, Modal, Spinner, fmtDate } from "./ui";

interface ProviderInfo { label: string; models: string[]; needs_base: boolean; free_model?: boolean }
interface LlmConfig {
  provider: string; model: string; fast_model: string; api_base: string; has_key: boolean; key_hint: string;
  key_source: "db" | "env" | null; source: "db" | "env"; updated_at: string | null; updated_by: string;
  last_check: { ok: boolean; latency_ms: number; model: string; reason?: string; checked_at: string } | null;
  providers: Record<string, ProviderInfo>;
}
interface TestResult { ok: boolean; latency_ms: number; model: string; reply?: string; reason?: string; checked_at: string }

const MODELS_REASON: Record<string, string> = {
  authentication: "key rejected", not_found: "no models endpoint", unsupported: "not supported for this provider",
  no_key: "no key", invalid_base: "invalid base URL", timeout: "timed out", connection: "cannot reach the provider", error: "provider error",
};
const REASON: Record<string, string> = {
  authentication: "Authentication failed — check the API key", not_found: "Model not found for this provider",
  rate_limited: "Rate limited by the provider", timeout: "Timed out (30 s)", connection: "Cannot reach the provider",
  error: "Provider returned an error (see server logs)",
};

function latencyTone(ms: number) {
  return ms < 1500 ? "text-emerald-700" : ms < 4000 ? "text-amber-700" : "text-rose-700";
}

/** Top-right status chip: provider/model, key status, last measured latency. Opens the settings modal. */
export function LlmStatusButton() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const { data } = useQuery({ queryKey: ["llm-settings"], queryFn: () => api.get<LlmConfig>("/settings/llm"), staleTime: 30_000 });
  const check = data?.last_check;
  const dot = !data ? "bg-slate-300" : !data.has_key ? "bg-rose-500" : check ? (check.ok ? "bg-emerald-500" : "bg-rose-500") : "bg-amber-400";
  const title = !data ? "" : !data.has_key ? "No LLM API key configured" : check ? (check.ok ? `OK · ${check.latency_ms} ms` : `Last check failed: ${REASON[check.reason ?? "error"]}`) : "Key configured — not tested yet";
  return (
    <>
      <button onClick={() => setOpen(true)} title={title}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs shadow-sm hover:border-indigo-400 transition">
        <span className={clsx("h-2 w-2 rounded-full", dot)} />
        <Cpu className="h-3.5 w-3.5 text-slate-500" />
        <span className="font-medium text-slate-700 max-w-[180px] truncate">{data ? `${data.providers[data.provider]?.label ?? data.provider} · ${data.model}` : "LLM"}</span>
        {check?.ok && <span className={clsx("tabular-nums", latencyTone(check.latency_ms))}>{check.latency_ms} ms</span>}
        <Settings2 className="h-3.5 w-3.5 text-slate-400" />
      </button>
      {open && data && <LlmSettingsModal data={data} canEdit={user?.role === "admin"} canTest={user?.role !== "requester"} onClose={() => setOpen(false)} />}
    </>
  );
}

function LlmSettingsModal({ data, canEdit, canTest, onClose }: { data: LlmConfig; canEdit: boolean; canTest: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ provider: data.provider, model: data.model, fast_model: data.fast_model, api_base: data.api_base, api_key: "" });
  const [result, setResult] = useState<TestResult | null>(data.last_check);
  const [error, setError] = useState<string | null>(null);
  const prov = data.providers[form.provider];
  const [models, setModels] = useState<string[] | null>(null);
  const [modelsReason, setModelsReason] = useState<string | null>(null);
  useEffect(() => { setResult(null); }, [form.provider, form.model]);

  const discover = useMutation({
    mutationFn: () => api.post<{ ok: boolean; models: string[]; reason: string | null }>("/settings/llm/models",
      canEdit ? { provider: form.provider, api_base: form.api_base, api_key: form.api_key || null } : {}),
    onSuccess: (r) => { setModels(r.ok ? r.models : []); setModelsReason(r.ok ? null : r.reason ?? "error"); },
    onError: (e: Error) => { setModels([]); setModelsReason(e.message); },
  });
  // Live list from the provider whenever provider/base changes and a key is usable (saved or just typed)
  const keyUsable = !!form.api_key || (data.has_key && form.provider === data.provider) || form.provider === "ollama";
  useEffect(() => {
    setModels(null); setModelsReason(null);
    if (!canTest || !keyUsable || (prov?.needs_base && !form.api_base)) return;
    const t = setTimeout(() => discover.mutate(), form.api_key ? 600 : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.provider, form.api_base, form.api_key, canTest]);
  const suggestions = models && models.length ? models : prov?.models ?? [];

  const dirty = form.provider !== data.provider || form.model !== data.model || form.fast_model !== data.fast_model || form.api_base !== data.api_base || !!form.api_key;
  const save = useMutation({
    mutationFn: () => api.put<LlmConfig>("/settings/llm", { ...form, api_key: form.api_key ? form.api_key : null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["llm-settings"] }); setForm((f) => ({ ...f, api_key: "" })); setError(null); },
    onError: (e: Error) => setError(e.message),
  });
  const clearKey = useMutation({
    mutationFn: () => api.put<LlmConfig>("/settings/llm", { ...form, api_key: "" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-settings"] }),
  });
  const test = useMutation({
    mutationFn: (fast: boolean) => api.post<TestResult>("/settings/llm/test", canEdit ? { ...form, api_key: form.api_key || null, fast } : { fast }),
    onSuccess: (r) => { setResult(r); setError(null); qc.invalidateQueries({ queryKey: ["llm-settings"] }); },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Modal title="LLM configuration" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-slate-600">Provider, models and API key used for intake, normalisation, the overlap judge and EU AI Act suggestions. The key is encrypted at rest and never shown again.</p>
        {!canEdit && <Callout>Read-only: only administrators can change these settings.</Callout>}

        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label className="label">Provider</label>
            <select className="input" disabled={!canEdit} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value, model: data.providers[e.target.value]?.models[0] ?? "", fast_model: "" })}>
              {Object.entries(data.providers).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Model (main)</label>
            <input className="input" list="llm-models" disabled={!canEdit} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder={form.provider === "custom" ? "provider/model" : form.provider === "openai_compatible" ? "e.g. meta-llama/Llama-3.1-70B-Instruct" : "model name"} />
            {form.provider === "openai_compatible" && <p className="text-[11px] text-slate-500 mt-1">Exact model id as served by the endpoint (<span className="mono">GET /v1/models</span>). Sent to LiteLLM as <span className="mono">openai/&lt;model&gt;</span>.</p>}
          </div>
          <div>
            <label className="label">Fast model (questions, risk, mapping)</label>
            <input className="input" list="llm-models" disabled={!canEdit} value={form.fast_model} onChange={(e) => setForm({ ...form, fast_model: e.target.value })} placeholder="same as main" />
          </div>
          <datalist id="llm-models">{suggestions.map((m) => <option key={m} value={m} />)}</datalist>
          <div className="col-span-2 -mt-1 flex items-center gap-2 text-[11px] text-slate-500">
            {discover.isPending ? <><Spinner /> Fetching models from the provider…</>
              : models && models.length ? <><span className="chip bg-emerald-100 text-emerald-800">{models.length} models</span> available to this key — type to filter the list</>
              : modelsReason ? <span className="text-amber-700">Model list unavailable ({MODELS_REASON[modelsReason] ?? modelsReason}); showing defaults</span>
              : keyUsable ? <span>Static suggestions</span> : <span>Enter a key to list the models it can use</span>}
            {canTest && keyUsable && <button type="button" className="underline hover:text-slate-800" onClick={() => discover.mutate()} disabled={discover.isPending}>Refresh</button>}
          </div>
          {prov?.needs_base && (
            <div className="col-span-2">
              <label className="label">API base URL</label>
              <input className="input" disabled={!canEdit} value={form.api_base} onChange={(e) => setForm({ ...form, api_base: e.target.value })} placeholder={form.provider === "ollama" ? "http://host.docker.internal:11434" : form.provider === "openai_compatible" ? "https://openrouter.ai/api/v1  ·  http://host.docker.internal:8000/v1" : "https://<resource>.openai.azure.com"} />
              {form.provider === "openai_compatible" && <p className="text-[11px] text-slate-500 mt-1">Base of the OpenAI-style API, usually ending in <span className="mono">/v1</span>. Local servers (vLLM, LM Studio) are reached from the backend container via <span className="mono">host.docker.internal</span>.</p>}
            </div>
          )}
          <div className="col-span-2">
            <label className="label">API key</label>
            <div className="flex gap-2">
              <input className="input" type="password" autoComplete="off" disabled={!canEdit} value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder={data.has_key ? `configured (${data.key_hint}, from ${data.key_source === "db" ? "settings" : ".env"}) — leave empty to keep` : "paste a key"} />
              {canEdit && data.key_source === "db" && <button className="btn-secondary" type="button" onClick={() => clearKey.mutate()}>Clear</button>}
            </div>
            {!data.has_key && <p className="text-xs text-rose-600 mt-1">No key: AI features are disabled until one is configured.</p>}
          </div>
        </div>

        <div className="card p-3 bg-slate-50">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-medium"><Activity className="h-4 w-4" /> Connection check</div>
            {canTest && <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => test.mutate(false)} disabled={test.isPending}>{test.isPending ? <Spinner /> : null} Test main</button>
              <button className="btn-secondary" onClick={() => test.mutate(true)} disabled={test.isPending}>Test fast</button>
            </div>}
          </div>
          {result ? (
            <div className="mt-2 text-sm">
              <span className={clsx("chip", result.ok ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800")}>{result.ok ? "OK" : "Failed"}</span>
              <span className={clsx("ml-2 font-semibold tabular-nums", latencyTone(result.latency_ms))}>{result.latency_ms} ms</span>
              <span className="ml-2 mono text-slate-500">{result.model}</span>
              {!result.ok && <div className="text-rose-700 mt-1">{REASON[result.reason ?? "error"]}</div>}
              <div className="text-[11px] text-slate-400 mt-1">{fmtDate(result.checked_at)}{result.reply ? ` · reply: “${result.reply}”` : ""}</div>
            </div>
          ) : <p className="text-xs text-slate-500 mt-2">Sends a 5-token prompt and measures the round trip{dirty ? " with the values above (unsaved)" : ""}.</p>}
        </div>

        {error && <Callout kind="warn">{error}</Callout>}
        <div className="flex items-center justify-between">
          <div className="text-[11px] text-slate-400 flex items-center gap-1"><ShieldCheck className="h-3 w-3" /> {data.source === "db" ? `Saved ${fmtDate(data.updated_at)} by ${data.updated_by}` : "Using .env configuration"} · {humanize(data.provider)}</div>
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={onClose}>Close</button>
            {canEdit && <button className="btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save</button>}
          </div>
        </div>
      </div>
    </Modal>
  );
}
