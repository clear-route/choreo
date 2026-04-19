import { useNavigate } from "react-router-dom";
import { useTopics } from "@/hooks/useTopics";
import { useTenants } from "@/hooks/useTenants";
import { useTenantParam, useEnvParam } from "@/hooks/useUrlState";
import { DataTable, type Column } from "@/components/data/DataTable";
import { CountOrDash } from "@/components/ui/CountOrDash";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { OnboardingState } from "@/components/ui/OnboardingState";
import { pageTitle, pageWrapper, pageStack, link, subtitle, outcomeTextSlow, outcomeTextFail, outcomeTextPass, badgeBase } from "@/theme/styles";
import type { TopicSummary } from "@/api/types";
import { fmtMs } from "@/utils/format";

interface TopicRow extends TopicSummary {
  reliabilityPct: number;
  reliabilityLabel: string;
  status: "healthy" | "degraded" | "failing";
  lastSeen: string;
}

function toTopicRows(topics: TopicSummary[]): TopicRow[] {
  return topics.map((t) => {
    const issues = t.slow_count + t.timeout_count;
    const reliabilityPct = t.sample_count > 0
      ? ((t.sample_count - issues) / t.sample_count) * 100
      : 100;
    const status: TopicRow["status"] =
      reliabilityPct < 90 ? "failing" :
      reliabilityPct < 98 ? "degraded" :
      "healthy";

    const now = Date.now();
    const lastSeenMs = new Date(t.latest_run_at).getTime();
    const ageHours = (now - lastSeenMs) / 3_600_000;
    const lastSeen =
      ageHours < 1 ? "just now" :
      ageHours < 24 ? `${Math.floor(ageHours)}h ago` :
      ageHours < 168 ? `${Math.floor(ageHours / 24)}d ago` :
      new Date(t.latest_run_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

    return {
      ...t,
      reliabilityPct,
      reliabilityLabel: `${reliabilityPct.toFixed(1)}%`,
      status,
      lastSeen,
    };
  });
}

const columns: Column<TopicRow>[] = [
  {
    key: "topic",
    label: "Topic",
    render: (row) => <span className={link}>{row.topic}</span>,
  },
  {
    key: "reliabilityPct",
    label: "Reliability",
    align: "right",
    width: "90px",
    render: (row) => {
      const colour =
        row.status === "failing" ? outcomeTextFail :
        row.status === "degraded" ? outcomeTextSlow :
        outcomeTextPass;
      return <span className={colour}>{row.reliabilityLabel}</span>;
    },
  },
  {
    key: "latest_p50_ms",
    label: "P50",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.latest_p50_ms),
  },
  {
    key: "latest_p95_ms",
    label: "P95",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.latest_p95_ms),
  },
  {
    key: "latest_p99_ms",
    label: "P99",
    align: "right",
    width: "72px",
    render: (row) => fmtMs(row.latest_p99_ms),
  },
  {
    key: "sample_count",
    label: "Samples",
    align: "right",
    width: "72px",
  },
  {
    key: "slow_count",
    label: "Slow",
    align: "right",
    width: "56px",
    render: (row) => <CountOrDash value={row.slow_count} style={outcomeTextSlow} />,
  },
  {
    key: "timeout_count",
    label: "Timeouts",
    align: "right",
    width: "72px",
    render: (row) => <CountOrDash value={row.timeout_count} style={outcomeTextFail} />,
  },
  {
    key: "status",
    label: "Status",
    align: "right",
    width: "72px",
    render: (row) => {
      const bg = { healthy: "bg-pass", degraded: "bg-slow", failing: "bg-fail" };
      return <span className={`${badgeBase} ${bg[row.status]}`}>{row.status}</span>;
    },
  },
  {
    key: "lastSeen",
    label: "Last Seen",
    align: "right",
    width: "80px",
    render: (row) => <span className="text-text-subtle">{row.lastSeen}</span>,
  },
];

export function TopicList() {
  const navigate = useNavigate();
  const [tenant] = useTenantParam();
  const [env] = useEnvParam();
  const { data: tenantsData, isError: tenantsError } = useTenants();
  const hasTenants = (tenantsData?.items.length ?? 0) > 0;

  const { data, isLoading, isError, error, refetch } = useTopics({
    tenant,
    environment: env ?? undefined,
  });

  const topics = data?.items ? toTopicRows(data.items) : [];

  return (
    <div className={`${pageWrapper} ${pageStack}`}>
      <div>
        <h2 className={pageTitle}>Topics</h2>
        <p className={subtitle}>
          Latency percentiles and reliability across all ingested runs
        </p>
      </div>
      {!tenant && (!hasTenants || tenantsError) ? (
        <OnboardingState resource="topics" />
      ) : !tenant ? (
        <EmptyState message="Select a tenant to view topics." />
      ) : isLoading ? (
        <LoadingSkeleton lines={8} />
      ) : isError ? (
        <ErrorState message={error.message} onRetry={() => void refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={topics}
          rowKey={(r) => r.topic}
          onRowClick={(r) => navigate(`/topics/${encodeURIComponent(r.topic)}`)}
          defaultSort="reliabilityPct"
          defaultDir="asc"
          emptyMessage="No topics found for this tenant."
        />
      )}
    </div>
  );
}
