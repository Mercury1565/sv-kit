import { useMemo, useRef, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  width: string; // CSS grid track, e.g. "90px" or "minmax(160px, 2fr)"
  render: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number | null | undefined;
  align?: "left" | "right";
  title?: (row: T) => string | undefined;
}

export function visibleRange(scrollTop: number, viewport: number, rowH: number, count: number, overscan = 8): [number, number] {
  const first = Math.max(0, Math.floor(scrollTop / rowH) - overscan);
  const last = Math.min(count, Math.ceil((scrollTop + viewport) / rowH) + overscan);
  return [first, last];
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T, ev: React.MouseEvent) => void;
  selected?: Set<string>;
  rowHeight?: number;
  height?: number | string;
  emptyText?: ReactNode;
  initialSort?: { key: string; dir: 1 | -1 };
  ariaLabel?: string;
}

/** Row-virtualized grid table: only rows in view (+ overscan) are in the DOM. */
export function VirtualTable<T>({ rows, columns, rowKey, onRowClick, selected, rowHeight = 26, height = "100%", emptyText, initialSort, ariaLabel }: Props<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    return rows
      .map((r, i) => ({ r, i, v: sv(r) }))
      .sort((a, b) => {
        const av = a.v;
        const bv = b.v;
        const an = av === null || av === undefined || (typeof av === "number" && Number.isNaN(av));
        const bn = bv === null || bv === undefined || (typeof bv === "number" && Number.isNaN(bv));
        if (an && bn) return a.i - b.i;
        if (an) return 1; // missing values always last
        if (bn) return -1;
        const c = av! < bv! ? -1 : av! > bv! ? 1 : 0;
        return c * sort.dir || a.i - b.i;
      })
      .map((x) => x.r);
  }, [rows, columns, sort]);

  const viewport = ref.current?.clientHeight || 640;
  const [first, last] = visibleRange(scrollTop, viewport, rowHeight, sorted.length);
  const template = columns.map((c) => c.width).join(" ");
  // header and rows are separate grids: give them the same minimum width so columns line up
  const minWidth = columns.reduce((s, c) => s + (Number(/(\d+)px/.exec(c.width)?.[1]) || 80), 0);

  const onSort = (c: Column<T>) => {
    if (!c.sortValue) return;
    setSort((s) => (s && s.key === c.key ? (s.dir === 1 ? { key: c.key, dir: -1 } : null) : { key: c.key, dir: 1 }));
  };

  return (
    <div className="vt" ref={ref} style={{ height }} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)} role="table" aria-label={ariaLabel} aria-rowcount={sorted.length}>
      <div className="vt-head" style={{ gridTemplateColumns: template, minWidth }} role="row">
        {columns.map((c) => (
          <div
            key={c.key}
            role="columnheader"
            className={`vt-cell ${c.sortValue ? "sortable" : ""}`}
            style={{ textAlign: c.align ?? "left" }}
            onClick={() => onSort(c)}
            aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
          >
            {c.header}
            {sort?.key === c.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
          </div>
        ))}
      </div>
      {sorted.length === 0 ? (
        <div className="state small">{emptyText ?? "No rows"}</div>
      ) : (
        <div style={{ height: sorted.length * rowHeight, position: "relative", minWidth }}>
          {sorted.slice(first, last).map((r, k) => {
            const i = first + k;
            const key = rowKey(r);
            return (
              <div
                key={key}
                role="row"
                aria-rowindex={i + 1}
                className={`vt-row ${selected?.has(key) ? "sel" : ""}`}
                style={{ top: i * rowHeight, gridTemplateColumns: template, minWidth, cursor: onRowClick ? "pointer" : undefined }}
                onClick={onRowClick ? (e) => onRowClick(r, e) : undefined}
              >
                {columns.map((c) => (
                  <div key={c.key} role="cell" className="vt-cell" style={{ textAlign: c.align ?? "left" }} title={c.title?.(r)}>
                    {c.render(r)}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
