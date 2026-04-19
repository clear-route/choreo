import { useState, useMemo, type ReactNode } from "react";
import { mono, thead as theadStyle } from "@/theme/styles";

// ── Types ──

export interface Column<T> {
  /** Unique key — also the accessor into the row object. */
  key: string;
  /** Header label. */
  label: string;
  /** Alignment. Defaults to "left". */
  align?: "left" | "right";
  /** Whether this column is sortable. Defaults to true. */
  sortable?: boolean;
  /** Custom cell renderer. Receives the row and returns a ReactNode. */
  render?: (row: T) => ReactNode;
  /** Width hint (CSS value). */
  width?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  /** Key extractor — returns a unique string per row. */
  rowKey: (row: T, index: number) => string;
  /** Called when a row is clicked. */
  onRowClick?: (row: T) => void;
  /** Default sort column key. */
  defaultSort?: string;
  /** Default sort direction. */
  defaultDir?: "asc" | "desc";
  /** Empty state message. */
  emptyMessage?: string;
}

type SortDir = "asc" | "desc";

// ── Component ──

/**
 * Generic sortable data table — reusable across all views.
 *
 * Stateless except for sort state. Receives data via props,
 * renders a table with sortable column headers, hover rows,
 * and optional click handler.
 */
 
export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  rowKey,
  onRowClick,
  defaultSort,
  defaultDir = "desc",
  emptyMessage = "No data.",
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string>(defaultSort ?? columns[0]?.key ?? "");
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir);

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sorted = useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortDir === "asc"
        ? (av as number) - (bv as number)
        : (bv as number) - (av as number);
    });
  }, [data, sortKey, sortDir]);

  if (data.length === 0) {
    return (
      <div className="rounded border border-border bg-bg p-6 text-center text-[11px] text-text-subtle italic">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="rounded border border-border bg-bg overflow-hidden">
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr>
            {columns.map((col) => {
              const isActive = sortKey === col.key;
              const isSortable = col.sortable !== false;
              const arrow = isActive ? (sortDir === "asc" ? " \u2191" : " \u2193") : "";

              return (
                <th
                  key={col.key}
                  className={`${theadStyle} px-4 py-2.5 border-b border-border ${
                    col.align === "right" ? "text-right" : "text-left"
                  } ${isSortable ? "cursor-pointer hover:text-text select-none transition-colors duration-[120ms]" : ""}`}
                  style={col.width ? { width: col.width } : undefined}
                  onClick={isSortable ? () => toggleSort(col.key) : undefined}
                >
                  {col.label}{arrow}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, idx) => (
            <tr
              key={rowKey(row, idx)}
              className={`border-b border-border/30 hover:bg-surface-2 transition-colors duration-[120ms] ${
                onRowClick ? "cursor-pointer" : ""
              }`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={`px-4 py-2 ${mono} text-[11px] tabular-nums ${
                    col.align === "right" ? "text-right text-text-muted" : ""
                  }`}
                >
                  {col.render ? col.render(row) : formatCell(row[col.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): ReactNode {
  if (value == null) return <span className="text-border-strong">{"\u2014"}</span>;
  if (typeof value === "number") {
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(1);
  }
  return String(value);
}
