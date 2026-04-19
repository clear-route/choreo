/**
 * Chronicle Design System.
 *
 * Visual DNA:
 *   - Flat surface hierarchy (bg → surface → surface-2), no card shadows
 *   - Tight spacing (4-8-12-16-20-24px scale)
 *   - Small radii (rounded-sm = 3px, rounded = 5px)
 *   - Dense, information-first layout
 *   - Solid-fill badges with white text
 *   - System font stack, monospace for data
 *   - oklch neutral greys, no blue tint
 *   - Borders as separators, not card chrome
 */

// ── Surface ──

/** Content panel — flat, border only, no shadow. Base for cardPadded etc. */
const card = "rounded border border-border bg-bg";

/** Panel with padding. */
export const cardPadded = `${card} p-4`;

/** Toolbar — surface background, compact padding. */
export const toolbar = "bg-surface border border-border rounded px-4 py-2";

// ── Typography ──

export const pageTitle = "text-xl font-semibold tracking-tight text-text";
export const chartTitle = "mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-text-muted";
export const caption = "text-[11px] text-text-subtle";
export const body = "text-[13px] text-text";
export const mono = "font-mono";
export const subtitle = "mt-1 font-mono text-[12px] text-text-muted";

// ── Table (used by DataTable) ──

export const thead = "border-b border-border text-left text-[10px] font-semibold uppercase tracking-[0.05em] text-text-muted";

// ── Form ──

export const input = "rounded-sm border border-border bg-bg px-2 py-1 text-[13px] text-text transition-colors duration-[120ms] placeholder:text-text-subtle hover:border-border-strong";
export const inputInteractive = `${input} cursor-pointer`;
export const inputCompact = "rounded-sm border border-border bg-bg px-2 py-1 text-[12px] text-text hover:border-border-strong";
export const btnGhost = "rounded-sm border border-border bg-transparent px-2 py-1 text-[12px] text-text-muted hover:bg-surface-2 hover:text-text transition-colors duration-[120ms] disabled:opacity-40 disabled:cursor-not-allowed";
export const segmentActive = "px-3 py-1 text-[12px] font-medium bg-info-tint text-info border-r border-info-border last:border-r-0";
export const segmentInactive = "px-3 py-1 text-[12px] text-text-muted hover:bg-surface-2 hover:text-text border-r border-border last:border-r-0 transition-colors duration-[120ms]";
export const segmentGroup = "inline-flex rounded-sm border border-border bg-bg overflow-hidden";
export const btnDanger = "mt-3 rounded-sm bg-fail px-3 py-1.5 text-[12px] font-medium text-badge-fg hover:opacity-90";

// ── Navigation ──

export const link = "text-info hover:underline";
export const linkSmall = "text-[12px] text-info hover:underline";
export const linkBack = "text-[12px] text-text-muted hover:text-text";
const navLink = "rounded-sm px-3 py-1 text-[13px] font-medium transition-colors duration-[120ms]";
export const navLinkActive = `${navLink} bg-info-tint text-info`;
export const navLinkInactive = `${navLink} text-text-muted hover:text-text hover:bg-surface-2`;

// ── Badge ──

export const badgeBase = "inline-flex items-center rounded-sm px-2 py-px text-[10px] font-bold font-mono text-badge-fg leading-[1.4]";
export const badgeVariants: Record<string, string> = {
  pass: "bg-pass", passed: "bg-pass",
  fail: "bg-fail", failed: "bg-fail",
  errored: "bg-fail brightness-[0.85]",
  timeout: "bg-timeout", slow: "bg-slow", skipped: "bg-skip",
  warning: "bg-warning", critical: "bg-critical",
  default: "bg-text-subtle text-bg",
};

// ── Feedback ──

export const tooltipClass = "pointer-events-none fixed z-50 rounded border border-border bg-bg px-3 py-2 text-[11px] shadow-[0_8px_24px_-6px_oklch(0_0_0/0.12),0_2px_8px_-2px_oklch(0_0_0/0.06)]";
export const emptyState = "flex flex-col items-center justify-center rounded border border-border bg-surface/50 p-6 text-center";
export const errorState = "flex flex-col items-center justify-center rounded border border-fail/30 bg-fail-tint p-5 text-center";
export const checkbox = "h-3.5 w-3.5 appearance-none rounded-sm border border-border bg-bg checked:border-info checked:bg-info cursor-pointer transition-colors duration-[120ms] relative checked:after:content-[''] checked:after:absolute checked:after:inset-0 checked:after:bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20viewBox%3D%220%200%2014%2014%22%20fill%3D%22none%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M3.5%207.5L6%2010L10.5%204.5%22%20stroke%3D%22white%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] checked:after:bg-center checked:after:bg-no-repeat";
export const labelSmall = "text-[12px] text-text-muted";

// ── Layout ──

/** Standard page wrapper — max width, padding, centred. */
export const pageWrapper = "mx-auto w-full max-w-7xl px-5 py-4";

/** Full-height layout (below the 48px nav header). */
export const fullHeight = "h-[calc(100vh-48px)]";

/** Stat label — uppercase, tiny, muted. Used for metadata grids. */
export const statLabel = "text-[10px] font-semibold uppercase tracking-[0.05em] text-text-subtle";

export const pageStack = "space-y-5";
export const headerGroup = "space-y-2";
export const rowBetween = "flex items-center justify-between";
export const row = "flex items-center";
export const rowGap2 = "flex items-center gap-2";
export const rowGap3 = "flex items-center gap-3";
export const stackGap3 = "space-y-3";

// ── Select (Radix) ──

export const selectContent = "overflow-hidden rounded border border-border bg-bg shadow-[0_8px_24px_-6px_oklch(0_0_0/0.12),0_2px_8px_-2px_oklch(0_0_0/0.06)]";
export const selectItem = "cursor-pointer rounded-sm px-3 py-1 text-[13px] outline-none data-[highlighted]:bg-surface-2";

// ── Outcome text ──

export const outcomeTextPass = "text-pass font-medium";
export const outcomeTextFail = "text-fail font-medium";
export const outcomeTextSlow = "text-slow font-medium";
export const outcomeTextMuted = "text-border-strong";

// ── Stat grid ──

export const statGridCompact = "grid grid-cols-4 gap-x-6 gap-y-1.5";

// ── Caret button ──

export const caretButton = "inline-flex w-[22px] h-[22px] items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-text text-[14px] font-bold transition-transform duration-[180ms] shrink-0";
export const caretButtonSmall = "inline-flex w-[18px] h-[18px] items-center justify-center rounded-sm border border-border-strong bg-surface-2 text-text text-[12px] font-bold transition-transform duration-[180ms] shrink-0";

// ── Skeleton ──

export const skeletonLine = "h-3 rounded-sm bg-surface-2";

// ── Proportion bar ──

export const proportionBar = "flex h-1.5 rounded-full overflow-hidden bg-surface-2";
