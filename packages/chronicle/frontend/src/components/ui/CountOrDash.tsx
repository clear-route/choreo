import { outcomeTextMuted } from "@/theme/styles";

export function CountOrDash({ value, style }: { value: number; style: string }) {
  return value > 0
    ? <span className={style}>{value}</span>
    : <span className={outcomeTextMuted}>{"\u2014"}</span>;
}
