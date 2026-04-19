import { useState } from "react";
import { useAnomalies } from "@/hooks/useAnomalies";
import { useTenants } from "@/hooks/useTenants";
import { useTenantParam, useOffsetParam } from "@/hooks/useUrlState";
import { AnomalyCard } from "@/components/data/AnomalyCard";
import { Pagination } from "@/components/ui/Pagination";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { OnboardingState } from "@/components/ui/OnboardingState";
import { pageStack, pageWrapper, pageTitle, rowBetween, rowGap2, stackGap3, checkbox, labelSmall } from "@/theme/styles";

export function AnomalyFeed() {
  const [tenant] = useTenantParam();
  const [offset, setOffset] = useOffsetParam();
  const [showResolved, setShowResolved] = useState(true);
  const { data: tenantsData, isError: tenantsError } = useTenants();
  const hasTenants = (tenantsData?.items.length ?? 0) > 0;

  const { data, isLoading, isError, error, refetch } = useAnomalies({
    tenant,
    offset,
  });

  const anomalies = showResolved
    ? data?.items ?? []
    : (data?.items ?? []).filter((a) => !a.resolved);

  return (
    <div className={`${pageWrapper} ${pageStack}`}>
      <div className={rowBetween}>
        <h2 className={pageTitle}>Anomalies</h2>
        <label className={`${rowGap2} ${labelSmall} cursor-pointer select-none`}>
          <input
            type="checkbox"
            checked={showResolved}
            onChange={(e) => setShowResolved(e.target.checked)}
            className={checkbox}
          />
          Show resolved
        </label>
      </div>

      {!tenant && (!hasTenants || tenantsError) ? (
        <OnboardingState resource="anomalies" />
      ) : !tenant ? (
        <EmptyState message="Select a tenant to view anomalies." />
      ) : isLoading ? (
        <LoadingSkeleton lines={6} />
      ) : isError ? (
        <ErrorState message={error.message} onRetry={() => void refetch()} />
      ) : anomalies.length === 0 ? (
        <EmptyState message="No anomalies detected." />
      ) : (
        <>
          <div className={stackGap3}>
            {anomalies.map((a) => (
              <AnomalyCard key={a.id} anomaly={a} />
            ))}
          </div>
          {data && (
            <Pagination
              total={data.total}
              limit={data.limit}
              offset={data.offset}
              onPageChange={(o) => void setOffset(o)}
            />
          )}
        </>
      )}
    </div>
  );
}
