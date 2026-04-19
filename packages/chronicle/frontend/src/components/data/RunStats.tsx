import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { RunTrendPoint } from "@/transforms/runSummary";
import { axisStyle, gridStyle, tooltipStyle } from "@/theme/recharts";
import { tokens } from "@/theme/tokens";
import { cardPadded, chartTitle } from "@/theme/styles";
import { tickIndices } from "@/utils/format";

interface RunTrendChartsProps {
  data: RunTrendPoint[];
  onPointClick: (runId: string) => void;
}

/** Shorten axis labels: "19 Apr, 09:44" -> "19 Apr 09:44" */
function shortLabel(label: string): string {
  return label.replace(",", "");
}

const CHART_H = 180;

/**
 * Three trend charts showing run health over time.
 * Stateless: receives pre-computed trend data, renders it.
 * Clicking any data point navigates to that run's detail.
 */
export function RunTrendCharts({ data, onPointClick }: RunTrendChartsProps) {
  const hasIssues = data.some((d) => d.failed > 0 || d.slow > 0);
  const ticks = tickIndices(data.length);
  const tickValues = ticks.map((i) => data[i]!.time);

  const handleClick = (payload: Record<string, unknown> | undefined) => {
    const id = payload?.runId;
    if (typeof id === "string") onPointClick(id);
  };

  const xAxisProps = {
    dataKey: "time" as const,
    tick: axisStyle,
    ticks: tickValues,
    tickFormatter: shortLabel,
  };

  return (
    <div className={`hidden md:grid gap-4 ${hasIssues ? "grid-cols-1 lg:grid-cols-3" : "grid-cols-1 lg:grid-cols-2"}`}>
      {/* Pass Rate */}
      <div className={cardPadded}>
        <h3 className={chartTitle}>Pass Rate</h3>
        <ResponsiveContainer width="100%" height={CHART_H}>
          <AreaChart data={data} onClick={(e) => handleClick(e?.activePayload?.[0]?.payload)}>
            <CartesianGrid {...gridStyle} />
            <XAxis {...xAxisProps} />
            <YAxis tick={axisStyle} domain={[0, 100]} unit="%" width={36} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value: number) => [`${value}%`, "Pass rate"]}
              cursor={{ stroke: tokens.colours.border, strokeDasharray: "3 3" }}
            />
            <defs>
              <linearGradient id="passGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={tokens.colours.pass} stopOpacity={0.2} />
                <stop offset="100%" stopColor={tokens.colours.pass} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <Area
              type="monotone"
              dataKey="passRate"
              stroke={tokens.colours.pass}
              strokeWidth={1.5}
              fill="url(#passGrad)"
              dot={false}
              activeDot={{ r: 3, fill: tokens.colours.pass, cursor: "pointer" }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Failures & Slow */}
      {hasIssues && (
        <div className={cardPadded}>
          <h3 className={chartTitle}>Failures &amp; Slow</h3>
          <ResponsiveContainer width="100%" height={CHART_H}>
            <BarChart data={data} onClick={(e) => handleClick(e?.activePayload?.[0]?.payload)}>
              <CartesianGrid {...gridStyle} />
              <XAxis {...xAxisProps} />
              <YAxis tick={axisStyle} width={24} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: tokens.colours.surface2 }} />
              <Bar dataKey="failed" name="Failed" fill={tokens.colours.fail} stackId="issues" radius={[2, 2, 0, 0]} />
              <Bar dataKey="slow" name="Slow" fill={tokens.colours.slow} stackId="issues" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Duration */}
      <div className={cardPadded}>
        <h3 className={chartTitle}>Run Duration</h3>
        <ResponsiveContainer width="100%" height={CHART_H}>
          <LineChart data={data} onClick={(e) => handleClick(e?.activePayload?.[0]?.payload)}>
            <CartesianGrid {...gridStyle} />
            <XAxis {...xAxisProps} />
            <YAxis tick={axisStyle} width={30} unit="s" />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value: number) => [`${value}s`, "Duration"]}
              cursor={{ stroke: tokens.colours.border, strokeDasharray: "3 3" }}
            />
            <Line
              type="monotone"
              dataKey="durationSec"
              name="Duration"
              stroke={tokens.colours.p50}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3, fill: tokens.colours.p50, cursor: "pointer" }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
