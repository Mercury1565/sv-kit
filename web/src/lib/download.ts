// Client-side downloads: CSV text, chart images. Nothing here talks to the server.

/** One CSV field: quoted when it holds a comma, quote or line break; null/undefined/NaN become empty. */
export function csvField(v: unknown): string {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "";
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** RFC 4180 CSV (CRLF line ends) from a header row and data rows. */
export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((r) => r.map(csvField).join(",")).join("\r\n") + "\r\n";
}

/** A safe file-name fragment: letters, digits, dot, dash, underscore. */
export function fileSlug(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").slice(0, 60);
}

function clickDownload(name: string, url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function downloadText(name: string, text: string, mime = "text/plain") {
  // a BOM so spreadsheet programs read UTF-8 CSV (long hierarchical names, non-ASCII) correctly
  const blob = new Blob([mime === "text/csv" ? "﻿" : "", text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  clickDownload(name, url);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadDataUrl(name: string, dataUrl: string) {
  clickDownload(name, dataUrl);
}
