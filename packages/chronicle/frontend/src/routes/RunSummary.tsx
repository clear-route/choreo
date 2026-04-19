import { useNavigate } from "react-router-dom";
import { useRuns } from "@/hooks/useRuns";
import { useTenants } from "@/hooks/useTenants";
import { useTenantParam, useEnvParam, useOffsetParam } from "@/hooks/useUrlState";
import { toRunRows, toRunTrend } from "@/transforms/runSummary";
import type { RunRow } from "@/transforms/runSummary";
import { RunTrendCharts } from "@/components/data/RunStats";
import { DataTable, type Column } from "@/components/data/DataTable";
import { Pagination } from "@/components/ui/Pagination";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { OnboardingState } from "@/components/ui/OnboardingState";
import { CountOrDash } from "@/components/ui/CountOrDash";
import { pageTitle, pageWrapper, pageStack, link, outcomeTextFail, outcomeTextSlow, outcomeTextPass, input, rowGap2 } from "@/theme/styles";

const DISPLAY_COUNT = 50;

const columns: Column<RunRow>[] = [
  {
    key: "startedAt",
    label: "Started",
    render: (row) => <span className={link}>{row.startedAt}</span>,
  },
  {
    key: "duration",
    label: "Duration",
    align: "right",
    width: "80px",
    render: (row) => <span className="text-text-muted">{row.duration}</span>,
  },
  {
    key: "passRate",
    label: "Rate",
    align: "right",
    width: "72px",
    render: (row) => {
      const colour =
        row.outcome === "fail" ? outcomeTextFail :
        row.outcome === "slow" ? outcomeTextSlow :
        outcomeTextPass;
      return <span className={colour}>{row.passRate}</span>;
    },
  },
  {
    key: "passed",
    label: "Pass",
    align: "right",
    width: "64px",
    render: (row) => <span className="text-text-muted">{row.passed}/{row.total}</span>,
  },
  {
    key: "failed",
    label: "Fail",
    align: "right",
    width: "48px",
    render: (row) => <CountOrDash value={row.failed} style={outcomeTextFail} />,
  },
  {
    key: "slow",
    label: "Slow",
    align: "right",
    width: "48px",
    render: (row) => <CountOrDash value={row.slow} style={outcomeTextSlow} />,
  },
];

export function RunSummary() {
  const navigate = useNavigate();
  const [tenant] = useTenantParam();
  const [env, setEnv] = useEnvParam();
  const [offset, setOffset] = useOffsetParam();
  const { data: tenantsData, isError: tenantsError } = useTenants();
  const hasTenants = (tenantsData?.items.length ?? 0) > 0;

  const { data, isLoading, isError, error, refetch } = useRuns({
    tenant,
    environment: env ?? undefined,
    limit: DISPLAY_COUNT,
    offset,
  });

  if (!tenant && (!hasTenants || tenantsError)) {
    return (
      <div className={pageWrapper}>
        <h2 className={pageTitle}>Runs</h2>
        <OnboardingState resource="runs" />
      </div>
    );
  }

  const rows = data?.items ? toRunRows(data.items, DISPLAY_COUNT) : [];
  const trendData = data?.items ? toRunTrend(data.items) : [];

  return (
    <div className={`${pageWrapper} ${pageStack}`}>
      <div className="flex items-center justify-between">
        <h2 className={pageTitle}>Runs</h2>
        {tenant && (
          <div className={rowGap2}>
            <input
              type="text"
              placeholder="Filter by environment..."
              value={env ?? ""}
              onChange={(e) => void setEnv(e.target.value || null)}
              className={input}
            />
          </div>
        )}
      </div>
      {!tenant ? (
        <EmptyState message="Select a tenant to view runs." />
      ) : isLoading ? (
        <LoadingSkeleton lines={8} />
      ) : isError ? (
        <ErrorState message={error.message} onRetry={() => void refetch()} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState message="No runs found for this tenant and environment." />
      ) : (
        <>
          {trendData.length >= 2 && (
            <RunTrendCharts
              data={trendData}
              onPointClick={(id) => navigate(`/runs/${id}`)}
            />
          )}
          <DataTable
            columns={columns}
            data={rows}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/runs/${r.id}`)}
            defaultSort="startedAt"
            defaultDir="desc"
            emptyMessage="No runs found."
          />
          <Pagination
            total={data.total}
            limit={DISPLAY_COUNT}
            offset={data.offset}
            onPageChange={(o) => void setOffset(o)}
          />
        </>
      )}
    </div>
  );
}
