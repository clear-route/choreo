import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import type { PagedResponse, TenantSummary } from "@/api/types";

export function useTenants() {
  return useQuery({
    queryKey: ["tenants"],
    queryFn: () => apiFetch<PagedResponse<TenantSummary>>("/tenants"),
    staleTime: 60_000,
  });
}
