import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { RunDetail } from "@/api/types";

export function useRunDetail(runId: string | undefined) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiFetch<RunDetail>(`/runs/${encodeURIComponent(runId!)}`),
    staleTime: 5 * 60_000,
    enabled: !!runId,
  });
}
