import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { Group } from "../api/types";

/**
 * Inline confirmation for removing one batch (sim --all, matrix, sweep) with all its runs and files.
 * The server refuses (409) while any of its runs is queued or running; that message is shown here.
 */
export function RemoveBatchConfirm({ group, runs, onRemoved, onCancel }: { group: Group; runs?: number; onRemoved: () => void; onCancel: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteGroup(group.id);
      onRemoved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  };
  const count = runs === undefined ? "all its runs" : `its ${runs} ${runs === 1 ? "run" : "runs"}`;
  return (
    <div className="remove-confirm" role="alertdialog" aria-label="Confirm removing the batch">
      <span>
        Remove batch <b className="mono">{group.id}</b>
        {group.name ? <> (“{group.name}”)</> : null} with {count} and their files? This cannot be undone.
        <span className="muted"> Other batches are not affected.</span>
      </span>
      <span className="spacer" />
      <button className="danger" onClick={remove} disabled={busy} autoFocus>
        {busy ? "Removing…" : "Remove batch"}
      </button>
      <button onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      {error && (
        <div className="small worse" style={{ flexBasis: "100%" }}>
          Not removed: {error}
        </div>
      )}
    </div>
  );
}
