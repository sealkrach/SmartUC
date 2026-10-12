import { useMutation, useQuery } from "@tanstack/react-query";
import { Save } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Canvas } from "../components/Canvas";
import { Callout, PageHeader, Spinner } from "../components/ui";
import { api, type Template, type UseCase } from "../lib/api";

export default function NewUseCase() {
  const nav = useNavigate();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/templates") });
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const template = templates.data?.find((t) => t.id === (templateId ?? templates.data?.find((x) => x.is_default)?.id));
  const create = useMutation({
    mutationFn: () => api.post<UseCase>("/usecases", { fields, template_id: template?.id }),
    onSuccess: (u) => nav(`/usecases/${u.id}`),
    onError: (e: Error) => setError(e.message),
  });
  if (!template) return <Spinner />;
  return (
    <div>
      <PageHeader title="Blank canvas" subtitle="Fill what you know; the use case is created as a draft and analysed in the background."
        actions={<>
          <select className="input w-auto" value={template.id} onChange={(e) => setTemplateId(e.target.value)}>{templates.data!.map((t) => <option key={t.id} value={t.id}>{t.name} v{t.version}</option>)}</select>
          <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending || !fields.title}><Save className="h-4 w-4" /> Create draft</button>
        </>} />
      {error && <div className="mb-3"><Callout kind="warn">{error}</Callout></div>}
      <Canvas template={template} fields={fields} editing onChange={setFields} highlightMissing />
    </div>
  );
}
