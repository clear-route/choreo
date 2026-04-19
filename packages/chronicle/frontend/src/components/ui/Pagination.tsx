import { rowBetween, btnGhost, rowGap2, caption } from "@/theme/styles";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onPageChange: (newOffset: number) => void;
}

export function Pagination({ total, limit, offset, onPageChange }: PaginationProps) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <div className={`${rowBetween} border-t border-border px-3 py-2 ${caption}`}>
      <span className="tabular-nums">
        {total === 0
          ? "No results"
          : `Showing ${offset + 1}\u2013${Math.min(offset + limit, total)} of ${total}`}
      </span>
      <div className={rowGap2}>
        <button
          className={btnGhost}
          disabled={offset === 0}
          onClick={() => onPageChange(Math.max(0, offset - limit))}
        >
          Previous
        </button>
        <span className="tabular-nums px-1">
          {currentPage} / {totalPages}
        </span>
        <button
          className={btnGhost}
          disabled={offset + limit >= total}
          onClick={() => onPageChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
