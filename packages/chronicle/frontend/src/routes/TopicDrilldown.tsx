import { useParams, Link, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTopicLatency } from "@/hooks/useTopicLatency";
import { useTopicRuns } from "@/hooks/useTopicRuns";
import { useTopicAnomalies } from "@/hooks/useTopicAnomalies";
import { useTenantParam, useEnvParam, useFromParam, useToParam } from "@/hooks/useUrlState";
import { TimeRangePicker } from "@/components/layout/TimeRangePicker";
import { LatencyLineChart } from "@/components/charts/LatencyLineChart";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Badge } from "@/components/ui/Badge";
import { CountOrDash } from "@/components/ui/CountOrDash";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatCard } from "@/components/data/StatCard";
import {
  pageStack, pageWrapper, pageTitle, headerGroup, subtitle, linkBack, toolbar,
  chartTitle, mono, link as linkStyle, outcomeTextFail, outcomeTextSlow,
} from "@/theme/styles";
import type { TopicRunSummary, AnomalyCard } from "@/api/types";
import { defaultRange, formatTimeShort as formatTime, fmtMs } from "@/utils/format";
import { toTopicSummaryStats } from "@/transforms/topicDrilldown";

// ── Topic Runs Table columns ──

const runColumns: Column<TopicRunSummary>[] = [
  {
    key: "started_at",
    label: "Started",
    render: (row) => (
      <span className={`${linkStyle} ${mono} text-[11px]`}>
        {formatTime(row.started_at)}
      </span>
    ),
  },
  {
    key: "environment",
    label: "Env",
    width: "80px",
    render: (row) => (
      <span className="text-text-muted">{row.environment ?? "\u2014"}</span>
    ),
  },
  {
    key: "handle_count",
    label: "Handles",
    align: "right",
    width: "64px",
  },
  {
    key: "p50_ms",
    label: "P50",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.p50_ms),
  },
  {
    key: "p95_ms",
    label: "P95",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.p95_ms),
  },
  {
    key: "p99_ms",
    label: "P99",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.p99_ms),
  },
  {
    key: "slow_count",
    label: "Slow",
    align: "right",
    width: "48px",
    render: (row) => <CountOrDash value={row.slow_count} style={outcomeTextSlow} />,
  },
  {
    key: "timeout_count",
    label: "Timeouts",
    align: "right",
    width: "64px",
    render: (row) => <CountOrDash value={row.timeout_count} style={outcomeTextFail} />,
  },
];

// ── Route ──

export function TopicDrilldown() {
  const navigate = useNavigate();
  const { topic } = useParams<{ topic: string }>();
  const [tenant] = useTenantParam();
  const [env] = useEnvParam();
  const [from, setFrom] = useFromParam();
  const [to, setTo] = useToParam();

  useEffect(() => {
    if (!from && !to) {
      const { from: defaultFrom, to: defaultTo } = defaultRange();
      void setFrom(defaultFrom);
      void setTo(defaultTo);
    }
  }, [from, to, setFrom, setTo]);

  const latency = useTopicLatency({
    topic: topic ?? "",
    tenant,
    environment: env ?? undefined,
    from: from ?? undefined,
    to: to ?? undefined,
  });

  const runs = useTopicRuns({
    topic: topic ?? "",
    tenant,
    limit: 20,
  });

  const anomalies = useTopicAnomalies({
    topic: topic ?? "",
    tenant,
  });

  // Convert anomalies to chart markers
  const [selectedAnomalyId, setSelectedAnomalyId] = useState<string | null>(null);

  // Find the nearest chart bucket index for the selected anomaly.
  const highlightIndex = useMemo(() => {
    if (!selectedAnomalyId || !anomalies.data || !latency.data) return null;
    const selected = anomalies.data.items.find((a) => a.id === selectedAnomalyId);
    if (!selected) return null;

    const anomalyMs = new Date(selected.detected_at).getTime();
    let nearestIdx = 0;
    let minDiff = Infinity;
    latency.data.buckets.forEach((b, i) => {
      const diff = Math.abs(new Date(b.bucket).getTime() - anomalyMs);
      if (diff < minDiff) {
        minDiff = diff;
        nearestIdx = i;
      }
    });
    return nearestIdx;
  }, [selectedAnomalyId, anomalies.data, latency.data]);

  const handleTimeChange = useCallback((newFrom: string, newTo: string) => {
    void setFrom(newFrom);
    void setTo(newTo);
  }, [setFrom, setTo]);

  return (
    <div className={`${pageWrapper} ${pageStack} overflow-auto`}>
      <div className={headerGroup}>
        <div>
          <Link to="/topics" className={linkBack}>&larr; Back to Topics</Link>
          <h2 className={`mt-1 ${pageTitle}`}>Topic Drilldown</h2>
          <p className={subtitle}>{topic}</p>
        </div>
        <div className={`flex items-center ${toolbar}`}>
          <TimeRangePicker from={from} to={to} onChange={handleTimeChange} />
        </div>
      </div>

      {!tenant ? (
        <EmptyState message="Select a tenant to view topic data." />
      ) : latency.isLoading ? (
        <LoadingSkeleton lines={8} />
      ) : latency.isError ? (
        <ErrorState message={latency.error.message} onRetry={() => void latency.refetch()} />
      ) : !latency.data || latency.data.buckets.length === 0 ? (
        <EmptyState message="No data for this time range." />
      ) : (
        <div className={pageStack}>
          {/* Summary stats */}
          <TopicSummaryStrip buckets={latency.data.buckets} resolution={latency.data.resolution} />

          {/* Latency chart with budget line and anomaly markers */}
          <LatencyLineChart
            data={latency.data.buckets}
            highlightIndex={highlightIndex}
          />

          {/* Anomaly cards (if any) */}
          {anomalies.data && anomalies.data.items.length > 0 && (
            <div>
              <h3 className={chartTitle}>Anomalies for this Topic</h3>
              <div className="space-y-2 mt-2">
                {anomalies.data.items.slice(0, 5).map((a) => (
                  <AnomalyRow
                    key={a.id}
                    anomaly={a}
                    isSelected={selectedAnomalyId === a.id}
                    onSelect={() => setSelectedAnomalyId(selectedAnomalyId === a.id ? null : a.id)}
                    onRunClick={(id) => navigate(`/runs/${id}`)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Recent runs table */}
          <div>
            <h3 className={chartTitle}>Recent Runs with this Topic</h3>
            <div className="mt-2">
              {runs.isLoading ? (
                <LoadingSkeleton lines={4} />
              ) : runs.data && runs.data.items.length > 0 ? (
                <DataTable
                  columns={runColumns}
                  data={runs.data.items}
                  rowKey={(r) => r.run_id}
                  onRowClick={(r) => navigate(`/runs/${r.run_id}`)}
                  defaultSort="started_at"
                  emptyMessage="No runs found for this topic."
                />
              ) : (
                <p className="text-[11px] text-text-subtle italic py-4">
                  No runs data available. The topic runs endpoint may not be implemented yet.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Inline components (small enough to stay in the route) ──

function TopicSummaryStrip({ buckets, resolution }: { buckets: import("@/api/types").LatencyBucket[]; resolution: string }) {
  const stats = toTopicSummaryStats(buckets, resolution);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
      <StatCard label="P50" value={stats.p50} />
      <StatCard label="P95" value={stats.p95} />
      <StatCard label="P99" value={stats.p99} />
      <StatCard label="Samples" value={String(stats.totalSamples)} />
      <StatCard label="Failed" value={String(stats.totalFail)} colour={stats.totalFail > 0 ? "text-fail" : "text-text"} />
      <StatCard label="Slow" value={String(stats.totalSlowAndTimeout)} colour={stats.totalSlowAndTimeout > 0 ? "text-slow" : "text-text"} />
      <StatCard label="Over Budget" value={String(stats.totalBudgetViolations)} colour={stats.totalBudgetViolations > 0 ? "text-slow" : "text-text"} />
      <StatCard label="Resolution" value={stats.resolution} />
    </div>
  );
}

function AnomalyRow({
  anomaly: a,
  isSelected,
  onSelect,
  onRunClick,
}: {
  anomaly: AnomalyCard;
  isSelected: boolean;
  onSelect: () => void;
  onRunClick: (id: string) => void;
}) {
  return (
    <div
      className={`rounded border px-4 py-2.5 flex items-center gap-3 text-[12px] cursor-pointer transition-all duration-[120ms] ${
        isSelected
          ? "border-info bg-info-tint ring-1 ring-info/30"
          : a.resolved
            ? "border-border/50 bg-bg opacity-60 hover:bg-surface-2 hover:opacity-100"
            : "border-border bg-bg hover:bg-surface-2"
      }`}
      onClick={onSelect}
    >
      <Badge variant={a.severity}>{a.severity}</Badge>
      <span className={`${mono} text-text-muted`}>{a.metric}</span>
      <span className={`${mono} tabular-nums`}>
        <span className="text-fail font-medium">{a.current_value.toFixed(1)}</span>
        <span className="text-text-subtle"> vs </span>
        <span className="text-text-muted">{a.baseline_value.toFixed(1)}</span>
      </span>
      <span className={`${mono} tabular-nums text-[11px] ${a.change_pct > 0 ? "text-fail" : "text-pass"}`}>
        {a.change_pct > 0 ? "+" : ""}{a.change_pct.toFixed(1)}%
      </span>
      <span className="ml-auto text-[10px] text-text-subtle">{formatTime(a.detected_at)}</span>
      {a.resolved && <span className="text-[10px] text-text-subtle italic">resolved</span>}
      <button
        className={`${linkStyle} text-[10px] shrink-0`}
        onClick={(e) => { e.stopPropagation(); onRunClick(a.run_id); }}
      >
        view run
      </button>
    </div>
  );
}
