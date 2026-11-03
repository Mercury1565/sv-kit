const CLS: Record<string, string> = {
  passed: "good",
  done: "good",
  failed: "bad",
  error: "bad",
  running: "run",
  queued: "",
  cancelled: "warn",
};

const GLYPH: Record<string, string> = {
  passed: "✓",
  done: "✓",
  failed: "✕",
  error: "!",
  running: "●",
  queued: "…",
  cancelled: "–",
};

export function StatusChip({ status }: { status: string }) {
  return (
    <span className={`chip ${CLS[status] ?? ""}`} title={status}>
      <span aria-hidden>{GLYPH[status] ?? "?"}</span>
      {status}
    </span>
  );
}

/** Timing chip: met / violated from a slack value (ps). */
export function SlackChip({ wns, hold }: { wns?: number | null; hold?: number | null }) {
  const bad = (typeof wns === "number" && wns < 0) || (typeof hold === "number" && hold < 0);
  const unknown = typeof wns !== "number" || !Number.isFinite(wns);
  if (unknown) return <span className="chip">timing ?</span>;
  return <span className={`chip ${bad ? "bad" : "good"}`}>{bad ? "✕ violated" : "✓ met"}</span>;
}
