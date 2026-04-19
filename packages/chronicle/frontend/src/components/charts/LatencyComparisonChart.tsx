import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { tokens } from "@/theme/tokens";
import { axisStyle } from "@/theme/recharts";
import { mono, tooltipClass } from "@/theme/styles";

/**
 * Comparison palette — 10 high-contrast colours ordered for maximum
 * visual separation.  Aligned with the design system: first three
 * match the p50/p95/p99 colours from LatencyLineChart so single-topic
 * comparisons look consistent.
 */
export const COMPARE_COLOURS = [
  tokens.colours.p50,   // blue-600
  tokens.colours.p95,   // amber-600
  tokens.colours.p99,   // red-600
  "#059669",  // emerald-600
  "#7c3aed",  // violet-600
  "#db2777",  // pink-600
  "#0891b2",  // cyan-600
  "#65a30d",  // lime-600
  "#e11d48",  // rose-600
  "#4f46e5",  // indigo-600
];

export interface LatencyComparisonChartProps {
  data: Record<string, unknown>[];
  topics: string[];
  tickValues: string[];
  formatter: (v: number | null | undefined) => string;
  isReliability?: boolean;
}

function ComparisonTooltip({
  active,
  payload,
  topics,
  formatter,
}: {
  active?: boolean;
  payload?: Array<{ payload: Record<string, unknown> }>;
  topics: string[];
  formatter: (v: number | null | undefined) => string;
}) {
  if (!active || !payload?.length) return null;
  const bucket = payload[0]?.payload;
  if (!bucket) return null;

  return (
    <div className={tooltipClass}>
      <p className="text-[10px] font-medium text-text-muted mb-1.5">
        {String(bucket.time)}
      </p>
      <div className="space-y-0.5">
        {topics.map((topic, idx) => (
          <div key={topic} className="flex items-center justify-between gap-4 text-[11px]">
            <div className="flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: COMPARE_COLOURS[idx % COMPARE_COLOURS.length] }}
              />
              <span className={`${mono} text-text-muted truncate max-w-[120px]`}>
                {topic}
              </span>
            </div>
            <span className={`${mono} font-semibold text-text tabular-nums`}>
              {formatter(bucket[topic] as number | null)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Multi-topic comparison line chart.
 *
 * Follows the same visual patterns as LatencyLineChart:
 * - No vertical grid lines
 * - Mono axis labels at 10px
 * - Custom tooltip (not Recharts default)
 * - No built-in Legend (caller renders its own if needed)
 * - No animation
 * - Consistent dot/activeDot sizing
 */
export function LatencyComparisonChart({
  data,
  topics,
  tickValues,
  formatter,
  isReliability = false,
}: LatencyComparisonChartProps) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart
        data={data}
        margin={{ top: 8, right: 16, left: 0, bottom: 4 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          stroke={tokens.colours.border}
          vertical={false}
        />
        <XAxis
          dataKey="time"
          tick={axisStyle}
          ticks={tickValues}
          tickFormatter={(v: string) => v.replace(",", "")}
          axisLine={{ stroke: tokens.colours.border }}
          tickLine={false}
        />
        <YAxis
          tick={axisStyle}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => formatter(v)}
          width={52}
          domain={isReliability ? [0, 100] : ["auto", "auto"]}
        />
        <Tooltip
          content={(props) => (
            <ComparisonTooltip
              active={props.active}
              payload={props.payload as Array<{ payload: Record<string, unknown> }>}
              topics={topics}
              formatter={formatter}
            />
          )}
          cursor={{ stroke: tokens.colours.border, strokeDasharray: "3 3" }}
        />
        {topics.map((topic, idx) => {
          const colour = COMPARE_COLOURS[idx % COMPARE_COLOURS.length]!;
          return (
            <Line
              key={topic}
              type="monotone"
              dataKey={topic}
              name={topic}
              stroke={colour}
              strokeWidth={2}
              dot={{ r: 2, fill: colour, strokeWidth: 0 }}
              activeDot={{ r: 4, fill: colour, strokeWidth: 2, stroke: tokens.colours.background }}
              connectNulls
              isAnimationActive={false}
              legendType="none"
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}
