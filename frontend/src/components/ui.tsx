import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { RELATION_LABEL, STATUS_LABEL, TIER_LABEL, humanize } from "../lib/api";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  submitted: "bg-sky-100 text-sky-800",
  qualified: "bg-violet-100 text-violet-800",
  approved: "bg-indigo-100 text-indigo-800",
  in_build: "bg-amber-100 text-amber-800",
  in_production: "bg-emerald-100 text-emerald-800",
  retired: "bg-slate-200 text-slate-600",
  rejected: "bg-rose-100 text-rose-800",
};
export function StatusBadge({ status }: { status: string }) {
  return <span className={clsx("chip", STATUS_COLORS[status] ?? "bg-slate-100 text-slate-700")}>{STATUS_LABEL[status] ?? humanize(status)}</span>;
}

const TIER_COLORS: Record<string, string> = {
  prohibited: "bg-rose-600 text-white",
  high_risk: "bg-rose-100 text-rose-800 border border-rose-200",
  limited_risk: "bg-amber-100 text-amber-800",
  minimal_risk: "bg-emerald-100 text-emerald-800",
  gpai_obligations_only: "bg-sky-100 text-sky-800",
};
export function TierBadge({ tier, suggested }: { tier: string | null | undefined; suggested?: boolean }) {
  if (!tier) return <span className="chip bg-slate-100 text-slate-500 border border-dashed border-slate-300">Unclassified</span>;
  return (
    <span className={clsx("chip", TIER_COLORS[tier] ?? "bg-slate-100", suggested && "opacity-70 border-dashed")} title={suggested ? "AI suggestion, not validated" : "Validated"}>
      {TIER_LABEL[tier] ?? humanize(tier)}{suggested && " (AI)"}
    </span>
  );
}

const REL_COLORS: Record<string, string> = {
  duplicate: "bg-rose-100 text-rose-800",
  overlap: "bg-amber-100 text-amber-800",
  complementary: "bg-emerald-100 text-emerald-800",
  depends_on: "bg-indigo-100 text-indigo-800",
  enables: "bg-indigo-100 text-indigo-800",
  shares_entity: "bg-slate-100 text-slate-700",
};
export const REL_HEX: Record<string, string> = {
  duplicate: "#e11d48", overlap: "#d97706", complementary: "#059669", depends_on: "#4f46e5", enables: "#4f46e5",
  shares_entity: "#94a3b8", uses_entity: "#cbd5e1",
};
export function RelationBadge({ type }: { type: string }) {
  return <span className={clsx("chip", REL_COLORS[type] ?? "bg-slate-100")}>{RELATION_LABEL[type] ?? humanize(type)}</span>;
}

export function Completeness({ value, size = "md" }: { value: number; size?: "sm" | "md" }) {
  const pct = Math.round(value * 100);
  const color = pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className={clsx("flex items-center gap-2", size === "sm" ? "w-24" : "w-40")} title={`${pct}% complete`}>
      <div className="flex-1 h-1.5 rounded-full bg-slate-200 overflow-hidden"><div className={clsx("h-full", color)} style={{ width: `${pct}%` }} /></div>
      <span className="text-xs tabular-nums text-slate-600 w-8 text-right">{pct}%</span>
    </div>
  );
}

export function Score({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value == null) return <span className="text-slate-300 text-xs">—</span>;
  const v = Math.round(value);
  const color = v >= 70 ? "text-emerald-700 bg-emerald-50" : v >= 45 ? "text-amber-700 bg-amber-50" : "text-slate-600 bg-slate-100";
  return <span className={clsx("inline-flex items-baseline gap-1 rounded-md px-1.5 py-0.5 tabular-nums text-xs font-semibold", color)} title={label}>{v}{label && <span className="font-normal text-[10px] opacity-70">{label}</span>}</span>;
}

export function Spinner({ className }: { className?: string }) {
  return <span className={clsx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600", className)} />;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="text-center py-12 text-slate-500">
      <p className="font-medium text-slate-700">{title}</p>
      {children && <div className="mt-2 text-sm">{children}</div>}
    </div>
  );
}

export function Callout({ kind = "info", children }: { kind?: "info" | "warn" | "ok"; children: ReactNode }) {
  const Icon = kind === "warn" ? AlertTriangle : kind === "ok" ? CheckCircle2 : Info;
  const cls = kind === "warn" ? "bg-amber-50 border-amber-200 text-amber-900" : kind === "ok" ? "bg-emerald-50 border-emerald-200 text-emerald-900" : "bg-sky-50 border-sky-200 text-sky-900";
  return <div className={clsx("flex gap-2 items-start rounded-lg border px-3 py-2 text-sm", cls)}><Icon className="h-4 w-4 mt-0.5 shrink-0" /><div className="min-w-0">{children}</div></div>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <div className={clsx("card w-full max-h-[90vh] overflow-auto p-5", wide ? "max-w-4xl" : "max-w-lg")} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">{title}</h3>
          <button className="btn-ghost p-1" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-2xl font-semibold mt-1 tabular-nums">{value}</div>
      {hint && <div className="text-xs text-slate-500 mt-1">{hint}</div>}
    </div>
  );
}

export function fmtDate(s: string | null | undefined) {
  if (!s) return "—";
  return new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
