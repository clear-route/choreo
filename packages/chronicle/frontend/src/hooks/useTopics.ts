import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { PagedResponse, TopicSummary } from "@/api/types";

interface UseTopicsParams {
  tenant: string;
  environment?: string;
}

export function useTopics(params: UseTopicsParams) {
  return useQuery({
    queryKey: ["topics", params.tenant, params.environment],
    queryFn: () =>
      apiFetch<PagedResponse<TopicSummary>>(
        `/topics${qs({
          tenant: params.tenant,
          environment: params.environment,
        })}`,
      ),
    staleTime: 30_000,
    enabled: !!params.tenant,
  });
}
