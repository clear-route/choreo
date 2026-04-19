import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { PagedResponse, AnomalyCard } from "@/api/types";

interface UseAnomaliesParams {
  tenant: string;
  limit?: number;
  offset?: number;
}

export function useAnomalies(params: UseAnomaliesParams) {
  return useQuery({
    queryKey: ["anomalies", params.tenant, params.limit, params.offset],
    queryFn: () =>
      apiFetch<PagedResponse<AnomalyCard>>(
        `/anomalies${qs({
          tenant: params.tenant,
          limit: params.limit ?? 50,
          offset: params.offset ?? 0,
        })}`,
      ),
    staleTime: 10_000,
    enabled: !!params.tenant,
  });
}
