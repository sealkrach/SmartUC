import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Empty, PageHeader, Spinner } from "../components/ui";
import { api, type Relation } from "../lib/api";
import { RelationRow } from "./UseCaseDetail";
import { isReviewer, useAuth } from "../lib/auth";

export default function RelationsInbox() {
  const [status, setStatus] = useState<"suggested" | "confirmed" | "dismissed" | "all">("suggested");
  const [type, setType] = useState("");
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data, isLoading } = useQuery({ queryKey: ["relation-inbox", status], queryFn: () => api.get<Relation[]>(`/portfolio/relations?status=${status}`) });
  const review = useMutation({
    mutationFn: ({ rid, s, t }: { rid: string; s: string; t?: string }) => api.patch(`/usecases/relations/${rid}`, { status: s, type: t }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["relation-inbox"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); qc.invalidateQueries({ queryKey: ["usecases"] }); },
  });
  const rows = (data ?? []).filter((r) => r.type !== "shares_entity" && (!type || r.type === type));
  const types = ["duplicate", "overlap", "complementary", "depends_on"];
  return (
    <div>
      <PageHeader title="Relations inbox" subtitle="AI-detected duplicates, overlaps and dependencies waiting for a human decision. Confirmed relations feed the portfolio graph." />
      <div className="flex flex-wrap gap-2 mb-4">
        {(["suggested", "confirmed", "dismissed", "all"] as const).map((s) => (
          <button key={s} onClick={() => setStatus(s)} className={clsx("chip border capitalize", status === s ? "bg-slate-800 text-white border-slate-800" : "bg-white border-slate-300 text-slate-600")}>{s}</button>
        ))}
        <span className="w-px bg-slate-200 mx-1" />
        <button onClick={() => setType("")} className={clsx("chip border", !type ? "bg-slate-800 text-white border-slate-800" : "bg-white border-slate-300 text-slate-600")}>All types</button>
        {types.map((t) => <button key={t} onClick={() => setType(t)} className={clsx("chip border capitalize", type === t ? "bg-slate-800 text-white border-slate-800" : "bg-white border-slate-300 text-slate-600")}>{t.replace("_", " ")}</button>)}
      </div>
      {isLoading ? <Spinner /> : rows.length === 0 ? <Empty title="Nothing here">{status === "suggested" ? "No pending suggestions. Run “Analyze portfolio” from the dashboard to look for new ones." : ""}</Empty> : (
        <div className="space-y-2">{rows.map((r) => <RelationRow key={r.id} r={r} onReview={isReviewer(user) ? (s, t) => review.mutate({ rid: r.id, s, t }) : undefined} />)}</div>
      )}
    </div>
  );
}
