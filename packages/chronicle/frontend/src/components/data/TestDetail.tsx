import { useState } from "react";
import { Link } from "react-router-dom";
import type { RawTest, RawHandle, RawScenario, RawTimelineEntry } from "@/hooks/useRunRaw";
import { formatMs } from "@/transforms/runDetail";
import { Badge } from "@/components/ui/Badge";
import { mono, caption, link, caretButtonSmall } from "@/theme/styles";

interface TestDetailProps {
  test: RawTest;
}

/** Right-panel detail view for a selected test — scenarios, handles, timeline. */
export function TestDetail({ test }: TestDetailProps) {
  return (
    <div className="py-4 px-5">
      {/* Test header */}
      <h2 className="text-[15px] font-semibold text-text mb-1 leading-tight">{test.name}</h2>
      <div className={`${mono} text-[11px] text-text-muted mb-4 break-all`}>{test.nodeid}</div>

      {test.scenarios.length > 0 ? (
        test.scenarios.map((scenario, i) => (
          <ScenarioSection key={`${scenario.name}-${i}`} scenario={scenario} />
        ))
      ) : test.traceback ? (
        <pre className={`${mono} text-[11px] text-fail whitespace-pre-wrap p-2 bg-fail-tint rounded-sm`}>
          {test.traceback}
        </pre>
      ) : (
        <div className={`${caption} py-4 italic`}>
          No Choreo scenarios in this test.
        </div>
      )}
    </div>
  );
}

function ScenarioSection({ scenario }: { scenario: RawScenario }) {
  const [expanded, setExpanded] = useState(true);
  const topics = [...new Set(scenario.handles.map((h) => h.topic))];

  return (
    <div className="[&+&]:border-t [&+&]:border-border [&+&]:pt-3 pb-2">
      <div
        className="flex items-center gap-3 py-2 border-b border-border mb-1.5 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <Caret open={expanded} />
        <Badge variant={scenario.outcome}>
          {scenario.outcome === "pass" ? "\u2713" : scenario.outcome.slice(0, 1).toUpperCase()}
        </Badge>
        <span className={`${mono} text-[12px] text-text-muted`}>
          <span className="text-text-subtle font-normal">scenario </span>
          <span className="font-medium">{scenario.name}</span>
        </span>
        <span className={caption}>{scenario.handles.length} handle{scenario.handles.length !== 1 ? "s" : ""}</span>
        {topics.length > 0 && topics.length <= 3 && (
          <span className={`${mono} text-[10px] text-text-subtle truncate`}>
            {topics.join(", ")}
          </span>
        )}
        <span className={`ml-auto ${mono} text-[11px] text-text-subtle tabular-nums`}>
          {formatMs(scenario.duration_ms)}
        </span>
      </div>

      {expanded && (
        <div>
          {scenario.handles.map((handle, i) => (
            <HandleRow key={`${handle.topic}-${i}`} handle={handle} />
          ))}
          {scenario.timeline.length > 0 && (
            <TimelineSection timeline={scenario.timeline} />
          )}
        </div>
      )}
    </div>
  );
}

function HandleRow({ handle }: { handle: RawHandle }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="first:border-t-0 border-t border-border pt-2 pb-2">
      <div
        className="flex items-center gap-3 text-[12px] cursor-pointer py-1"
        onClick={() => setExpanded(!expanded)}
      >
        <Caret open={expanded} />
        <Badge variant={handle.outcome}>
          {handle.outcome === "pass" ? "\u2713" : handle.outcome.slice(0, 1).toUpperCase()}
        </Badge>
        <span className={`${mono} text-[11px] flex items-center gap-1.5`}>
          <span className="text-text-subtle text-[10px] uppercase tracking-[0.05em]">topic</span>
          <span className="bg-surface-2 border border-border rounded-sm px-2 py-px">
            <Link
              to={`/topics/${encodeURIComponent(handle.topic)}`}
              className={link}
              onClick={(e) => e.stopPropagation()}
            >
              {handle.topic}
            </Link>
          </span>
        </span>
        <span className={`text-[10px] uppercase tracking-[0.05em] text-text-subtle`}>
          {handle.diagnosis.kind}
        </span>
        <span className={`ml-auto ${mono} text-[11px] text-text-subtle tabular-nums`}>
          {handle.latency_ms != null ? formatMs(handle.latency_ms) : "—"}
        </span>
      </div>

      {expanded && (
        <div className="pl-8 py-2">
          {/* Diagnosis note */}
          {handle.diagnosis.kind === "matched" && (
            <div className={`${mono} text-[11px] text-pass bg-pass-tint rounded-sm px-2 py-1 mb-2`}>
              Matched in {handle.latency_ms != null ? formatMs(handle.latency_ms) : "—"}
              {handle.attempts > 0 && ` after ${handle.attempts} attempt${handle.attempts !== 1 ? "s" : ""}`}
            </div>
          )}
          {handle.diagnosis.kind === "silent_timeout" && (
            <div className={`${mono} text-[11px] text-fail bg-fail-tint rounded-sm px-2 py-1 mb-2`}>
              Silent timeout — no message arrived on {handle.topic}
            </div>
          )}
          {handle.diagnosis.kind === "over_budget" && (
            <div className={`${mono} text-[11px] text-slow bg-slow-tint rounded-sm px-2 py-1 mb-2`}>
              Over budget: {handle.latency_ms != null ? formatMs(handle.latency_ms) : "?"} vs {handle.budget_ms != null ? `${formatMs(handle.budget_ms)} budget` : "no budget"}
            </div>
          )}
          {handle.diagnosis.kind === "near_miss" && (
            <div className={`${mono} text-[11px] text-fail bg-fail-tint rounded-sm px-2 py-1 mb-2`}>
              Near miss — message arrived but matcher rejected it
            </div>
          )}

          {/* Budget bar */}
          {handle.budget_ms != null && handle.latency_ms != null && (
            <BudgetBar latencyMs={handle.latency_ms} budgetMs={handle.budget_ms} />
          )}

          {/* Matcher */}
          <div className={`${caption} mt-1`}>
            matcher: <code className={`${mono} bg-surface-2 px-1 rounded-sm text-text`}>{handle.matcher_description}</code>
          </div>

          {/* Expected / Actual panels */}
          {handle.expected != null && handle.actual != null && (
            <div className="grid grid-cols-2 gap-3 mt-2">
              <JsonPanel title="Expected" data={handle.expected} />
              <JsonPanel title="Actual" data={handle.actual} />
            </div>
          )}

          {/* Reason */}
          {handle.reason && handle.outcome !== "pass" && (
            <div className={`${mono} text-[11px] text-fail bg-fail-tint rounded-sm px-2 py-1 mt-2`}>
              {handle.reason}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BudgetBar({ latencyMs, budgetMs }: { latencyMs: number; budgetMs: number }) {
  const maxMs = Math.max(latencyMs, budgetMs) * 1.1;
  const budgetPct = (budgetMs / maxMs) * 100;
  const latencyPct = (latencyMs / maxMs) * 100;
  const isOver = latencyMs > budgetMs;

  return (
    <div className="my-2">
      <div className={`${mono} text-[11px] text-text-muted mb-1 flex gap-2 items-baseline`}>
        <span>{formatMs(latencyMs)}</span>
        <span className="text-text-subtle">/</span>
        <span>{formatMs(budgetMs)} budget</span>
        {isOver && <strong className="text-slow">over</strong>}
      </div>
      <div className="relative h-1.5 bg-surface-2 rounded-full overflow-hidden">
        <div
          className="absolute top-0 bottom-0 left-0 bg-pass"
          style={{ width: `${Math.min(budgetPct, latencyPct)}%` }}
        />
        {isOver && (
          <div
            className="absolute top-0 bottom-0 bg-slow"
            style={{ left: `${budgetPct}%`, width: `${latencyPct - budgetPct}%` }}
          />
        )}
        <div
          className="absolute top-[-2px] bottom-[-2px] w-[2px] bg-text/50"
          style={{ left: `${budgetPct}%` }}
        />
      </div>
    </div>
  );
}

/** Waterfall timeline */
function TimelineSection({ timeline }: { timeline: RawTimelineEntry[] }) {
  const maxMs = timeline.length > 0 ? Math.max(...timeline.map((e) => e.offset_ms)) : 1;

  const textColour: Record<string, string> = {
    published: "text-info", matched: "text-pass", deadline: "text-fail",
    received: "text-text-muted", replied: "text-pass", mismatched: "text-fail",
  };
  const barBg: Record<string, string> = {
    published: "bg-info", matched: "bg-pass", deadline: "bg-fail",
    received: "bg-text-subtle", replied: "bg-pass", mismatched: "bg-fail",
  };
  const label = (a: string) => a === "deadline" ? "timeout" : a;
  const gridCols = "minmax(200px, 1fr) minmax(180px, 320px) 72px";

  return (
    <div className="mt-3">
      <h4 className="text-[10px] font-semibold uppercase tracking-[0.08em] text-text-muted mb-2 flex justify-between items-baseline">
        <span>Timeline</span>
        <small className={`${mono} text-text-subtle font-normal normal-case tracking-normal`}>{formatMs(maxMs)} total</small>
      </h4>
      <div className="border border-border rounded-sm bg-bg overflow-hidden">
        <div className={`grid items-center ${mono} text-[10px] bg-surface border-b border-border py-1.5`} style={{ gridTemplateColumns: gridCols }}>
          <span className="pl-3 uppercase tracking-[0.05em] text-text-subtle">Event</span>
          <span className="relative h-3 mx-2">
            {[0, 25, 50, 75, 100].map((pct) => (
              <span key={pct} className="absolute top-0 text-text-subtle tabular-nums whitespace-nowrap" style={{ left: `${pct}%`, transform: "translateX(-50%)" }}>
                {pct === 0 ? "0" : formatMs((maxMs * pct) / 100)}
              </span>
            ))}
          </span>
          <span className="text-right pr-3 uppercase tracking-[0.05em] text-text-subtle">Offset</span>
        </div>
        {timeline.map((entry, i) => {
          const pct = (entry.offset_ms / maxMs) * 100;
          const isDeadline = entry.action === "deadline";
          return (
            <div key={i} className={`grid items-center ${mono} text-[11px] py-0.5 border-b border-surface-2 last:border-b-0 ${isDeadline ? "bg-fail-tint font-semibold" : ""}`} style={{ gridTemplateColumns: gridCols }}>
              <span className="pl-3 flex items-center gap-1.5 min-w-0 pr-2">
                <span className="truncate text-text">{entry.topic}</span>
                <span className={`shrink-0 text-[10px] uppercase tracking-[0.05em] font-semibold ${textColour[entry.action] ?? "text-text-muted"}`}>{label(entry.action)}</span>
              </span>
              <span className="relative h-[18px] mx-2" style={{ background: "linear-gradient(to right, transparent calc(25% - 0.5px), var(--color-surface-2) calc(25% - 0.5px), var(--color-surface-2) 25%, transparent 25%, transparent calc(50% - 0.5px), var(--color-surface-2) calc(50% - 0.5px), var(--color-surface-2) 50%, transparent 50%, transparent calc(75% - 0.5px), var(--color-surface-2) calc(75% - 0.5px), var(--color-surface-2) 75%, transparent 75%)" }}>
                <span className={`absolute top-[5px] h-[8px] left-0 rounded-full ${barBg[entry.action] ?? "bg-text-subtle"} opacity-30`} style={{ width: `${Math.max(pct, 1)}%` }} />
                <span className={`absolute top-[3px] h-[12px] w-[3px] rounded-full ${barBg[entry.action] ?? "bg-text-subtle"}`} style={{ left: `${pct}%`, transform: "translateX(-50%)" }} />
              </span>
              <span className="text-right pr-3 text-text-subtle tabular-nums">{formatMs(entry.offset_ms)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function JsonPanel({ title, data }: { title: string; data: unknown }) {
  return (
    <div className="border border-border rounded-sm bg-surface overflow-hidden">
      <div className="px-3 py-1 text-[10px] uppercase tracking-[0.08em] text-text-muted bg-surface-2 border-b border-border font-semibold">
        {title}
      </div>
      <pre className={`${mono} text-[11px] leading-[1.4] p-2 whitespace-pre-wrap break-words text-text overflow-auto max-h-[300px]`}>
        {typeof data === "string" ? data : JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function Caret({ open }: { open: boolean }) {
  return (
    <span className={`${caretButtonSmall} ${open ? "" : "-rotate-90"}`}>
      &#x25BE;
    </span>
  );
}
