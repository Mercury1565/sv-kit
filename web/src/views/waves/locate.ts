// Signal → source line, using only documented endpoints (/api/design/hierarchy + /api/source).
// See docs/proposals/web.md: a dedicated `/api/design/signal?path=` would make this exact.
import { api } from "../../api/client";
import type { DesignHierarchy } from "../../api/types";

/** Split a dotted hierarchical path, keeping generate indices (gen[3].u_x) intact. */
export function splitPath(p: string): string[] {
  const out: string[] = [];
  let cur = "";
  let br = 0;
  for (const ch of p) {
    if (ch === "[") br++;
    if (ch === "]") br = Math.max(0, br - 1);
    if (ch === "." && br === 0) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

/**
 * Resolve the module that declares `path`'s leaf. The wave hierarchy has testbench scopes
 * above the design top, so try every scope as "the top instance" and keep the start that
 * matches the most instance names; generate blocks stay in the current module.
 */
export function moduleForSignal(h: DesignHierarchy, path: string): string | null {
  const scopes = splitPath(path).slice(0, -1);
  if (!h.modules[h.top]) return null;
  let best: { mod: string; score: number } | null = null;
  for (let s = 0; s < scopes.length; s++) {
    let mod = h.top;
    let score = 0;
    for (let i = s + 1; i < scopes.length; i++) {
      const inst = h.modules[mod]?.instances.find((x) => x.name === scopes[i]);
      if (inst && h.modules[inst.module]) {
        mod = inst.module;
        score++;
      }
    }
    if (!best || score > best.score) best = { mod, score };
  }
  return best?.mod ?? h.top;
}

const DECL = /\b(input|output|inout|logic|wire|reg|bit|int|integer|byte|shortint|longint|var|tri|wand|wor)\b/;

export async function locateSignal(path: string): Promise<{ file: string; line: number }> {
  const h = await api.hierarchy();
  const mod = moduleForSignal(h, path);
  const m = mod ? h.modules[mod] : null;
  if (!m?.file) throw new Error(`no source file known for ${path}`);
  const leaf = splitPath(path).pop()!.replace(/\[.*$/, "");
  const start = m.line ?? 1;
  const src = await api.source(m.file, start + 2000, 2000);
  const re = new RegExp(`\\b${leaf.replace(/[$\\]/g, "\\$&")}\\b`);
  let found = -1;
  src.lines.forEach((l, i) => {
    const n = src.start + i;
    if (found < 0 && n >= start && re.test(l) && DECL.test(l)) found = n;
  });
  if (found < 0)
    src.lines.forEach((l, i) => {
      const n = src.start + i;
      if (found < 0 && n >= start && re.test(l)) found = n;
    });
  return { file: m.file, line: found > 0 ? found : start };
}
