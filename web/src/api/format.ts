// Formatting for numbers the user compares: always shown in mono.

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const gb = n / 1e9;
  if (gb >= 10) return `${gb.toFixed(0)} GB`;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.round(n / 1e6)} MB`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`;
}

export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

export function fmtCtx(n: number): string {
  return n >= 1024 && n % 1024 === 0 ? `${n / 1024}k` : String(n);
}

export function fmtRate(n: number | null | undefined): string {
  if (n == null) return "—";
  return n >= 100 ? n.toFixed(0) : n.toFixed(1);
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
