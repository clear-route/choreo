/**
 * Chronicle design tokens.
 *
 * These JS tokens are used by Recharts (which needs inline colour values)
 * and the custom Canvas heatmap. Tailwind components use CSS variables
 * defined in index.css instead.
 *
 * oklch values converted to hex for Recharts/Canvas compatibility.
 */
export const tokens = {
  colours: {
    // Chart line colours
    p50: "#3b82f6",     // info blue
    p95: "#ca8a04",     // slow amber
    p99: "#dc2626",     // fail red

    // Outcome fills (for Recharts bar chart)
    pass: "#16a34a",    // oklch(0.62 0.15 150) approx
    fail: "#dc2626",    // oklch(0.62 0.18 25) approx
    timeout: "#b91c1c", // oklch(0.55 0.18 15) approx
    slow: "#ca8a04",    // oklch(0.70 0.15 75) approx

    // Heatmap scale
    heatmapMin: "#dcfce7",
    heatmapMid: "#fef9c3",
    heatmapMax: "#fecaca",

    // Severity
    warning: "#ca8a04",
    critical: "#dc2626",

    // Chrome — used by Canvas heatmap labels/tooltips
    background: "#fdfdfd",  // oklch(0.99)
    surface: "#fafafa",     // oklch(0.98)
    surface2: "#f5f5f5",    // oklch(0.965)
    border: "#e5e5e5",      // oklch(0.91)
    text: "#1a1a1a",        // oklch(0.18)
    textMuted: "#737373",   // oklch(0.50)
    textSubtle: "#a3a3a3",  // oklch(0.67)
  },
  fonts: {
    body: "ui-sans-serif, -apple-system, 'Segoe UI', Inter, system-ui, sans-serif",
    mono: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Monaco, Consolas, monospace",
  },
  spacing: {
    chartHeight: 280,
    heatmapCellSize: 16,
    sparklineHeight: 22,
    sparklineWidth: 72,
  },
} as const;

export const outcomeColour: Record<string, string> = {
  pass: tokens.colours.pass,
  fail: tokens.colours.fail,
  timeout: tokens.colours.timeout,
  slow: tokens.colours.slow,
};
