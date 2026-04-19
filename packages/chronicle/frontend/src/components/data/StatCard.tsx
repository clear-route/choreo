import { cardPadded, chartTitle, mono } from "@/theme/styles";

interface StatCardProps {
  label: string;
  value: string;
  colour?: string;  // Tailwind text colour class, defaults to "text-text"
}

export function StatCard({ label, value, colour = "text-text" }: StatCardProps) {
  return (
    <div className={cardPadded}>
      <div className={chartTitle}>{label}</div>
      <div className={`text-[15px] font-semibold tracking-tight ${mono} tabular-nums ${colour}`}>
        {value}
      </div>
    </div>
  );
}
