import type { FileGroup } from "@/transforms/runDetail";
import type { RawTest } from "@/hooks/useRunRaw";
import { formatMs } from "@/transforms/runDetail";
import { Badge } from "@/components/ui/Badge";
import { mono, caption, input, segmentGroup, segmentActive, segmentInactive, caretButton } from "@/theme/styles";
import { useState, useMemo } from "react";

interface FileTreeProps {
  groups: FileGroup[];
  selectedNodeId: string | null;
  onSelectTest: (test: RawTest) => void;
}

type OutcomeFilter = "passed" | "failed" | "slow" | "timeout" | "skipped" | "errored";

const FILTERS: { key: OutcomeFilter; label: string }[] = [
  { key: "passed", label: "passed" },
  { key: "failed", label: "failed" },
  { key: "slow", label: "slow" },
  { key: "timeout", label: "timeout" },
  { key: "skipped", label: "skipped" },
  { key: "errored", label: "errored" },
];

/** Left panel — file-grouped test tree */
export function FileTree({ groups, selectedNodeId, onSelectTest }: FileTreeProps) {
  const [activeFilters, setActiveFilters] = useState<Set<OutcomeFilter>>(new Set(["passed", "failed", "slow", "timeout", "skipped", "errored"]));
  const [search, setSearch] = useState("");

  const toggleFilter = (key: OutcomeFilter) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // Filter tests by outcome and search
  const filteredGroups = useMemo(() => {
    const searchLower = search.toLowerCase();
    return groups.map((group) => {
      const filtered = group.tests.filter((test) => {
        const outcome = effectiveOutcome(test);
        const outcomeMatch = activeFilters.has(outcome as OutcomeFilter) ||
          (outcome === "passed" && activeFilters.has("passed"));
        const searchMatch = !searchLower ||
          test.name.toLowerCase().includes(searchLower) ||
          test.nodeid.toLowerCase().includes(searchLower);
        return outcomeMatch && searchMatch;
      });
      return { ...group, tests: filtered, testCount: filtered.length };
    }).filter((g) => g.testCount > 0);
  }, [groups, activeFilters, search]);

  // Count visible tests
  const visibleCount = filteredGroups.reduce((sum, g) => sum + g.testCount, 0);
  const totalCount = groups.reduce((sum, g) => sum + g.testCount, 0);

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar — outcome filters + search */}
      <div className="shrink-0 bg-surface border-b border-border px-3 py-2 space-y-2">
        {/* Outcome filter toggles */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className={segmentGroup}>
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => toggleFilter(f.key)}
                className={activeFilters.has(f.key) ? segmentActive : segmentInactive}
              >
                {f.label}
              </button>
            ))}
          </div>
          <span className={`${caption} tabular-nums ml-auto`}>
            {visibleCount === totalCount ? `${totalCount} tests` : `${visibleCount} of ${totalCount}`}
          </span>
        </div>
        {/* Search */}
        <input
          type="search"
          placeholder="search tests..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className={`${input} w-full`}
        />
      </div>

      {/* File tree — scrollable */}
      <div className="flex-1 overflow-y-auto py-1">
        {filteredGroups.length === 0 ? (
          <div className={`${caption} p-4 italic text-center`}>No tests match the current filters.</div>
        ) : (
          filteredGroups.map((group) => (
            <FileSection
              key={group.file}
              group={group}
              selectedNodeId={selectedNodeId}
              onSelectTest={onSelectTest}
              autoExpand={!!search || filteredGroups.length === 1}
            />
          ))
        )}
      </div>
    </div>
  );
}

function FileSection({
  group,
  selectedNodeId,
  onSelectTest,
  autoExpand,
}: {
  group: FileGroup;
  selectedNodeId: string | null;
  onSelectTest: (test: RawTest) => void;
  autoExpand: boolean;
}) {
  const [expanded, setExpanded] = useState(autoExpand);

  // Auto-expand when search is active
  const isExpanded = expanded || autoExpand;

  return (
    <div className="mb-0.5">
      <button
        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-semibold ${mono} text-text rounded-sm hover:bg-surface-2 transition-colors duration-[120ms]`}
        onClick={() => setExpanded(!expanded)}
      >
        <Caret open={isExpanded} />
        <span className="flex flex-col min-w-0 flex-1 leading-[1.25]">
          <span className="text-[11px] font-normal text-text-muted truncate" dir="rtl" style={{ textAlign: "left" }}>
            {group.dir}
          </span>
          <span className="truncate">{group.basename}</span>
        </span>
        <span className={`${caption} tabular-nums shrink-0`}>{group.testCount} tests</span>
        {group.failCount > 0 && (
          <span className="bg-fail text-badge-fg text-[10px] font-semibold px-2 rounded-full tabular-nums leading-[1.3] min-w-[16px] text-center">
            {group.failCount}
          </span>
        )}
        {group.slowCount > 0 && (
          <span className="bg-slow text-badge-fg text-[10px] font-semibold px-2 rounded-full tabular-nums leading-[1.3] min-w-[16px] text-center">
            {group.slowCount}
          </span>
        )}
      </button>
      {isExpanded && (
        <div>
          {group.tests.map((test) => (
            <TestRow
              key={test.nodeid}
              test={test}
              isSelected={test.nodeid === selectedNodeId}
              onSelect={() => onSelectTest(test)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Derive the visual outcome for a test from the raw JSON.
 *  Uses the test-level outcome, falling back to the worst scenario/handle
 *  outcome when the test is marked as "passed" but contains non-pass scenarios.
 *  This does NOT recompute — it reads what the reporter wrote. */
function effectiveOutcome(test: RawTest): string {
  if (test.outcome !== "passed") return test.outcome;
  // Check raw scenario outcomes (these ARE in the raw JSON)
  for (const s of test.scenarios) {
    if (s.outcome === "fail" || s.outcome === "timeout") return s.outcome;
  }
  return test.outcome;
}

function TestRow({
  test,
  isSelected,
  onSelect,
}: {
  test: RawTest;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const outcome = effectiveOutcome(test);
  const outcomeClass =
    outcome === "failed" || outcome === "errored"
      ? "border-l-fail bg-fail-tint"
      : outcome === "slow"
        ? "border-l-slow bg-slow-tint"
        : outcome === "timeout"
          ? "border-l-fail bg-fail-tint"
          : isSelected
            ? "border-l-info bg-surface-2"
            : "border-l-transparent";

  return (
    <div
      className={`flex items-center gap-2 pl-8 pr-3 py-1 text-[13px] border-l-2 cursor-pointer hover:bg-surface-2 transition-colors duration-[120ms] ${outcomeClass}`}
      onClick={onSelect}
    >
      <Badge variant={outcome}>
        {outcome === "passed" ? "\u2713" : outcome.slice(0, 1).toUpperCase()}
      </Badge>
      <span className={`flex-1 min-w-0 truncate ${mono} text-[12px] text-text`}>
        {test.name}
      </span>
      <span className={`${mono} text-[11px] text-text-subtle tabular-nums shrink-0`}>
        {formatMs(test.duration_ms)}
      </span>
    </div>
  );
}

function Caret({ open }: { open: boolean }) {
  return (
    <span className={`${caretButton} ${open ? "" : "-rotate-90"}`}>&#x25BE;</span>
  );
}
