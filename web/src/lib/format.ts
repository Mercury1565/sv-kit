// Number / time formatting. Every renderer goes through these so null, NaN and
// huge values never reach the screen raw.

export const DASH = "—";
const MINUS = "−";

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

const nfCache = new Map<string, Intl.NumberFormat>();
function nf(min: number, max: number, sign = false): Intl.NumberFormat {
  const k = `${min}:${max}:${sign}`;
  let f = nfCache.get(k);
  if (!f) {
    f = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
      signDisplay: sign ? "exceptZero" : "auto",
    });
    nfCache.set(k, f);
  }
  return f;
}

function fixMinus(s: string): string {
  return s.replace(/^-/, MINUS).replace(/^\+-/, MINUS);
}

/** Generic number: thousands separators, at most `digits` decimals (fewer for big values). */
export function fmtNum(v: unknown, digits = 2): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v !== "number") return String(v);
  if (Number.isNaN(v)) return "NaN";
  if (!Number.isFinite(v)) return v > 0 ? "∞" : `${MINUS}∞`;
  const a = Math.abs(v);
  const d = a >= 1e5 ? 0 : a >= 100 ? Math.min(digits, 1) : digits;
  return fixMinus(nf(0, d).format(v));
}

/** Signed number with explicit +/− (for slack and deltas). */
export function fmtSigned(v: unknown, digits = 1): string {
  if (!isNum(v)) return fmtNum(v, digits);
  const a = Math.abs(v);
  const d = a >= 1e5 ? 0 : digits;
  return fixMinus(nf(0, d, true).format(v));
}

export function fmtInt(v: unknown): string {
  if (!isNum(v)) return fmtNum(v);
  return fixMinus(nf(0, 0).format(Math.round(v)));
}

/** Compact form for chart axes / chips: 1.2k, 3.4M. */
export function fmtCompact(v: unknown): string {
  if (!isNum(v)) return fmtNum(v);
  const a = Math.abs(v);
  const units: [number, string][] = [
    [1e12, "T"],
    [1e9, "G"],
    [1e6, "M"],
    [1e3, "k"],
  ];
  for (const [n, u] of units) {
    if (a >= n) return fixMinus(nf(0, a / n >= 100 ? 0 : 1).format(v / n)) + u;
  }
  return fmtNum(v, 2);
}

export function fmtPct(v: unknown, digits = 1): string {
  if (!isNum(v)) return fmtNum(v);
  return fmtSigned(v, digits) + "%";
}

/** Picoseconds shown in ps below 10 ns, ns above (always with unit). */
export function fmtPs(v: unknown, signed = false): string {
  if (!isNum(v)) return fmtNum(v);
  if (Math.abs(v) >= 1e4) return (signed ? fmtSigned(v / 1000, 2) : fmtNum(v / 1000, 2)) + " ns";
  return (signed ? fmtSigned(v, 1) : fmtNum(v, 1)) + " ps";
}

export function fmtDuration(s: unknown): string {
  if (!isNum(s)) return DASH;
  if (s < 0) return DASH;
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Epoch seconds → "2026-11-02 14:03" (local). */
export function fmtDate(epoch: unknown): string {
  if (!isNum(epoch)) return DASH;
  const d = new Date(epoch * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtAgo(epoch: unknown, now = Date.now() / 1000): string {
  if (!isNum(epoch)) return DASH;
  const s = Math.max(0, now - epoch);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 14) return `${Math.round(s / 86400)} d ago`;
  return fmtDate(epoch).slice(0, 10);
}

export function plural(n: number, one: string, many = one + "s"): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

export function shortSha(sha?: string | null): string {
  return sha ? sha.slice(0, 8) : DASH;
}

/** Middle truncation for paths / hierarchical names where the end matters. */
export function middleTruncate(s: string, max: number): string {
  if (s.length <= max) return s;
  if (max < 5) return s.slice(0, max);
  const keepEnd = Math.ceil((max - 1) * 0.6);
  const keepStart = max - 1 - keepEnd;
  return s.slice(0, keepStart) + "…" + s.slice(s.length - keepEnd);
}

/** Pretty metric names for compare tables. */
const METRIC_LABELS: Record<string, string> = {
  wns: "WNS",
  tns: "TNS",
  hold_wns: "Hold WNS",
  hold_tns: "Hold TNS",
  area_total: "Area",
  cell_count: "Cells",
  period: "Period",
  "power.total": "Power total",
  "power.dynamic": "Power dynamic",
  "power.leakage": "Power leakage",
};

export function metricLabel(m: string): string {
  if (METRIC_LABELS[m]) return METRIC_LABELS[m];
  if (m.startsWith("vt_mix.")) return `Vt ${m.slice(7)} cells`;
  return m;
}
