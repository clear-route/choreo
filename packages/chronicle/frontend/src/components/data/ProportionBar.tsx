import type { ProportionSegment } from "@/transforms/runSummary";
import { proportionBar } from "@/theme/styles";

const segmentColours: Record<string, string> = {
  passed: "bg-pass",
  slow: "bg-slow",
  failed: "bg-fail",
  errored: "bg-fail brightness-[0.8]",
  skipped: "bg-skip",
};

interface ProportionBarProps {
  segments: ProportionSegment[];
}

/** 6px coloured segment bar */
export function ProportionBar({ segments }: ProportionBarProps) {
  return (
    <div className={proportionBar}>
      {segments.map((seg) => (
        <span
          key={seg.key}
          className={segmentColours[seg.key] ?? "bg-text-subtle"}
          style={{ width: `${seg.fraction * 100}%` }}
        />
      ))}
    </div>
  );
}
