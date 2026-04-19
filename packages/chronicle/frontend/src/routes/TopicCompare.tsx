import { useState, useMemo, useCallback } from "react";
import { useQueries } from "@tanstack/react-query";
import { useQueryState, parseAsString } from "nuqs";
import { useTopics } from "@/hooks/useTopics";
import { useTenantParam, useEnvParam, useFromParam, useToParam } from "@/hooks/useUrlState";
import { apiFetch, qs } from "@/api/client";
import type { TopicLatencyResponse, LatencyBucket } from "@/api/types";
import { TimeRangePicker } from "@/components/layout/TimeRangePicker";
import { LatencyComparisonChart, COMPARE_COLOURS } from "@/components/charts/LatencyComparisonChart";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  headerGroup, fullHeight, pageWrapper, toolbar, cardPadded, chartTitle, mono, input, pageTitle,
  segmentGroup, segmentActive, segmentInactive,
  caption,
} from "@/theme/styles";

type Metric = "p50_ms" | "p95_ms" | "p99_ms" | "reliability";
const METRIC_OPTIONS: { key: Metric; label: string }[] = [
  { key: "p50_ms", label: "P50" },
  { key: "p95_ms", label: "P95" },
  { key: "p99_ms", label: "P99" },
  { key: "reliability", label: "Reliability" },
];

const MAX_TOPICS = 10;

import { formatTimeShort as formatTime, fmtMs, tickIndices } from "@/utils/format";

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "\u2014";
  return `${v.toFixed(1)}%`;
}

/** Compute reliability % for a bucket: (samples - slow - timeout - fail) / samples * 100 */
function bucketReliability(b: LatencyBucket): number {
  if (b.sample_count === 0) return 100;
  const issues = b.slow_count + b.timeout_count + b.fail_count;
  return ((b.sample_count - issues) / b.sample_count) * 100;
}

export function TopicCompare() {
  const [tenant] = useTenantParam();
  const [env] = useEnvParam();
  const [from, setFrom] = useFromParam();
  const [to, setTo] = useToParam();
  // Store selected topics in URL for shareability: ?topics=orders.created,orders.filled
  const [topicsParam, setTopicsParam] = useQueryState("topics", parseAsString);
  const selected = useMemo(
    () => (topicsParam ? topicsParam.split(",").filter(Boolean) : []),
    [topicsParam],
  );
  const setSelected = useCallback(
    (topics: string[]) => void setTopicsParam(topics.length > 0 ? topics.join(",") : null),
    [setTopicsParam],
  );
  const [search, setSearch] = useState("");
  const [metric, setMetric] = useState<Metric>("p95_ms");

  const { data: topicsData } = useTopics({ tenant, environment: env ?? undefined });
  const allTopics = topicsData?.items ?? [];
  const filtered = allTopics.filter((t) =>
    t.topic.toLowerCase().includes(search.toLowerCase())
  );

  // Dynamic queries — one per selected topic, no hardcoded limit
  const latencyQueries = useQueries({
    queries: selected.map((topic) => ({
      queryKey: ["topicLatency", topic, tenant, env, from, to],
      queryFn: () =>
        apiFetch<TopicLatencyResponse>(
          `/topics/${encodeURIComponent(topic)}/latency${qs({
            tenant,
            environment: env,
            from: from ?? undefined,
            to: to ?? undefined,
          })}`,
        ),
      staleTime: 30_000,
      enabled: !!tenant && !!topic,
    })),
  });

  const isLoading = latencyQueries.some((q) => q.isLoading);

  // Merge all topic data into a single time-series
  const chartData = useMemo(() => {
    const timeMap = new Map<string, Record<string, unknown>>();

    latencyQueries.forEach((query, idx) => {
      const topic = selected[idx];
      if (!topic || !query.data) return;
      for (const b of query.data.buckets) {
        const time = formatTime(b.bucket);
        const existing = timeMap.get(time) ?? { time };
        if (metric === "reliability") {
          existing[topic] = bucketReliability(b);
        } else {
          existing[topic] = b[metric];
        }
        timeMap.set(time, existing);
      }
    });

    return Array.from(timeMap.values()).sort((a, b) =>
      String(a.time).localeCompare(String(b.time))
    );
  }, [latencyQueries, selected, metric]);

  const toggleTopic = (topic: string) => {
    const next = selected.includes(topic)
      ? selected.filter((t) => t !== topic)
      : selected.length < MAX_TOPICS
        ? [...selected, topic]
        : selected;
    setSelected(next);
  };

  const handleTimeChange = useCallback((newFrom: string, newTo: string) => {
    void setFrom(newFrom);
    void setTo(newTo);
  }, [setFrom, setTo]);

  const ticks = tickIndices(chartData.length);
  const tickValues = ticks.map((i) => String(chartData[i]?.time ?? "")).filter(Boolean);
  const metricLabel = METRIC_OPTIONS.find((m) => m.key === metric)?.label ?? "P95";
  const isReliability = metric === "reliability";
  const formatter = isReliability ? fmtPct : fmtMs;
  // yUnit available for future axis labelling

  return (
    <div className={`flex flex-col ${fullHeight} ${pageWrapper}`}>
      <div className={`${headerGroup} shrink-0 mb-4`}>
        <h2 className={pageTitle}>Compare Topics</h2>
        <div className={`flex items-center gap-3 ${toolbar}`}>
          <TimeRangePicker from={from} to={to} onChange={handleTimeChange} />
        </div>
      </div>

      {!tenant ? (
        <EmptyState message="Select a tenant to compare topics." />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-4 flex-1 min-h-0">
          {/* Topic picker */}
          <div className={`${cardPadded} flex flex-col min-h-0`}>
            <h3 className={chartTitle}>Select Topics (max {MAX_TOPICS})</h3>
            <input
              type="text"
              placeholder="Search topics..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={`${input} w-full mb-2`}
            />
            {selected.length > 0 && (
              <button
                onClick={() => setSelected([])}
                className={`${caption} text-info hover:underline mb-2 text-left`}
              >
                Clear all ({selected.length})
              </button>
            )}
            <div className="space-y-0.5 flex-1 overflow-auto min-h-0">
              {filtered.map((t) => {
                const isSelected = selected.includes(t.topic);
                const idx = selected.indexOf(t.topic);
                return (
                  <button
                    key={t.topic}
                    onClick={() => toggleTopic(t.topic)}
                    className={`w-full text-left px-2 py-1.5 rounded-sm text-[12px] ${mono} transition-colors duration-[120ms] flex items-center gap-2 ${
                      isSelected
                        ? "bg-surface-2 text-text"
                        : selected.length >= MAX_TOPICS
                          ? "text-text-subtle cursor-not-allowed"
                          : "text-text-muted hover:bg-surface-2 hover:text-text"
                    }`}
                    disabled={!isSelected && selected.length >= MAX_TOPICS}
                  >
                    {isSelected && (
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: COMPARE_COLOURS[idx % COMPARE_COLOURS.length] }}
                      />
                    )}
                    <span className="truncate">{t.topic}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Chart */}
          <div className="flex flex-col min-h-0">
            {selected.length === 0 ? (
              <EmptyState message="Select topics from the list to compare." />
            ) : isLoading ? (
              <LoadingSkeleton lines={6} />
            ) : chartData.length < 2 ? (
              <EmptyState message="Not enough data to render a comparison." />
            ) : (
              <div className={`${cardPadded} flex flex-col flex-1 min-h-0`}>
                <div className="flex items-center justify-between mb-2 shrink-0">
                  <h3 className={chartTitle}>{metricLabel} {isReliability ? "" : "Latency "}Comparison</h3>
                  <div className={segmentGroup}>
                    {METRIC_OPTIONS.map((opt) => (
                      <button
                        key={opt.key}
                        onClick={() => setMetric(opt.key)}
                        className={metric === opt.key ? segmentActive : segmentInactive}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex-1 min-h-[300px]">
                  <LatencyComparisonChart
                    data={chartData}
                    topics={selected}
                    tickValues={tickValues}
                    formatter={formatter}
                    isReliability={isReliability}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
