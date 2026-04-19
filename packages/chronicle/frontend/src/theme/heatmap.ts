import { tokens } from "./tokens";

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function interpolateRgb(
  a: [number, number, number],
  b: [number, number, number],
  t: number,
): string {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `rgb(${r},${g},${bl})`;
}

const rgbMin = hexToRgb(tokens.colours.heatmapMin);
const rgbMid = hexToRgb(tokens.colours.heatmapMid);
const rgbMax = hexToRgb(tokens.colours.heatmapMax);

/** Map a value within [min, max] to a green-yellow-red colour. */
export function latencyColour(
  value: number,
  min: number,
  max: number,
): string {
  if (max === min) return interpolateRgb(rgbMin, rgbMin, 0);
  const t = Math.min(1, Math.max(0, (value - min) / (max - min)));
  if (t < 0.5) {
    return interpolateRgb(rgbMin, rgbMid, t * 2);
  }
  return interpolateRgb(rgbMid, rgbMax, (t - 0.5) * 2);
}

export const cellSize = tokens.spacing.heatmapCellSize;
export const nullCellColour = "#f3f4f6"; // grey-100
