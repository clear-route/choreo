import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { PagedResponse, RunSummary } from "@/api/types";

interface UseRunsParams {
  tenant: string;
  environment?: string;
  branch?: string;
  limit?: number;
  offset?: number;
}

export function useRuns(params: UseRunsParams) {
  return useQuery({
    queryKey: ["runs", params.tenant, params.environment, params.branch, params.limit, params.offset],
    queryFn: () =>
      apiFetch<PagedResponse<RunSummary>>(
        `/runs${qs({
          tenant: params.tenant,
          environment: params.environment,
          branch: params.branch,
          limit: params.limit ?? 50,
          offset: params.offset ?? 0,
        })}`,
      ),
    staleTime: 10_000,
    enabled: !!params.tenant,
  });
}
