import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export const NAME_MAX = 80;

/**
 * The user's own name for a run or a batch, editable in place.
 * Shows the name (or "Add a name"); click to edit; Enter or leaving the field saves, Escape cancels.
 * Saving an empty name removes it.
 */
export function NameEditor({ name, onSave, what = "run" }: { name?: string | null; onSave: (name: string) => Promise<unknown>; what?: string }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const cancelled = useRef(false);
  const saving = useRef(false);

  useEffect(() => {
    if (!editing) setValue(name ?? "");
  }, [name, editing]);
  useEffect(() => {
    if (editing) {
      cancelled.current = false; // removing a focused input does not always fire blur after Escape
      input.current?.focus();
      input.current?.select();
    }
  }, [editing]);

  const save = async () => {
    if (cancelled.current) {
      cancelled.current = false;
      return;
    }
    if (saving.current) return; // Enter blurs the field: one save, not two
    const v = value.trim();
    if (v === (name ?? "")) {
      setEditing(false);
      return;
    }
    saving.current = true;
    setBusy(true);
    try {
      await onSave(v);
      setErr(null);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };

  if (editing)
    return (
      <span className="row name-editor" style={{ gap: 6 }}>
        <input
          ref={input}
          value={value}
          maxLength={NAME_MAX}
          disabled={busy}
          placeholder={`Name this ${what}`}
          aria-label={`Name of this ${what}`}
          onChange={(e) => setValue(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            else if (e.key === "Escape") {
              cancelled.current = true;
              setValue(name ?? "");
              setErr(null);
              setEditing(false);
            }
          }}
          style={{ width: 260 }}
        />
        <span className="faint small">Enter to save · Esc to cancel</span>
        {err && <span className="small worse">{err}</span>}
      </span>
    );
  return (
    <span className="row name-editor" style={{ gap: 4, minWidth: 0 }}>
      {name ? (
        <>
          <span className="run-name ellipsis" title={name}>
            {name}
          </span>
          <button className="ghost icon small" onClick={() => setEditing(true)} aria-label={`Rename this ${what}`} title={`Rename this ${what}`}>
            <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M11.5 2.5l2 2L6 12H4v-2z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          </button>
        </>
      ) : (
        <button className="ghost small faint" onClick={() => setEditing(true)} title={`Give this ${what} a name you will remember`}>
          + Add a name
        </button>
      )}
    </span>
  );
}
