import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { RunSummary } from "../api/types";
import { StatusChip } from "../components/StatusChip";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { fmtAgo, fmtDate, fmtDuration, fmtInt, shortSha } from "../lib/format";
import { useAsync, useDebounced, useServerEvents } from "../lib/hooks";
import { navigate } from "../lib/router";

export const isSynthKind = (k: string) => k === "synth" || k === "sweep-point";

export function runHref(r: Pick<RunSummary, "id" | "kind">): string {
  return isSynthKind(r.kind) ? `#/synth/${encodeURIComponent(r.id)}` : `#/runs/${encodeURIComponent(r.id)}`;
}

export function runLabel(r: RunSummary): string {
  return r.test ?? r.experiment ?? (Object.keys(r.knobs ?? {}).length ? Object.entries(r.knobs).map(([k, v]) => `${k}=${v}`).join(" ") : "");
}

export function filterRuns(runs: RunSummary[], f: { kind: string; status: string; tag: string; text: string }): RunSummary[] {
  const text = f.text.trim().toLowerCase();
  return runs.filter(
    (r) =>
      (!f.kind || r.kind === f.kind) &&
      (!f.status || r.status === f.status) &&
      (!f.tag || r.tags.some((t) => t.toLowerCase().includes(f.tag.toLowerCase()))) &&
      (!text ||
        r.id.toLowerCase().includes(text) ||
        (r.name ?? "").toLowerCase().includes(text) ||
        r.adapter.toLowerCase().includes(text) ||
        (r.test ?? "").toLowerCase().includes(text) ||
        (r.experiment ?? "").toLowerCase().includes(text) ||
        (r.git_sha ?? "").startsWith(text) ||
        (r.message ?? "").toLowerCase().includes(text)),
  );
}

export function RunsTable({ runs, selected, onToggle, height }: { runs: RunSummary[]; selected: Set<string>; onToggle: (id: string) => void; height?: number | string }) {
  const cols: Column<RunSummary>[] = useMemo(
    () => [
      {
        key: "sel",
        header: "",
        width: "28px",
        render: (r) =>
          isSynthKind(r.kind) ? (
            <input
              type="checkbox"
              aria-label={`select ${r.id} for compare`}
              checked={selected.has(r.id)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => onToggle(r.id)}
            />
          ) : null,
      },
      { key: "id", header: "Run", width: "84px", render: (r) => <span className="mono">{r.id}</span>, sortValue: (r) => r.id },
      { key: "kind", header: "Kind", width: "92px", render: (r) => r.kind, sortValue: (r) => r.kind },
      { key: "adapter", header: "Tool", width: "minmax(80px, 120px)", render: (r) => r.adapter, title: (r) => r.adapter, sortValue: (r) => r.adapter },
      {
        key: "label",
        header: "Name / test / experiment",
        width: "minmax(180px, 3fr)",
        render: (r) => (
          <span className="row" style={{ gap: 6 }}>
            {r.name && <span className="run-name ellipsis" style={{ flexShrink: 1 }}>{r.name}</span>}
            <span className={`ellipsis mono ${r.name ? "muted" : ""}`} style={r.name ? { flexShrink: 2 } : undefined}>
              {runLabel(r) || (r.name ? null : <span className="faint">—</span>)}
            </span>
            {r.cached_from && <span className="chip" title={`identical inputs: reused the result of ${r.cached_from}`}>reused</span>}
          </span>
        ),
        title: (r) => [r.name, runLabel(r), r.message].filter(Boolean).join("\n") || undefined,
        sortValue: (r) => r.name ?? runLabel(r),
      },
      { key: "status", header: "Status", width: "104px", render: (r) => <StatusChip status={r.status} />, sortValue: (r) => r.status },
      { key: "dur", header: "Duration", width: "84px", align: "right", render: (r) => fmtDuration(r.duration_s), sortValue: (r) => r.duration_s },
      {
        key: "git",
        header: "Git",
        width: "96px",
        render: (r) => (
          <span className="mono">
            {shortSha(r.git_sha)}
            {r.git_dirty ? <span className="dirty">*</span> : null}
          </span>
        ),
        title: (r) => (r.git_sha ? r.git_sha + (r.git_dirty ? " (dirty)" : "") : undefined),
      },
      {
        key: "tags",
        header: "Tags",
        width: "minmax(100px, 1.4fr)",
        render: (r) => (
          <span className="row" style={{ gap: 3, overflow: "hidden" }}>
            {r.tags.slice(0, 3).map((t) => (
              <span key={t} className="tag" title={t}>
                {t}
              </span>
            ))}
            {r.tags.length > 3 && <span className="faint small">+{r.tags.length - 3}</span>}
          </span>
        ),
        title: (r) => (r.tags.length ? r.tags.join(", ") : undefined),
      },
      { key: "time", header: "Created", width: "100px", align: "right", render: (r) => fmtAgo(r.created_at), title: (r) => fmtDate(r.created_at), sortValue: (r) => r.created_at },
    ],
    [selected, onToggle],
  );
  return (
    <VirtualTable
      rows={runs}
      columns={cols}
      rowKey={(r) => r.id}
      selected={selected}
      onRowClick={(r) => navigate(runHref(r))}
      height={height}
      emptyText="No runs match these filters."
      ariaLabel="Runs"
    />
  );
}

export function RunsView() {
  const runs = useAsync(() => api.allRuns(), []);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [textRaw, setText] = useState("");
  const text = useDebounced(textRaw, 120);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useServerEvents((ev) => {
    if (ev.type === "run.created") runs.setData((rs) => (rs ? [ev.run, ...rs.filter((r) => r.id !== ev.run.id)] : rs));
    else if (ev.type === "run.updated") runs.setData((rs) => rs?.map((r) => (r.id === ev.run.id ? { ...r, ...ev.run } : r)));
    else if (ev.type === "run.status")
      runs.setData((rs) => rs?.map((r) => (r.id === ev.run_id ? { ...r, status: ev.status, message: ev.message ?? r.message } : r)));
  });

  const all = runs.data ?? [];
  const rows = useMemo(() => filterRuns(all, { kind, status, tag, text }), [all, kind, status, tag, text]);
  const toggle = useMemo(
    () => (id: string) =>
      setSelected((s) => {
        const n = new Set(s);
        if (n.has(id)) n.delete(id);
        else n.add(id);
        return n;
      }),
    [],
  );

  if (runs.error) return <div className="view"><ErrorState error={runs.error} onRetry={runs.reload} /></div>;
  if (runs.loading && !runs.data) return <div className="view"><Loading what="Loading runs" /></div>;
  if (all.length === 0)
    return (
      <div className="view">
        <Empty title="No runs yet" cmd={["svkit sim <test>", "svkit synth", "svkit sweep clock"]}>
          Runs appear here as soon as a simulation or synthesis starts, from the CLI or the buttons in Tests and Synthesis.
        </Empty>
      </div>
    );

  const kinds = Array.from(new Set(all.map((r) => r.kind))).sort();
  const statuses = Array.from(new Set(all.map((r) => r.status))).sort();
  const sel = Array.from(selected);
  return (
    <div className="view fill">
      <div className="view-head">
        <h1>Runs</h1>
        <span className="muted small">
          {fmtInt(rows.length)} of {fmtInt(all.length)}
        </span>
        <span className="spacer" />
        <input placeholder="Filter: id, name, test, sha, message" value={textRaw} onChange={(e) => setText(e.target.value)} aria-label="Filter runs" style={{ width: 220 }} />
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {statuses.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <input placeholder="Tag" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Tag filter" style={{ width: 100 }} />
        <button className="primary" disabled={sel.length < 2} onClick={() => navigate(`/compare?ids=${sel.join(",")}`)} title="Select two or more synthesis runs">
          Compare{sel.length ? ` (${sel.length})` : ""}
        </button>
        {sel.length > 0 && (
          <button className="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        )}
      </div>
      <div style={{ flex: "1 1 auto", minHeight: 240, display: "flex" }}>
        <RunsTable runs={rows} selected={selected} onToggle={toggle} height="100%" />
      </div>
    </div>
  );
}
