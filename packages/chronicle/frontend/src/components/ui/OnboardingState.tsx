import { useTenants } from "@/hooks/useTenants";
import { emptyState, caption, mono } from "@/theme/styles";

interface OnboardingStateProps {
  /** What the user is trying to see (e.g. "runs", "topics"). */
  resource: string;
}

/**
 * Replaces the FilterBar + EmptyState combo when no tenant is available.
 *
 * Three states:
 * 1. API unreachable — connection error, suggest starting the backend
 * 2. No tenants exist — fresh install, show the curl onboarding command
 * 3. Tenants loading — brief skeleton
 */
export function OnboardingState({ resource }: OnboardingStateProps) {
  const { data, isLoading, isError } = useTenants();
  const tenants = data?.items ?? [];

  if (isLoading) {
    return (
      <div className={emptyState}>
        <p className={caption}>Connecting...</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className={emptyState}>
        <p className={`${caption} text-fail`}>Cannot reach the Chronicle API.</p>
        <p className={`mt-2 ${caption}`}>
          Start the backend with{" "}
          <code className={`${mono} bg-surface-2 px-1 rounded-sm`}>
            uvicorn chronicle.app:create_app --factory --port 8000
          </code>
        </p>
      </div>
    );
  }

  if (tenants.length === 0) {
    return (
      <div className={emptyState}>
        <p className={`${caption} font-medium`}>No tenants yet</p>
        <p className={`mt-2 ${caption}`}>
          Ingest your first report to create a tenant automatically:
        </p>
        <pre className={`mt-3 rounded border border-border bg-surface-2 px-4 py-3 ${mono} text-[11px] text-text-muted text-left max-w-lg`}>
{`curl -X POST \\
  -H "Content-Type: application/json" \\
  -H "X-Chronicle-Tenant: my-team" \\
  http://localhost:8000/api/v1/runs \\
  -d @test-report/results.json`}
        </pre>
      </div>
    );
  }

  // Tenants exist but none is selected — this shouldn't normally render
  // because the route components check for tenant before reaching here.
  return (
    <div className={emptyState}>
      <p className={caption}>Select a tenant to view {resource}.</p>
    </div>
  );
}
