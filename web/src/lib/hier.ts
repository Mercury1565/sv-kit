// Area-by-hierarchy tree: flat {path: area} → sortable, collapsible indented rows.

export interface HierRow {
  path: string; // full key ('' = top)
  name: string; // last component
  depth: number;
  area: number | null;
  pct: number | null; // of total
  hasChildren: boolean;
}

export function splitHier(p: string): string[] {
  if (!p) return [];
  // DC uses '/', some tools '.'; never split inside [...] (generate indices)
  const out: string[] = [];
  let cur = "";
  let br = 0;
  for (const ch of p) {
    if (ch === "[") br++;
    if (ch === "]") br = Math.max(0, br - 1);
    if ((ch === "/" || (ch === "." && !p.includes("/"))) && br === 0) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

interface Node {
  path: string;
  name: string;
  area: number | null;
  children: Map<string, Node>;
}

export function buildHierRows(
  areas: Record<string, number>,
  opts: { expanded: Set<string>; sort: "area" | "name"; dir: 1 | -1; total?: number | null },
): HierRow[] {
  const root: Node = { path: "", name: "(top)", area: areas[""] ?? null, children: new Map() };
  const sep = Object.keys(areas).some((k) => k.includes("/")) ? "/" : ".";
  for (const [k, a] of Object.entries(areas)) {
    if (!k) continue;
    const parts = splitHier(k);
    let n = root;
    let acc = "";
    for (let i = 0; i < parts.length; i++) {
      acc = acc ? acc + sep + parts[i] : parts[i];
      let c = n.children.get(parts[i]);
      if (!c) {
        c = { path: acc, name: parts[i], area: null, children: new Map() };
        n.children.set(parts[i], c);
      }
      if (i === parts.length - 1) c.area = a;
      n = c;
    }
  }
  const total = opts.total ?? root.area ?? null;
  const out: HierRow[] = [];
  const walk = (n: Node, depth: number) => {
    out.push({
      path: n.path,
      name: n.name,
      depth,
      area: n.area,
      pct: n.area !== null && total ? (n.area / total) * 100 : null,
      hasChildren: n.children.size > 0,
    });
    if (!opts.expanded.has(n.path)) return;
    const kids = Array.from(n.children.values()).sort((a, b) =>
      opts.sort === "name" ? a.name.localeCompare(b.name, undefined, { numeric: true }) * opts.dir : ((a.area ?? -Infinity) - (b.area ?? -Infinity)) * -opts.dir,
    );
    for (const c of kids) walk(c, depth + 1);
  };
  walk(root, 0);
  return out;
}
