import { useQuery } from "@tanstack/react-query";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { REL_HEX, Spinner, StatusBadge, TierBadge } from "../components/ui";
import { api, RELATION_LABEL, humanize, type GraphData } from "../lib/api";

/** Tiny force layout — enough for a few hundred nodes, no extra dependency. */
function layout(nodes: GraphData["nodes"], edges: GraphData["edges"]) {
  const pos = new Map<string, { x: number; y: number; vx: number; vy: number }>();
  const R = 90 * Math.sqrt(nodes.length) + 300;
  nodes.forEach((n, i) => {
    const a = (i / nodes.length) * Math.PI * 2;
    pos.set(n.id, { x: Math.cos(a) * R, y: Math.sin(a) * R, vx: 0, vy: 0 });
  });
  const ids = nodes.map((n) => n.id);
  const links = edges.filter((e) => pos.has(e.source) && pos.has(e.target));
  for (let it = 0; it < 260; it++) {
    const k = 1 - it / 260;
    for (let i = 0; i < ids.length; i++) {
      const a = pos.get(ids[i])!;
      for (let j = i + 1; j < ids.length; j++) {
        const b = pos.get(ids[j])!;
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 1;
        const f = (90000 / d2) * k;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
      // gravity to centre
      a.vx -= a.x * 0.002 * k; a.vy -= a.y * 0.002 * k;
    }
    for (const e of links) {
      const a = pos.get(e.source)!, b = pos.get(e.target)!;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const target = e.type === "uses_entity" ? 230 : 380;
      const f = ((d - target) / d) * 0.03 * k;
      a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
    for (const p of pos.values()) { p.x += p.vx; p.y += p.vy; p.vx *= 0.6; p.vy *= 0.6; }
  }
  return pos;
}

const STATUS_BORDER: Record<string, string> = { draft: "border-slate-300", submitted: "border-sky-400", qualified: "border-violet-400", approved: "border-indigo-500", in_build: "border-amber-400", in_production: "border-emerald-500", retired: "border-slate-400", rejected: "border-rose-400" };

function UseCaseNode({ data }: NodeProps) {
  const d = data as GraphData["nodes"][number] & { dim?: boolean };
  return (
    <div className={clsx("rounded-xl bg-white border-2 shadow-sm px-3 py-2 w-56 text-left", STATUS_BORDER[d.status ?? ""] ?? "border-slate-300", d.dim && "opacity-30")}>
      <Handle type="target" position={Position.Top} className="!bg-slate-400 !w-1.5 !h-1.5" />
      <div className="mono text-slate-400 text-[10px]">{d.ref}</div>
      <div className="text-xs font-semibold leading-snug line-clamp-2">{d.label}</div>
      <div className="text-[10px] text-slate-500 mt-0.5 truncate">{d.business_unit}</div>
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400 !w-1.5 !h-1.5" />
    </div>
  );
}
function EntityNode({ data }: NodeProps) {
  const d = data as GraphData["nodes"][number] & { dim?: boolean };
  const color = { system: "bg-indigo-50 border-indigo-200 text-indigo-900", data: "bg-emerald-50 border-emerald-200 text-emerald-900", team: "bg-amber-50 border-amber-200 text-amber-900", model: "bg-fuchsia-50 border-fuchsia-200 text-fuchsia-900" }[d.entity_kind ?? "system"];
  return (
    <div className={clsx("rounded-full border px-3 py-1 text-[11px] font-medium shadow-sm", color, d.dim && "opacity-30")}>
      <Handle type="target" position={Position.Top} className="!opacity-0" />
      <span className="uppercase text-[9px] opacity-60 mr-1">{d.entity_kind}</span>{d.label}
      <Handle type="source" position={Position.Bottom} className="!opacity-0" />
    </div>
  );
}
const nodeTypes = { usecase: UseCaseNode, entity: EntityNode };

export default function Graph() {
  const [showEntities, setShowEntities] = useState(true);
  const [showSuggested, setShowSuggested] = useState(true);
  const [minShared, setMinShared] = useState(2);
  const [selected, setSelected] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["graph", showEntities, showSuggested, minShared],
    queryFn: () => api.get<GraphData>(`/portfolio/graph?include_entities=${showEntities}&include_suggested=${showSuggested}&min_shared=${minShared}`),
  });

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] };
    const pos = layout(data.nodes, data.edges);
    const neighbours = new Set<string>();
    if (selected) { neighbours.add(selected); data.edges.forEach((e) => { if (e.source === selected) neighbours.add(e.target); if (e.target === selected) neighbours.add(e.source); }); }
    const nodes: Node[] = data.nodes.map((n) => ({ id: n.id, type: n.kind, position: pos.get(n.id)!, data: { ...n, dim: selected ? !neighbours.has(n.id) : false } }));
    const edges: Edge[] = data.edges.map((e) => {
      const dim = selected ? !(e.source === selected || e.target === selected) : false;
      const color = REL_HEX[e.type] ?? "#94a3b8";
      return {
        id: e.id, source: e.source, target: e.target, type: "default",
        animated: e.status === "suggested",
        style: { stroke: color, strokeWidth: e.type === "uses_entity" ? 1 : 2, opacity: dim ? 0.08 : e.status === "suggested" ? 0.7 : 1, strokeDasharray: e.status === "suggested" ? "4 3" : undefined },
        label: e.type !== "uses_entity" && !dim ? RELATION_LABEL[e.type] : undefined,
        labelStyle: { fontSize: 10, fill: color, fontWeight: 600 }, labelBgStyle: { fill: "#fff", fillOpacity: 0.9 },
        markerEnd: e.type === "depends_on" ? { type: MarkerType.ArrowClosed, color } : undefined,
      };
    });
    return { nodes, edges };
  }, [data, selected]);

  const sel = data?.nodes.find((n) => n.id === selected);
  const selEdges = data?.edges.filter((e) => e.source === selected || e.target === selected) ?? [];

  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col">
      <div className="flex flex-wrap items-center gap-4 mb-3">
        <div><h1 className="text-xl font-semibold tracking-tight">Portfolio graph</h1><p className="text-sm text-slate-500">Use cases, the entities they share, and the AI-detected relations between them. Click a node to focus.</p></div>
        <span className="flex-1" />
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={showEntities} onChange={(e) => setShowEntities(e.target.checked)} /> Shared entities</label>
        <label className="flex items-center gap-1.5 text-sm">min shared by <input type="number" min={2} max={10} className="input w-16 py-1" value={minShared} onChange={(e) => setMinShared(Number(e.target.value))} /></label>
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={showSuggested} onChange={(e) => setShowSuggested(e.target.checked)} /> Include AI suggestions</label>
      </div>
      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-4 gap-3">
        <div className="lg:col-span-3 card overflow-hidden relative">
          {isLoading ? <div className="p-6"><Spinner /></div> : (
            <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.1} onNodeClick={(_, n) => setSelected(n.id === selected ? null : n.id)} onPaneClick={() => setSelected(null)} proOptions={{ hideAttribution: true }}>
              <Background gap={24} color="#e2e8f0" />
              <Controls />
            </ReactFlow>
          )}
          <div className="absolute bottom-3 left-3 card px-3 py-2 text-[11px] flex flex-wrap gap-3">
            {Object.entries(RELATION_LABEL).filter(([k]) => k !== "shares_entity").map(([k, v]) => <span key={k} className="flex items-center gap-1"><span className="inline-block w-4 h-0.5" style={{ background: REL_HEX[k] }} />{v}</span>)}
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-dashed border-slate-500" />suggested</span>
          </div>
        </div>
        <aside className="card p-4 overflow-auto">
          {!sel ? <p className="text-sm text-slate-500">Select a node to see its connections.</p> : (
            <div>
              {sel.kind === "usecase" ? (
                <>
                  <div className="mono text-xs text-slate-400">{sel.ref}</div>
                  <Link to={`/usecases/${sel.id}`} className="font-semibold hover:text-indigo-700">{sel.label}</Link>
                  <div className="flex gap-1.5 mt-2"><StatusBadge status={sel.status!} /><TierBadge tier={sel.risk_tier} /></div>
                  <div className="text-xs text-slate-500 mt-1">{sel.business_unit} · {humanize(sel.capability)}</div>
                </>
              ) : <><div className="text-xs uppercase text-slate-400">{sel.entity_kind}</div><div className="font-semibold">{sel.label}</div></>}
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mt-4 mb-2">{selEdges.length} connections</h4>
              <ul className="space-y-2">
                {selEdges.map((e) => {
                  const otherId = e.source === sel.id ? e.target : e.source;
                  const other = data!.nodes.find((n) => n.id === otherId);
                  return (
                    <li key={e.id} className="text-sm">
                      <button className="text-left hover:text-indigo-700" onClick={() => setSelected(otherId)}>
                        <span className="text-[10px] font-semibold uppercase mr-1" style={{ color: REL_HEX[e.type] }}>{RELATION_LABEL[e.type] ?? e.type}</span>
                        {other?.ref && <span className="mono text-slate-400 mr-1">{other.ref}</span>}{other?.label}
                      </button>
                      {e.type !== "uses_entity" && <p className="text-xs text-slate-500">{e.explanation}</p>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
