import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import type { LatencyBucket } from "@/api/types";
import { tokens } from "@/theme/tokens";
import { axisStyle, lineColours } from "@/theme/recharts";
import { chartTitle, mono, caption, tooltipClass } from "@/theme/styles";
import { formatTime, tickIndices } from "@/utils/format";

interface LatencyLineChartProps {
  data: LatencyBucket[];
  /** Budget threshold in ms -- renders a horizontal line if provided. */
  budgetMs?: number | null;
  /** Index of the bucket to highlight (nearest to selected anomaly). */
  highlightIndex?: number | null;
}

/** Chart-specific ms formatter with microsecond support for sub-1ms values. */
function formatMs(v: number | null | undefined): string {
  if (v == null) return "\u2014";
  if (v < 1) return `${(v * 1000).toFixed(0)}\u00B5s`;
  if (v < 100) return `${v.toFixed(1)}ms`;
  return `${v.toFixed(0)}ms`;
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: LatencyBucket }> }) {
  if (!active || !payload?.length) return null;
  const bucket = payload[0]?.payload;
  if (!bucket) return null;

  return (
    <div className={tooltipClass}>
      <p className="text-[10px] font-medium text-text-muted mb-1.5">{formatTime(bucket.bucket)}</p>
      <div className="space-y-0.5">
        <Row colour={COLOURS.p50} label="p50" value={formatMs(bucket.p50_ms)} />
        <Row colour={COLOURS.p95} label="p95" value={formatMs(bucket.p95_ms)} />
        <Row colour={COLOURS.p99} label="p99" value={formatMs(bucket.p99_ms)} />
      </div>
      <div className="mt-1.5 pt-1.5 border-t border-border/50 flex gap-3">
        <span className="text-[10px] text-text-subtle">{bucket.sample_count} samples</span>
        {bucket.slow_count > 0 && <span className="text-[10px] text-slow">{bucket.slow_count} slow</span>}
        {bucket.timeout_count > 0 && <span className="text-[10px] text-fail">{bucket.timeout_count} timeout</span>}
      </div>
    </div>
  );
}

function Row({ colour, label, value }: { colour: string; label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 text-[11px]">
      <div className="flex items-center gap-1.5">
        <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: colour }} />
        <span className={`${mono} text-text-muted`}>{label}</span>
      </div>
      <span className={`${mono} font-semibold text-text tabular-nums`}>{value}</span>
    </div>
  );
}

function LegendStat({ label, value, colour }: { label: string; value: string; colour: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: colour }} />
      <span className={`${caption} uppercase tracking-[0.06em]`}>{label}</span>
      <span className={`${mono} text-[12px] font-semibold text-text tabular-nums`}>{value}</span>
    </div>
  );
}

// Use the shared palette from recharts.ts
const COLOURS = lineColours;

export function LatencyLineChart({ data, budgetMs, highlightIndex }: LatencyLineChartProps) {
  if (data.length === 0) return null;

  const formatted = data.map((b, i) => ({
    ...b,
    time: formatTime(b.bucket),
    // Only the highlighted bucket gets a value — renders as a single large dot
    highlight: i === highlightIndex ? (b.p95_ms ?? b.p50_ms ?? 0) : null,
  }));

  const latest = data[data.length - 1]!;
  const ticks = tickIndices(formatted.length);
  const tickValues = ticks.map((i) => formatted[i]!.time);

  const p95Values = data.map((b) => b.p95_ms).filter((v): v is number => v != null);
  const avgP95 = p95Values.length > 0
    ? p95Values.reduce((a, b) => a + b, 0) / p95Values.length
    : null;

  return (
    <div className="rounded border border-border bg-bg">
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <h3 className={chartTitle}>Latency Trend</h3>
        <div className="flex items-center gap-4">
          <LegendStat label="P50" value={formatMs(latest.p50_ms)} colour={COLOURS.p50} />
          <LegendStat label="P95" value={formatMs(latest.p95_ms)} colour={COLOURS.p95} />
          <LegendStat label="P99" value={formatMs(latest.p99_ms)} colour={COLOURS.p99} />
        </div>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart
          data={formatted}
          margin={{ top: 8, right: 48, left: 0, bottom: 4 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke={tokens.colours.border}
            vertical={false}
          />
          <XAxis
            dataKey="time"
            tick={axisStyle}
            axisLine={{ stroke: tokens.colours.border }}
            tickLine={false}
            ticks={tickValues}
            tickFormatter={(v: string) => v.replace(",", "")}
          />
          <YAxis
            tick={axisStyle}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatMs(v)}
            width={52}
          />
          <Tooltip
            content={<CustomTooltip />}
            cursor={{ stroke: tokens.colours.border, strokeDasharray: "3 3" }}
          />

          {avgP95 != null && (
            <ReferenceLine
              y={avgP95}
              stroke={tokens.colours.textSubtle}
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{
                value: "avg",
                position: "right",
                fontSize: 9,
                fill: tokens.colours.textSubtle,
                fontFamily: tokens.fonts.mono,
              }}
            />
          )}

          {/* Budget threshold line */}
          {budgetMs != null && (
            <ReferenceLine
              y={budgetMs}
              stroke={tokens.colours.slow}
              strokeDasharray="8 4"
              strokeWidth={1.5}
              label={{
                value: `budget ${formatMs(budgetMs)}`,
                position: "right",
                fontSize: 9,
                fill: tokens.colours.slow,
                fontFamily: tokens.fonts.mono,
              }}
            />
          )}

          {/* Highlighted anomaly — renders a large ring at the selected bucket */}
          {highlightIndex != null && (
            <Line
              type="monotone"
              dataKey="highlight"
              name="anomaly"
              stroke="none"
              strokeWidth={0}
              dot={(props: { cx?: number; cy?: number; index?: number; value?: number | null }) => {
                if (props.value == null || props.cx == null || props.cy == null) return <g key={props.index} />;
                return (
                  <g key={props.index}>
                    <circle cx={props.cx} cy={props.cy} r={12} fill={tokens.colours.fail} fillOpacity={0.15} />
                    <circle cx={props.cx} cy={props.cy} r={7} fill="none" stroke={tokens.colours.fail} strokeWidth={2.5} />
                    <circle cx={props.cx} cy={props.cy} r={3} fill={tokens.colours.fail} />
                  </g>
                );
              }}
              activeDot={false}
              isAnimationActive={false}
              connectNulls={false}
              legendType="none"
            />
          )}

          <Line
            type="monotone"
            dataKey="p50_ms"
            name="p50"
            stroke={COLOURS.p50}
            strokeWidth={2}
            dot={{ r: 3, fill: COLOURS.p50, strokeWidth: 0 }}
            activeDot={{ r: 5, fill: COLOURS.p50, strokeWidth: 2, stroke: tokens.colours.background }}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="p95_ms"
            name="p95"
            stroke={COLOURS.p95}
            strokeWidth={2.5}
            dot={{ r: 3, fill: COLOURS.p95, strokeWidth: 0 }}
            activeDot={{ r: 5, fill: COLOURS.p95, strokeWidth: 2, stroke: tokens.colours.background }}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="p99_ms"
            name="p99"
            stroke={COLOURS.p99}
            strokeWidth={2}
            strokeDasharray="6 3"
            dot={{ r: 3, fill: COLOURS.p99, strokeWidth: 0 }}
            activeDot={{ r: 5, fill: COLOURS.p99, strokeWidth: 2, stroke: tokens.colours.background }}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
