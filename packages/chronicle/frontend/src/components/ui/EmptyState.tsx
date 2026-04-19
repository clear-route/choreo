import { emptyState, caption } from "@/theme/styles";

interface EmptyStateProps {
  message: string;
}

export function EmptyState({ message }: EmptyStateProps) {
  return (
    <div className={emptyState}>
      <p className={`${caption} italic`}>{message}</p>
    </div>
  );
}
