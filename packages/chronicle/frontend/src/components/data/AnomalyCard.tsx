import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/Badge";
import type { AnomalyCard as AnomalyCardType } from "@/api/types";
import { rowGap2, rowGap3, caption, body, link, mono } from "@/theme/styles";

interface AnomalyCardProps {
  anomaly: AnomalyCardType;
}

const methodLabels: Record<string, string> = {
  rolling_baseline: "Baseline",
  budget_violation: "Budget",
  outcome_shift: "Outcome",
};

export function AnomalyCard({ anomaly }: AnomalyCardProps) {
  const isResolved = anomaly.resolved;

  return (
    <div
      className={`rounded border p-3 transition-colors duration-[120ms] ${
        isResolved
          ? "border-border bg-surface opacity-60"
          : anomaly.severity === "critical"
            ? "border-fail/30 bg-fail-tint"
            : "border-slow/30 bg-slow-tint"
      }`}
    >
      <div className="flex items-start justify-between">
        <div>
          <div className={rowGap2}>
            <span className={`${mono} text-[12px] font-semibold`}>{anomaly.topic}</span>
            <Badge variant={anomaly.severity}>{anomaly.severity}</Badge>
            <span className={caption}>
              {methodLabels[anomaly.detection_method] ?? anomaly.detection_method}
            </span>
          </div>
          <p className={`mt-1 ${body}`}>
            {anomaly.metric}: <strong>{anomaly.current_value.toFixed(1)}</strong> vs
            baseline <strong>{anomaly.baseline_value.toFixed(1)}</strong>
            <span className="ml-2 text-[11px] text-text-subtle">
              ({anomaly.change_pct > 0 ? "+" : ""}
              {anomaly.change_pct.toFixed(1)}%)
            </span>
          </p>
        </div>
        <div className={`text-right ${caption} tabular-nums`}>
          <div>{new Date(anomaly.detected_at).toLocaleString("en-GB")}</div>
          {isResolved && anomaly.resolved_at && (
            <div className="mt-1 text-pass">
              Resolved {new Date(anomaly.resolved_at).toLocaleString("en-GB")}
            </div>
          )}
        </div>
      </div>
      <div className={`mt-2 ${rowGap3} text-[11px]`}>
        <Link to={`/runs/${anomaly.run_id}`} className={link}>View run</Link>
        <Link to={`/topics/${encodeURIComponent(anomaly.topic)}`} className={link}>Topic drilldown</Link>
      </div>
    </div>
  );
}
