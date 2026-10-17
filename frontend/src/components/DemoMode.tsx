import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Callout, Modal, Spinner, fmtDate } from "./ui";

interface DemoStatus { active: boolean; count: number; available: number; loaded_at?: string; loaded_by?: string }

export function useDemoStatus() {
  return useQuery({ queryKey: ["demo"], queryFn: () => api.get<DemoStatus>("/demo"), staleTime: 30_000 });
}

function useDemoActions() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries();
  const load = useMutation({ mutationFn: () => api.post<DemoStatus>("/demo/load"), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.post<DemoStatus>("/demo/remove"), onSuccess: refresh });
  return { load, remove };
}

/** Thin amber banner shown to everyone while sample data is loaded. */
export function DemoBanner() {
  const { user } = useAuth();
  const { data } = useDemoStatus();
  const { remove } = useDemoActions();
  const [confirm, setConfirm] = useState(false);
  if (!data?.active) return null;
  return (
    <>
      <div className="flex items-center gap-3 bg-amber-50 border-b border-amber-200 px-6 py-1.5 text-xs text-amber-900">
        <FlaskConical className="h-3.5 w-3.5" />
        <span><b>Demo mode</b> — {data.count} sample use cases are loaded (tag <span className="mono">demo</span>). They are fictional and will be removed when demo mode is turned off.</span>
        <span className="flex-1" />
        {user?.role === "admin" && <button className="underline hover:text-amber-700" onClick={() => setConfirm(true)}>Remove demo data</button>}
      </div>
      {confirm && (
        <Modal title="Remove demo data" onClose={() => setConfirm(false)}>
          <p className="text-sm text-slate-600">This deletes the {data.count} sample use cases, their relations, versions and risk validations. Real use cases and the audit trail are kept.</p>
          <div className="flex justify-end gap-2 mt-4">
            <button className="btn-secondary" onClick={() => setConfirm(false)}>Cancel</button>
            <button className="btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(undefined, { onSuccess: () => setConfirm(false) })}>{remove.isPending ? <Spinner /> : <Trash2 className="h-4 w-4" />} Remove</button>
          </div>
        </Modal>
      )}
    </>
  );
}

/** Admin card: load / remove the sample portfolio. */
export function DemoModeCard() {
  const { data, isLoading } = useDemoStatus();
  const { load, remove } = useDemoActions();
  const err = (load.error || remove.error) as Error | null;
  return (
    <div className="card p-4 space-y-3">
      <h3 className="text-sm font-semibold flex items-center gap-2"><FlaskConical className="h-4 w-4" /> Demo mode</h3>
      <p className="text-sm text-slate-600">Loads {data?.available ?? 26} fictional banking use cases with deliberate duplicates and dependencies, validated risk tiers and a decision history — handy for a walkthrough or to try the overlap analysis. No LLM key needed; with one, run “Analyze portfolio” afterwards.</p>
      {isLoading ? <Spinner /> : data?.active ? (
        <>
          <Callout kind="ok">Active — {data.count} sample use cases, loaded {fmtDate(data.loaded_at)} by {data.loaded_by || "startup seed"}.</Callout>
          <button className="btn-secondary text-rose-700" disabled={remove.isPending} onClick={() => remove.mutate()}><Trash2 className="h-4 w-4" /> Remove demo data</button>
        </>
      ) : (
        <button className="btn-primary" disabled={load.isPending} onClick={() => load.mutate()}>{load.isPending ? <Spinner /> : <FlaskConical className="h-4 w-4" />} Load demo data</button>
      )}
      {err && <Callout kind="warn">{err.message}</Callout>}
    </div>
  );
}
