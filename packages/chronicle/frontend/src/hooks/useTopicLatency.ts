import { useQuery } from "@tanstack/react-query";
import { apiFetch, qs } from "@/api/client";
import type { TopicLatencyResponse } from "@/api/types";

interface UseTopicLatencyParams {
  topic: string;
  tenant: string;
  environment?: string;
  from?: string;
  to?: string;
}

export function useTopicLatency(params: UseTopicLatencyParams) {
  return useQuery({
    queryKey: ["topicLatency", params.topic, params.tenant, params.environment, params.from, params.to],
    queryFn: () =>
      apiFetch<TopicLatencyResponse>(
        `/topics/${encodeURIComponent(params.topic)}/latency${qs({
          tenant: params.tenant,
          environment: params.environment,
          from: params.from,
          to: params.to,
        })}`,
      ),
    staleTime: 30_000,
    enabled: !!params.tenant && !!params.topic,
  });
}
