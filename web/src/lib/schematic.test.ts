import { describe, expect, it } from "vitest";
import { render } from "netlistsvg";
import skin from "./schematic-skin.svg?raw";
import { cellKey, decorate, sanitize, setZoom } from "./schematic";
import type { Schematic } from "../api/types";

// every symbol in the skin, wired into one small module
const cell = (type: string, conns: Record<string, (number | string)[]>, outs: string[], attributes: Record<string, string> = {}) => ({
  type,
  port_directions: Object.fromEntries(Object.keys(conns).map((p) => [p, outs.includes(p) ? "output" : "input"])),
  connections: conns,
  attributes,
});
const NETLIST = {
  modules: {
    zoo: {
      attributes: { top: 1 },
      ports: {
        a: { direction: "input", bits: [2] },
        b: { direction: "input", bits: [3] },
        bus: { direction: "input", bits: [30, 31, 32, 33] },
        q: { direction: "output", bits: [20] },
      },
      cells: {
        u_buf: cell("$_BUF_", { A: [2], Y: [4] }, ["Y"]),
        u_not: cell("$_NOT_", { A: [3], Y: [5] }, ["Y"]),
        u_and: cell("$_AND_", { A: [4], B: [5], Y: [6] }, ["Y"], { delay: "12.5 ps" }),
        u_and3: cell("$svkit_and3", { A: [4], B: [5], C: [6], Y: [7] }, ["Y"]),
        u_and4: cell("$svkit_and4", { A: [4], B: [5], C: [6], D: [7], Y: [8] }, ["Y"]),
        u_nand: cell("$_NAND_", { A: [4], B: [5], Y: [9] }, ["Y"]),
        u_nand3: cell("$svkit_nand3", { A: [4], B: [5], C: [9], Y: [10] }, ["Y"]),
        u_nand4: cell("$svkit_nand4", { A: [4], B: [5], C: [9], D: [10], Y: [11] }, ["Y"]),
        u_or: cell("$_OR_", { A: [4], B: [5], Y: [12] }, ["Y"]),
        u_or3: cell("$svkit_or3", { A: [4], B: [5], C: [12], Y: [13] }, ["Y"]),
        u_or4: cell("$svkit_or4", { A: [4], B: [5], C: [12], D: [13], Y: [14] }, ["Y"]),
        u_nor: cell("$_NOR_", { A: [4], B: [5], Y: [15] }, ["Y"]),
        u_nor3: cell("$svkit_nor3", { A: [4], B: [5], C: [15], Y: [16] }, ["Y"]),
        u_nor4: cell("$svkit_nor4", { A: [4], B: [5], C: [15], D: [16], Y: [17] }, ["Y"]),
        u_xor: cell("$_XOR_", { A: [8], B: [11], Y: [18] }, ["Y"]),
        u_xnor: cell("$_XNOR_", { A: [14], B: [17], Y: [19] }, ["Y"]),
        u_mux: cell("$_MUX_", { A: [18], B: ["0"], S: [19], Y: [21] }, ["Y"]),
        r0: cell("$_DFF_P_", { D: [21], C: [2], Q: [22] }, ["Q"]),
        r1: cell("$_DFF_N_", { D: [22], C: [2], Q: [23] }, ["Q"]),
        r2: cell("$svkit_dffr", { D: [23], C: [2], R: [3], Q: [24] }, ["Q"]),
        l0: cell("$_DLATCH_P_", { D: [24], E: [3], Q: [20] }, ["Q"]),
        u_blk: cell("$svkit_box_140", { "din[3:0]": [30, 31, 32, 33], en: [2], dout: [25] }, ["dout"], { type_label: "my_block" }),
        u_odd: cell("AOI21_X1", { A1: [25], A2: [2], B: [3], ZN: [26] }, ["ZN"], { type_label: "AOI21_X1" }),
      },
    },
  },
};

describe("netlistsvg with the svkit skin", () => {
  it("draws every symbol, boxes and module ports", async () => {
    const svg = await render(skin, NETLIST);
    expect(svg.startsWith("<svg")).toBe(true);
    for (const k of Object.keys(NETLIST.modules.zoo.cells)) expect(svg).toContain(`id="cell_${k}"`);
    for (const p of ["a", "b", "q", "bus"]) expect(svg).toContain(`id="cell_${p}"`);
    expect(svg).toContain(">12.5 ps<"); // delay attribute shown under the gate
    expect(svg).toContain(">my_block<"); // box header = module name
    expect(svg).toContain(">AOI21_X1<"); // unknown type falls back to a box with its type
    expect(svg).toContain(">din[3:0]<"); // port labels
    expect(svg).toMatch(/class="net_\d+(,\d+)+"/); // a bus wire
  }, 30000);
});

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="https://github.com/nturley/netlistsvg" width="200" height="100" onload="alert(1)">
<style>svg{fill:red}</style><script>alert(2)</script>
<g s:type="nand" id="cell_u1/U3" transform="translate(10,10)"><text class="ref">u1/U3</text><path d="M0,0 L1,1" class="sym cell_u1/U3"/></g>
<g s:type="generic" id="cell_u_alu"><rect class="box cell_u_alu" width="10" height="10"/></g>
<g id="cell_a"><path d="M0,0" class="io-sym"/></g>
<line x1="0" x2="5" y1="0" y2="0" class="net_7"/><line x1="0" x2="5" y1="0" y2="0" class="net_8,9"/>
<circle cx="1" cy="1" r="2" class="net_7"/><foreignObject><div>x</div></foreignObject><a href="javascript:alert(3)"><text>x</text></a></svg>`;

const S: Pick<Schematic, "cells" | "path_nets"> = {
  cells: {
    "u1/U3": { kind: "cell", name: "u1/U3", type: "NAND2_X1", on_path: true, delay: 41.23 },
    u_alu: { kind: "instance", name: "u_alu", module: "alu" },
    a: { kind: "port", name: "a", dir: "in" },
  },
  path_nets: ["net_7"],
};

function mount(svg: string): SVGSVGElement {
  const host = document.createElement("div");
  host.innerHTML = sanitize(svg);
  return host.querySelector("svg") as SVGSVGElement;
}

describe("sanitize", () => {
  it("keeps the drawing and drops anything active", () => {
    const out = sanitize(SVG);
    expect(out).not.toMatch(/script|onload|alert|<style|foreignObject|javascript/i);
    expect(out).toContain('id="cell_u1/U3"');
    expect(out).toContain('class="net_8,9"');
    expect(out).toContain("<path");
  });
});

describe("decorate", () => {
  it("highlights the path, adds tooltips, click targets and bus wires", () => {
    const el = mount(SVG);
    decorate(el, S, { clickable: (c) => c.kind === "instance" });
    expect(el.getAttribute("viewBox")).toBe("0 0 200 100");
    const g = el.querySelector('g[id="cell_u1/U3"]')!;
    expect(g.classList.contains("on")).toBe(true);
    expect(g.querySelector("title")!.textContent).toBe("u1/U3\ncell NAND2_X1\ncell delay 41.2 ps (critical path)");
    const alu = el.querySelector('g[id="cell_u_alu"]')!;
    expect(alu.classList.contains("click")).toBe(true);
    expect(alu.getAttribute("tabindex")).toBe("0");
    expect(el.querySelector('g[id="cell_a"]')!.classList.contains("click")).toBe(false);
    const [l7, l89] = Array.from(el.querySelectorAll("line"));
    expect(l7.classList.contains("on")).toBe(true);
    expect(l89.classList.contains("bus")).toBe(true);
    expect(l89.classList.contains("on")).toBe(false);
    expect(cellKey(alu.querySelector("rect"))).toBe("u_alu");
    setZoom(el, 0.5);
    expect(el.getAttribute("width")).toBe("100");
  });
});
