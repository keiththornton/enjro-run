import { api } from "./client";
import type { BinaryStatus, Settings, SystemInfo } from "./types";

export const getSettings = () => api<Settings>("/admin/settings");
export const updateSettings = (patch: Partial<Settings>) => api<Settings>("/admin/settings", { method: "PUT", body: patch });
export const apiKey = (action: "generate" | "rotate" | "clear") =>
  api<Settings & { key: string | null }>("/admin/settings/api-key", { method: "POST", body: { action } });
export const setHfToken = (token: string | null) =>
  api<{ hf_token: Settings["hf_token"] }>("/admin/settings/hf-token", { method: "PUT", body: { token } });
export const getSystem = () => api<SystemInfo>("/admin/system");
export const getBinary = () => api<BinaryStatus>("/admin/binary");
export const installBinary = (tag?: string, variant?: string) =>
  api<BinaryStatus>("/admin/binary/install", { method: "POST", body: { tag, variant } });
export const rollbackBinary = () => api<BinaryStatus>("/admin/binary/rollback", { method: "POST", body: {} });
