import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plug, Trash2 } from "lucide-react";
import { useState } from "react";
import { Callout, PageHeader, Spinner, fmtDate } from "../components/ui";
import { api } from "../lib/api";

interface ApiKey { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null }

const TOOLS: [string, string][] = [
  ["list_use_cases / get_use_case / get_history", "read the registry"],
  ["search_similar", "semantic search before creating anything — the anti-duplicate reflex"],
  ["normalize_text / create_use_case / update_use_case", "turn free text into a canvas and register it"],
  ["start_intake / continue_intake / convert_intake", "drive the guided interview from any chat client"],
  ["transition_use_case / suggest_risk / validate_risk", "governance gates and EU AI Act tier"],
  ["list_relations / review_relation / add_relation", "duplicates, overlaps, dependencies"],
  ["analyze_use_case / analyze_portfolio / portfolio_dashboard / list_entities", "portfolio intelligence"],
  ["list_templates / get_template", "field ids and allowed values"],
];

export default function McpApi() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<ApiKey[]>("/users/me/api-keys") });
  const [name, setName] = useState("Claude Desktop");
  const [created, setCreated] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.post<ApiKey & { key: string }>("/users/me/api-keys", { name }),
    onSuccess: (k) => { setCreated(k.key); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
  });
  const revoke = useMutation({ mutationFn: (id: string) => api.del(`/users/me/api-keys/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }) });
  const base = `${location.origin}/mcp`;
  const keyShown = created ?? "<your-api-key>";
  const copy = (t: string) => navigator.clipboard?.writeText(t);

  return (
    <div>
      <PageHeader title="MCP & API" subtitle="Connect Claude, Cursor or your own agents to this registry through the Model Context Protocol. REST API docs are at /docs." />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <div className="card p-4">
            <h3 className="text-sm font-semibold flex items-center gap-2 mb-1"><Plug className="h-4 w-4" /> MCP endpoint</h3>
            <p className="text-sm text-slate-600 mb-3">Streamable HTTP transport, authenticated with a personal API key. Your role (requester / reviewer / admin) applies to every tool call.</p>
            <div className="flex items-center gap-2 mb-4"><code className="mono bg-slate-100 rounded px-2 py-1 flex-1">{base}</code><button className="btn-ghost" onClick={() => copy(base)}><Copy className="h-4 w-4" /></button></div>

            {created && <div className="mb-4"><Callout kind="ok">Key created — copy it now, it will not be shown again: <code className="mono break-all">{created}</code> <button className="btn-ghost p-1 ml-1" onClick={() => copy(created)}><Copy className="h-3.5 w-3.5" /></button></Callout></div>}

            <h4 className="label">Claude Code</h4>
            <pre className="mono bg-slate-900 text-slate-100 rounded-lg p-3 overflow-auto mb-3">{`claude mcp add --transport http usecase-hub ${base} \\
  --header "Authorization: Bearer ${keyShown}"`}</pre>
            <h4 className="label">Claude Desktop / any client with a JSON config (via mcp-remote)</h4>
            <pre className="mono bg-slate-900 text-slate-100 rounded-lg p-3 overflow-auto mb-3">{`{
  "mcpServers": {
    "usecase-hub": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "${base}", "--header", "Authorization:Bearer ${keyShown}"]
    }
  }
}`}</pre>
            <h4 className="label">curl (JSON-RPC)</h4>
            <pre className="mono bg-slate-900 text-slate-100 rounded-lg p-3 overflow-auto">{`curl -s ${base} -H "Authorization: Bearer ${keyShown}" \\
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'`}</pre>
          </div>
          <div className="card p-4">
            <h3 className="text-sm font-semibold mb-2">Tools exposed</h3>
            <table className="table">
              <tbody>{TOOLS.map(([t, d]) => <tr key={t}><td className="mono text-indigo-800 whitespace-nowrap">{t}</td><td className="text-slate-600">{d}</td></tr>)}</tbody>
            </table>
            <p className="text-xs text-slate-500 mt-3">Resources: <span className="mono">usecase://UC-0001</span>, <span className="mono">template://ai-usecase-canvas</span>. Prompts: <span className="mono">qualify_use_case</span>, <span className="mono">register_from_brief</span>.</p>
          </div>
        </div>
        <div className="space-y-4">
          <form className="card p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <h3 className="text-sm font-semibold flex items-center gap-2"><KeyRound className="h-4 w-4" /> New API key</h3>
            <div><label className="label">Label</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Claude Desktop, CI, LangGraph agent…" /></div>
            <button className="btn-primary w-full justify-center" disabled={create.isPending}>Create key</button>
          </form>
          <div className="card p-4">
            <h3 className="text-sm font-semibold mb-2">Your keys</h3>
            {isLoading ? <Spinner /> : data?.length === 0 ? <p className="text-sm text-slate-400">No key yet.</p> : (
              <ul className="divide-y divide-slate-100">
                {data?.map((k) => (
                  <li key={k.id} className="py-2 flex items-center gap-2 text-sm">
                    <div className="flex-1 min-w-0"><div className="font-medium truncate">{k.name}</div><div className="text-xs text-slate-500 mono">{k.prefix}… · created {fmtDate(k.created_at)}{k.last_used_at && ` · used ${fmtDate(k.last_used_at)}`}</div></div>
                    <button className="btn-ghost text-rose-600 p-1" title="Revoke" onClick={() => revoke.mutate(k.id)}><Trash2 className="h-4 w-4" /></button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
