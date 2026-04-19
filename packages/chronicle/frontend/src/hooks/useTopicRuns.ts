import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { PagedResponse, TopicRunSummary } from "@/api/types";

interface UseTopicRunsParams {
  topic: string;
  tenant: string;
  limit?: number;
}

export function useTopicRuns(params: UseTopicRunsParams) {
  return useQuery({
    queryKey: ["topicRuns", params.topic, params.tenant, params.limit],
    queryFn: () =>
      apiFetch<PagedResponse<TopicRunSummary>>(
        `/topics/${encodeURIComponent(params.topic)}/runs${qs({
          tenant: params.tenant,
          limit: params.limit ?? 20,
        })}`,
      ),
    staleTime: 30_000,
    enabled: !!params.tenant && !!params.topic,
  });
}
