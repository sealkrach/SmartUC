const BASE = (import.meta.env.VITE_API_URL as string | undefined) || "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken() {
  return localStorage.getItem("uch_token");
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem("uch_token", t);
  else localStorage.removeItem("uch_token");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (init.body && !(init.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    if (!location.pathname.startsWith("/login")) location.href = "/login";
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  return (ct.includes("json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(p: string, body: unknown) => request<T>(p, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(p: string, body: unknown) => request<T>(p, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(p: string) => request<T>(p, { method: "DELETE" }),
};

export async function logout() {
  try { await request("/auth/logout", { method: "POST" }); } catch { /* token may already be invalid */ }
  setToken(null);
}

export async function guestLogin() {
  const res = await fetch(`${BASE}/demo/guest-login`, { method: "POST" });
  if (!res.ok) throw new ApiError(res.status, "The public demo is not available right now");
  const j = await res.json();
  setToken(j.access_token);
}

export async function login(email: string, password: string) {
  const form = new URLSearchParams({ username: email, password });
  const res = await fetch(`${BASE}/auth/login`, { method: "POST", body: form });
  if (!res.ok) throw new ApiError(res.status, "Incorrect email or password");
  const j = await res.json();
  setToken(j.access_token);
}

// ------------------------------------------------------------------ types

export type Role = "requester" | "reviewer" | "admin";
export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; must_change_password?: boolean; is_demo_guest?: boolean }

export interface TemplateField {
  id: string; label: string; type: string; required?: boolean; weight?: number; options?: string[];
  ai_hint?: string; entity_kind?: string; min?: number; max?: number; section: string;
  read_only?: boolean; llm_exposable?: boolean;
}
export interface TemplateSection { id: string; title: string; fields: TemplateField[] }
export interface Template {
  id: string; key: string; version: number; name: string; description: string; is_default: boolean;
  definition: { sections: TemplateSection[]; submission_min_completeness?: number; [k: string]: unknown };
}

export interface UseCaseListItem {
  id: string; ref: string; title: string; summary: string; status: string; business_unit: string;
  completeness: number; value_score: number | null; feasibility_score: number | null; priority_score: number | null;
  risk_tier: string | null; risk_stale?: boolean; tags: string[]; source: string; owner: User | null; ai_capability: string | null;
  suggested_relations: number; updated_at: string; created_at: string;
}
export interface UseCase extends UseCaseListItem {
  template_id: string; fields: Record<string, unknown>; missing_required: string[];
  ai_suggestions: Record<string, unknown>; last_analyzed_at: string | null;
  allowed_transitions: { to: string; gate: string | null; min_role: string }[];
}
export interface Relation {
  id: string; source_id: string; target_id: string; type: string; status: string; confidence: number;
  similarity: number | null; explanation: string; suggested_action: string; origin: string;
  entity: { id: string; kind: string; name: string } | null; created_at: string;
  source_ref: string | null; source_title: string | null; target_ref: string | null; target_title: string | null;
}
export interface Decision { id: string; from_status: string; to_status: string; comment: string; decided_by: User | null; created_at: string }
export interface Version { id: string; version: number; fields: Record<string, unknown>; change_note: string; created_at: string }
export interface IntakeSession {
  id: string; template_id: string; messages: { role: string; content: string; ts: string }[]; draft: Record<string, unknown>;
  completeness: number; missing_required: string[]; assumptions: string[]; phase: string; use_case_id: string | null; updated_at: string;
}
export interface ImportJob {
  id: string; filename: string; columns: string[]; sample_rows: Record<string, string>[]; row_count: number;
  mapping: Record<string, string | null>; ai_enrich: boolean; status: string; progress: number;
  created_ids: string[]; errors: { row: number; error: string }[]; template_id: string; created_at: string;
}
export interface GraphData {
  nodes: { id: string; kind: "usecase" | "entity"; label: string; ref?: string; status?: string; risk_tier?: string | null;
    business_unit?: string; priority?: number | null; capability?: string | null; entity_kind?: string }[];
  edges: { id: string; source: string; target: string; type: string; status: string; confidence: number; explanation: string; role?: string }[];
}

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft", submitted: "Submitted", qualified: "Qualified", approved: "Approved", in_build: "In build",
  in_production: "In production", retired: "Retired", rejected: "Rejected",
};
export const STATUS_ORDER = ["draft", "submitted", "qualified", "approved", "in_build", "in_production", "retired", "rejected"];
export const RELATION_LABEL: Record<string, string> = {
  duplicate: "Duplicate", overlap: "Overlap", complementary: "Complementary", depends_on: "Depends on",
  enables: "Enables", shares_entity: "Shares entity", uses_entity: "Uses",
};
export const TIER_LABEL: Record<string, string> = {
  prohibited: "Prohibited", high_risk: "High risk", limited_risk: "Limited risk", minimal_risk: "Minimal risk",
  gpai_obligations_only: "GPAI only", unclassified: "Unclassified",
};
export const humanize = (s: string | null | undefined) =>
  (s ?? "").toString().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
