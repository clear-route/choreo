import { rowGap2, segmentGroup, segmentActive, segmentInactive, inputCompact, caption } from "@/theme/styles";

const presets = [
  { label: "24h", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
] as const;

interface TimeRangePickerProps {
  from: string | null;
  to: string | null;
  onChange: (from: string, to: string) => void;
}

export function TimeRangePicker({ from, to, onChange }: TimeRangePickerProps) {
  const activePreset = presets.find((p) => {
    if (!from || !to) return false;
    const diff = new Date(to).getTime() - new Date(from).getTime();
    return Math.abs(diff - p.days * 86_400_000) < 3_600_000;
  });

  const handlePreset = (days: number) => {
    const now = new Date();
    const start = new Date(now.getTime() - days * 86_400_000);
    onChange(start.toISOString(), now.toISOString());
  };

  return (
    <div className={rowGap2}>
      <div className={segmentGroup}>
        {presets.map((p) => (
          <button
            key={p.label}
            onClick={() => handlePreset(p.days)}
            className={activePreset?.label === p.label ? segmentActive : segmentInactive}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1 ml-3 pl-3 border-l border-border">
        <input
          type="date"
          value={from?.slice(0, 10) ?? ""}
          onChange={(e) => onChange(new Date(e.target.value).toISOString(), to ?? new Date().toISOString())}
          className={inputCompact}
        />
        <span className={caption}>to</span>
        <input
          type="date"
          value={to?.slice(0, 10) ?? ""}
          onChange={(e) => onChange(from ?? new Date().toISOString(), new Date(e.target.value).toISOString())}
          className={inputCompact}
        />
      </div>
    </div>
  );
}
