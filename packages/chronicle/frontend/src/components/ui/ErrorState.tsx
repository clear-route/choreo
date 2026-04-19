import { btnDanger, errorState, labelSmall } from "@/theme/styles";

interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className={errorState}>
      <p className={`${labelSmall} text-fail`}>{message}</p>
      {onRetry && (
        <button className={btnDanger} onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
