import { api } from "./client";
import type { DownloadJob, HfSearchResult, RepoListing } from "./types";

export const searchHf = (q: string, signal?: AbortSignal) =>
  api<{ data: HfSearchResult[] }>(`/admin/hf/search?q=${encodeURIComponent(q)}`, { signal }).then((r) => r.data);
export const repoFiles = (repo: string) => {
  const [owner, name] = repo.split("/");
  return api<RepoListing>(`/admin/hf/repo/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/files`);
};
export const startDownload = (repo: string, quant: string) =>
  api<DownloadJob>("/admin/downloads", { method: "POST", body: { repo, quant } });
export const listDownloads = () => api<{ data: DownloadJob[] }>("/admin/downloads").then((r) => r.data);
export const cancelDownload = (id: string) => api<DownloadJob>(`/admin/downloads/${id}`, { method: "DELETE" });
