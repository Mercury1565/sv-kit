import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { RunSummary } from "../api/types";

const BUSY = new Set(["queued", "running"]);

/**
 * "Remove (N)" for the selected runs, with an inline confirmation (no browser dialog).
 * Queued / running runs are skipped; the server keeps runs that reused a removed result working.
 */
export function RemoveRuns({ runs, selected, onRemoved }: { runs: RunSummary[]; selected: Set<string>; onRemoved: (ids: string[]) => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const chosen = runs.filter((r) => selected.has(r.id));
  const removable = chosen.filter((r) => !BUSY.has(r.status));
  const skipped = chosen.length - removable.length;
  const n = removable.length;

  const remove = async () => {
    setBusy(true);
    const done: string[] = [];
    const errs: string[] = [];
    for (const r of removable) {
      try {
        await api.deleteRun(r.id);
        done.push(r.id);
      } catch (e) {
        errs.push(`${r.id}: ${e instanceof ApiError ? e.detail : String(e)}`);
      }
    }
    setBusy(false);
    setErrors(errs);
    if (!errs.length) setAsking(false);
    if (done.length) onRemoved(done);
  };

  return (
    <>
      <button
        onClick={() => {
          setErrors([]);
          setAsking((a) => !a);
        }}
        disabled={!chosen.length}
        aria-expanded={asking}
        title={chosen.length ? "Remove the selected runs and their files" : "Tick runs in the list to remove them"}
      >
        Remove{chosen.length ? ` (${chosen.length})` : ""}
      </button>
      {asking && chosen.length > 0 && (
        <div className="remove-confirm" role="alertdialog" aria-label="Confirm removing runs">
          <span>
            {n ? (
              <>
                Remove <b>{n === 1 ? removable[0].id : `${n} runs`}</b>
                {n === 1 && removable[0].name ? <> (“{removable[0].name}”)</> : null} and {n === 1 ? "its" : "their"} files? This cannot be undone.
                <span className="muted"> Runs that reused {n === 1 ? "its result" : "their results"} keep working.</span>
              </>
            ) : (
              "The selected runs are still running: cancel them first."
            )}
            {skipped > 0 && n > 0 && <span className="muted"> {skipped} still running will be kept.</span>}
          </span>
          <span className="spacer" />
          {n > 0 && (
            <button className="danger" onClick={remove} disabled={busy} autoFocus>
              {busy ? "Removing…" : `Remove ${n === 1 ? "run" : `${n} runs`}`}
            </button>
          )}
          <button onClick={() => setAsking(false)} disabled={busy}>
            Cancel
          </button>
          {errors.length > 0 && (
            <div className="small worse" style={{ flexBasis: "100%" }}>
              {errors.map((e) => (
                <div key={e}>Not removed: {e}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
