// Widths of the Waves view's left columns (signal tree, signal names, value at cursor), in px.
// Remembered per browser; the waveform canvas takes whatever is left.
import { useCallback, useState } from "react";

export type Col = "side" | "names" | "vals";
export const COL_DEFAULT: Record<Col, number> = { side: 240, names: 300, vals: 136 };
export const COL_MIN: Record<Col, number> = { side: 120, names: 120, vals: 60 };
export const CANVAS_MIN = 200;
const KEY = "svkit.waves.columns";

function load(): Record<Col, number> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    const out = { ...COL_DEFAULT };
    for (const k of Object.keys(out) as Col[]) if (typeof v[k] === "number" && v[k] >= COL_MIN[k]) out[k] = v[k];
    return out;
  } catch {
    return { ...COL_DEFAULT };
  }
}

export function useColumns() {
  const [w, setW] = useState(load);
  const set = useCallback((k: Col, px: number) => {
    setW((cur) => {
      const next = { ...cur, [k]: px };
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* storage blocked: widths last for this page */
      }
      return next;
    });
  }, []);
  const reset = useCallback((k: Col) => set(k, COL_DEFAULT[k]), [set]);
  return { w, set, reset };
}
