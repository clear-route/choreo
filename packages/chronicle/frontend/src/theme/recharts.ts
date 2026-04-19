import { tokens } from "./tokens";

/** Axis tick label style — matches report's mono, xs, subtle text. */
export const axisStyle = {
  fontSize: 11,
  fill: tokens.colours.textSubtle,
  fontFamily: tokens.fonts.mono,
};

/** Grid line style — subtle, dashed. */
export const gridStyle = {
  strokeDasharray: "3 3",
  stroke: tokens.colours.border,
};

/** Tooltip content style — matches report's popover styling. */
export const tooltipStyle: React.CSSProperties = {
  background: tokens.colours.background,
  border: `1px solid ${tokens.colours.border}`,
  borderRadius: "5px",
  fontSize: "11px",
  fontFamily: tokens.fonts.body,
  boxShadow: "0 8px 24px -6px rgba(0,0,0,0.12), 0 2px 8px -2px rgba(0,0,0,0.06)",
  padding: "6px 10px",
};

/** Percentile line colours. */
export const lineColours = {
  p50: tokens.colours.p50,
  p95: tokens.colours.p95,
  p99: tokens.colours.p99,
} as const;
