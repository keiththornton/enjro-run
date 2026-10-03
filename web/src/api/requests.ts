import { api } from "./client";
import type { RequestDetail, RequestRow } from "./types";

export function listRequests(params: { limit?: number; before?: number; model_id?: string; status?: string }) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  return api<{ data: RequestRow[] }>(`/admin/requests?${q}`).then((r) => r.data);
}
export const getRequest = (id: string) => api<RequestDetail>(`/admin/requests/${encodeURIComponent(id)}`);
