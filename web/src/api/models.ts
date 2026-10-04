import { api } from "./client";
import type { Estimate, FitResult, ModelView, Preset, PsEntry } from "./types";

export const listModels = () => api<{ data: ModelView[] }>("/admin/models").then((r) => r.data);
export const listPs = () => api<{ data: PsEntry[] }>("/admin/ps").then((r) => r.data);
export const estimate = (id: string, preset: Partial<Preset>) =>
  api<Estimate>(`/admin/models/${encodeURIComponent(id)}/estimate`, { method: "POST", body: { preset } });
export const fitModel = (id: string, preset: Partial<Preset>) =>
  api<FitResult>(`/admin/models/${encodeURIComponent(id)}/fit`, { method: "POST", body: { preset } });
export const loadModel = (id: string, preset: Partial<Preset>, confirm: boolean) =>
  api<ModelView>(`/admin/models/${encodeURIComponent(id)}/load`, { method: "POST", body: { preset, confirm } });
export const unloadModel = (id: string) =>
  api<ModelView>(`/admin/models/${encodeURIComponent(id)}/unload`, { method: "POST", body: {} });
export const setAliases = (id: string, aliases: string[]) =>
  api<ModelView>(`/admin/models/${encodeURIComponent(id)}`, { method: "PATCH", body: { aliases } });
export const deleteModel = (id: string) =>
  api<{ deleted: string }>(`/admin/models/${encodeURIComponent(id)}`, { method: "DELETE" });
export const modelLogs = (id: string) =>
  api<{ lines: string[] }>(`/admin/models/${encodeURIComponent(id)}/logs`).then((r) => r.lines);
