import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { PagedResponse, AnomalyCard } from "@/api/types";

interface UseTopicAnomaliesParams {
  topic: string;
  tenant: string;
}

export function useTopicAnomalies(params: UseTopicAnomaliesParams) {
  return useQuery({
    queryKey: ["topicAnomalies", params.topic, params.tenant],
    queryFn: () =>
      apiFetch<PagedResponse<AnomalyCard>>(
        `/anomalies${qs({
          tenant: params.tenant,
          topic: params.topic,
          limit: 50,
        })}`,
      ),
    staleTime: 30_000,
    enabled: !!params.tenant && !!params.topic,
  });
}
