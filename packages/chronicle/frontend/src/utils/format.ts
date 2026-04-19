/**
 * Shared formatting utilities used across transforms, routes, and components.
 * No React imports. No side effects. Pure functions only.
 */

/** Format milliseconds to a concise human-readable string. */
export function fmtMs(v: number | null | undefined): string {
  if (v == null) return "\u2014";
  if (v < 1) return "<1ms";
  if (v < 100) return `${v.toFixed(1)}ms`;
  if (v < 1000) return `${Math.round(v)}ms`;
  return `${(v / 1000).toFixed(1)}s`;
}

/** Format a timestamp to "DD Mon HH:MM" in en-GB locale. */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Format a timestamp without the comma: "19 Apr 09:44". */
export function formatTimeShort(iso: string): string {
  return formatTime(iso).replace(",", "");
}

/** Format milliseconds to a human-readable duration string.
 *  < 1000 → "42ms", < 60000 → "2.18s", >= 60000 → "1m 12s"
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  const mins = Math.floor(ms / 60_000);
  const secs = ((ms % 60_000) / 1000).toFixed(0);
  return `${mins}m ${secs}s`;
}

/** Pick ~N evenly spaced indices from an array of length `len`. */
export function tickIndices(len: number, max: number = 6): number[] {
  if (len <= max) return Array.from({ length: len }, (_, i) => i);
  const step = (len - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => Math.round(i * step));
}

/** Compute a default 7-day time range ending now. */
export function defaultRange(): { from: string; to: string } {
  const now = new Date();
  const sevenDaysAgo = new Date(now.getTime() - 7 * 86_400_000);
  return { from: sevenDaysAgo.toISOString(), to: now.toISOString() };
}
