import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useRunDetail } from "@/hooks/useRunDetail";
import { useRunRaw, type RawTest } from "@/hooks/useRunRaw";
import { toHeroProps, formatDuration } from "@/transforms/runSummary";
import { groupTestsByFile } from "@/transforms/runDetail";
import { ProportionBar } from "@/components/data/ProportionBar";
import { FileTree } from "@/components/data/FileTree";
import { TestDetail } from "@/components/data/TestDetail";
import { Badge } from "@/components/ui/Badge";
import { LoadingSkeleton } from "@/components/ui/LoadingSkeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { btnGhost, fullHeight, linkBack, mono, caption, statGridCompact, statLabel } from "@/theme/styles";

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const { data: run, isLoading, isError, error, refetch } = useRunDetail(runId);
  const { data: rawReport, isLoading: rawLoading } = useRunRaw(runId);
  const [selectedTest, setSelectedTest] = useState<RawTest | null>(null);
  const [treeCollapsed, setTreeCollapsed] = useState(false);
  const [showMeta, setShowMeta] = useState(false);

  if (isLoading) return <div className="p-5"><LoadingSkeleton lines={12} /></div>;
  if (isError) {
    return (
      <div className="p-5">
        <Link to="/runs" className={linkBack}>&larr; Back to Runs</Link>
        <div className="mt-3">
          <ErrorState message={error.message} onRetry={() => void refetch()} />
        </div>
      </div>
    );
  }
  if (!run) return null;

  const heroProps = toHeroProps({
    ...run,
    anomaly_count: 0,
    pass_rate: run.total_tests > 0 ? run.total_passed / run.total_tests : 0,
    topic_count: 0,
    p50_ms: null,
    p95_ms: null,
    p99_ms: null,
  });

  const fileGroups = rawReport ? groupTestsByFile(rawReport) : [];

  return (
    <div className={`flex flex-col ${fullHeight}`}>
      {/* Compact sticky header */}
      <div className="shrink-0 bg-surface border-b border-border px-5 py-3">
        {/* Hero line: back link + headline + duration */}
        <div className="flex items-baseline justify-between gap-4">
          <div className="flex items-baseline gap-4 min-w-0">
            <Link to="/runs" className={`${linkBack} shrink-0`}>&larr;</Link>
            <p className="text-xl font-normal tracking-tight text-text-muted leading-[1.2] m-0">
              <strong className={`font-semibold tabular-nums ${
                heroProps.headlineOutcome === "fail" ? "text-fail" :
                heroProps.headlineOutcome === "slow" ? "text-slow" : "text-pass"
              }`}>
                {heroProps.headline.split(" ")[0]}
              </strong>
              {" "}{heroProps.headline.split(" ").slice(1).join(" ")}
            </p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className={`${mono} text-[13px] text-text-muted tabular-nums`}>{heroProps.duration}</span>
            <button
              onClick={() => setShowMeta(!showMeta)}
              className={btnGhost}
            >
              {showMeta ? "hide" : "about"}
            </button>
          </div>
        </div>

        {/* Proportion bar */}
        <div className="mt-2">
          <ProportionBar segments={heroProps.segments} />
        </div>

        {/* Sub-line: stats */}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-1 text-[12px] tabular-nums text-text-muted">
          {heroProps.stats.map((stat, i) => (
            <span key={i} className="flex items-center">
              {i > 0 && <span className="text-text-subtle/40 mx-1.5">&middot;</span>}
              <span className={stat.colour === "pass" ? "text-pass" : stat.colour === "fail" ? "text-fail" : stat.colour === "slow" ? "text-slow" : stat.isMono ? `${mono} text-text-subtle` : ""}>
                {stat.label}
              </span>
            </span>
          ))}
        </div>

        {/* Expandable metadata — hidden by default */}
        {showMeta && (
          <div className={`mt-3 pt-3 border-t border-border ${statGridCompact}`}>
            <Stat label="Started" value={new Date(run.started_at).toLocaleString("en-GB")} />
            <Stat label="Duration" value={formatDuration(run.duration_ms)} />
            <Stat label="Environment" value={run.environment || "not set"} />
            <Stat label="Transport" value={run.transport} />
            <Stat label="Branch" value={run.branch ?? "not set"} isMono />
            <Stat label="Git SHA" value={run.git_sha?.slice(0, 8) ?? "not set"} isMono />
            <Stat label="Project" value={run.project_name ?? "not set"} />
            <div>
              <div className={statLabel}>Totals</div>
              <div className="mt-0.5 flex gap-1.5">
                <Badge variant="pass">{run.total_passed}</Badge>
                {run.total_failed > 0 && <Badge variant="fail">{run.total_failed}</Badge>}
                {run.total_slow > 0 && <Badge variant="slow">{run.total_slow}</Badge>}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Split body — tree left, resizer, detail right */}
      <div className="flex flex-1 min-h-0">
        {/* Left: file tree */}
        {!treeCollapsed && (
          <div className="w-[32%] min-w-[240px] max-w-[50%] border-r border-border bg-surface overflow-y-auto">
            {rawLoading ? (
              <div className="p-3"><LoadingSkeleton lines={10} /></div>
            ) : fileGroups.length > 0 ? (
              <FileTree
                groups={fileGroups}
                selectedNodeId={selectedTest?.nodeid ?? null}
                onSelectTest={setSelectedTest}
              />
            ) : (
              <div className={`${caption} p-4 italic`}>No test data available.</div>
            )}
          </div>
        )}

        {/* Resizer / collapse toggle */}
        <div
          className="flex-shrink-0 w-2 bg-surface border-r border-border cursor-pointer hover:bg-info-tint transition-colors duration-[120ms] relative select-none"
          onClick={() => setTreeCollapsed(!treeCollapsed)}
          title={treeCollapsed ? "Show test tree (\\)" : "Hide test tree (\\)"}
        >
          <span className="absolute top-3 left-1/2 -translate-x-1/2 text-text-muted text-[14px] font-bold leading-none pointer-events-none">
            {treeCollapsed ? "\u203A" : "\u22EE"}
          </span>
        </div>

        {/* Right: test detail */}
        <div className="flex-1 overflow-y-auto bg-bg">
          {selectedTest ? (
            <TestDetail test={selectedTest} />
          ) : (
            <div className="flex items-center justify-center h-full">
              <p className={`${caption} italic`}>Select a test from the left to see its details.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, isMono }: { label: string; value: string; isMono?: boolean }) {
  return (
    <div>
      <div className={statLabel}>{label}</div>
      <div className={`mt-0.5 text-[13px] ${isMono ? `${mono} text-text-muted` : "text-text"}`}>{value}</div>
    </div>
  );
}
