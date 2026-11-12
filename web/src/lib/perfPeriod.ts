// The clock period behind CPI/TPI numbers, as text for chart subtitles and labels (so saved images say it).
import { fmtPs, isNum } from "./format";

export interface HasPeriod {
  period?: number | null;
  period_source?: string | null;
}

/** "svkit.toml" | "sweep:g_0002" | "synth:r_0001" | "manual" -> words. */
export function periodSourceText(src?: string | null): string {
  if (!src) return "";
  const [kind, id] = src.split(":");
  if (kind === "sweep") return `from clock sweep ${id ?? ""}`.trim();
  if (kind === "synth") return `from synthesis ${id ?? ""}`.trim();
  if (kind === "manual") return "entered by hand";
  return `from ${src}`;
}

/** One bar's period, short: "4,000 ps". */
export function periodShort(p: HasPeriod): string {
  return isNum(p.period) ? fmtPs(p.period) : "no period";
}

/** True when the bars do not all share one period (then each bar's label carries its own). */
export function periodsDiffer(items: HasPeriod[]): boolean {
  const ps = new Set(items.map((i) => (isNum(i.period) ? i.period : null)));
  return ps.size > 1;
}

/**
 * The subtitle line: "Clock period 4,000 ps (from svkit.toml)" when every bar shares it; else the range
 * with "per bar, under each label"; or that none is known.
 */
export function periodNote(items: HasPeriod[]): string {
  const known = items.filter((i) => isNum(i.period));
  if (!known.length) return "Clock period: not known for these runs (time per instruction needs one)";
  const ps = Array.from(new Set(known.map((i) => i.period as number))).sort((a, b) => a - b);
  const srcs = Array.from(new Set(known.map((i) => i.period_source ?? "")));
  const missing = items.length - known.length;
  const tail = missing ? `; ${missing} without one` : "";
  if (ps.length === 1) {
    const src = srcs.length === 1 && srcs[0] ? ` (${periodSourceText(srcs[0])})` : "";
    return `Clock period ${fmtPs(ps[0])}${src}${tail}`;
  }
  const from = srcs.length === 1 && srcs[0] ? `, all ${periodSourceText(srcs[0])}` : "";
  return `Clock period ${fmtPs(ps[0])} – ${fmtPs(ps[ps.length - 1])}: each run's own, under its label${from}${tail}`;
}
