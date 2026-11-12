// Draw a table as a PNG with the 2D canvas (no html-to-image dependency, no tainted canvas).
// Cells hold one or more lines; each line has its own tone so deltas keep their better/worse color.

export interface ImgLine {
  text: string;
  tone?: "fg" | "muted" | "faint" | "good" | "bad";
  bold?: boolean;
  mono?: boolean;
}
export type ImgCell = ImgLine[];

export interface TableImage {
  title?: string;
  subtitle?: string;
  header: ImgCell[];
  rows: ImgCell[][];
  /** per column; default: first left, the rest right */
  align?: ("left" | "right")[];
}

export interface ImgTheme {
  fg: string;
  muted: string;
  faint: string;
  good: string;
  bad: string;
  border: string;
  bg: string;
  font: string;
  mono: string;
}

const FS = 13;
const LINE = 18;
const PAD_X = 12;
const PAD_Y = 8;
const MARGIN = 16;
const MAX_COL = 420;

function fontOf(l: ImgLine, t: ImgTheme, size = FS): string {
  return `${l.bold ? "600 " : ""}${size}px ${l.mono ? t.mono : t.font}`;
}

/** Clip a line to `max` px with an ellipsis. */
function fit(ctx: CanvasRenderingContext2D, s: string, max: number): string {
  if (ctx.measureText(s).width <= max) return s;
  let lo = 0;
  let hi = s.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(s.slice(0, mid) + "…").width <= max) lo = mid;
    else hi = mid - 1;
  }
  return s.slice(0, lo) + "…";
}

export function tablePng(tbl: TableImage, t: ImgTheme, pixelRatio = 2): string {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("this browser cannot draw images (no canvas)");
  const ncol = tbl.header.length;
  const all = [tbl.header, ...tbl.rows];
  const widths = Array.from({ length: ncol }, (_, c) => {
    let w = 40;
    for (const row of all)
      for (const l of row[c] ?? []) {
        ctx.font = fontOf(l, t);
        w = Math.max(w, ctx.measureText(l.text).width);
      }
    return Math.min(MAX_COL, Math.ceil(w)) + 2 * PAD_X;
  });
  const heights = all.map((row) => Math.max(1, ...row.map((c) => c.length)) * LINE + 2 * PAD_Y);
  const titleH = (tbl.title ? 24 : 0) + (tbl.subtitle ? 20 : 0) + (tbl.title || tbl.subtitle ? 8 : 0);
  const W = widths.reduce((a, b) => a + b, 0) + 2 * MARGIN;
  const H = heights.reduce((a, b) => a + b, 0) + titleH + 2 * MARGIN;
  canvas.width = Math.ceil(W * pixelRatio);
  canvas.height = Math.ceil(H * pixelRatio);
  ctx.scale(pixelRatio, pixelRatio);
  ctx.fillStyle = t.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = "middle";

  let y = MARGIN;
  if (tbl.title) {
    ctx.font = `600 16px ${t.font}`;
    ctx.fillStyle = t.fg;
    ctx.fillText(fit(ctx, tbl.title, W - 2 * MARGIN), MARGIN, y + 10);
    y += 24;
  }
  if (tbl.subtitle) {
    ctx.font = `12px ${t.font}`;
    ctx.fillStyle = t.muted;
    ctx.fillText(fit(ctx, tbl.subtitle, W - 2 * MARGIN), MARGIN, y + 8);
    y += 20;
  }
  if (tbl.title || tbl.subtitle) y += 8;

  all.forEach((row, r) => {
    const h = heights[r];
    let x = MARGIN;
    for (let c = 0; c < ncol; c++) {
      const align = tbl.align?.[c] ?? (c === 0 ? "left" : "right");
      (row[c] ?? []).forEach((l, k) => {
        ctx.font = fontOf(l, t);
        ctx.fillStyle = t[l.tone ?? (r === 0 ? "muted" : "fg")];
        ctx.textAlign = align;
        const tx = align === "left" ? x + PAD_X : x + widths[c] - PAD_X;
        ctx.fillText(fit(ctx, l.text, widths[c] - 2 * PAD_X), tx, y + PAD_Y + k * LINE + LINE / 2);
      });
      x += widths[c];
    }
    y += h;
    ctx.strokeStyle = t.border;
    ctx.lineWidth = r === 0 ? 1.5 : 1;
    ctx.beginPath();
    ctx.moveTo(MARGIN, Math.round(y) + 0.5);
    ctx.lineTo(W - MARGIN, Math.round(y) + 0.5);
    ctx.stroke();
  });
  return canvas.toDataURL("image/png");
}
