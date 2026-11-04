"""Schematic netlists for the dashboard's diagrams (drawn in the browser by netlistsvg).

Both diagrams are sent as one Yosys-JSON module (``{"modules": {name: {ports, cells}}}``), the
input format netlistsvg draws with svkit's skin (``web/src/lib/schematic-skin.svg``):

* **Blocks** (:func:`block_netlist`): one module level from the DesignGraph. Child instances,
  glue logic and the module's ports become cells/ports; nets become shared bit numbers.
* **Gates** (:func:`cone_netlist`): a fan-in cone of the synthesized netlist. Each library cell
  is mapped onto a gate symbol when its function is recognised (Liberty ``function``/``ff``/
  ``latch`` first, then the cell-name convention), else drawn as a box with its pin names.

Cell types used here are Yosys' internal gate names (``$_NAND_`` ...) plus svkit's own
(``$svkit_nand3``, ``$svkit_dffr``, ``$svkit_box_<width>``); the skin carries one template per type.
Nothing is specific to a library or project: unknown cells always fall back to a box.

Next to the netlist the response carries ``cells`` (per drawn cell key: what it is, for tooltips,
click-through and critical-path highlighting) and ``path_nets`` (netlistsvg wire classes on the path).
"""
from __future__ import annotations

import itertools
import re
from typing import Any, Iterable, Optional

from svkit.design.netlist import LibCell, Netlist

BOX_WIDTHS = (100, 140, 200, 280, 380)
CHAR_W = 6.6            # 11px monospace in the skin
CONST_BITS = {"1'b0": "0", "1'b1": "1", "1'bx": "x", "1'bz": "z"}

# gate symbols: (function, inputs) -> skin type and its input pins in order
GATES: dict[tuple[str, int], tuple[str, tuple[str, ...]]] = {
    ("buf", 1): ("$_BUF_", ("A",)),
    ("not", 1): ("$_NOT_", ("A",)),
    ("and", 2): ("$_AND_", ("A", "B")),
    ("and", 3): ("$svkit_and3", ("A", "B", "C")),
    ("and", 4): ("$svkit_and4", ("A", "B", "C", "D")),
    ("nand", 2): ("$_NAND_", ("A", "B")),
    ("nand", 3): ("$svkit_nand3", ("A", "B", "C")),
    ("nand", 4): ("$svkit_nand4", ("A", "B", "C", "D")),
    ("or", 2): ("$_OR_", ("A", "B")),
    ("or", 3): ("$svkit_or3", ("A", "B", "C")),
    ("or", 4): ("$svkit_or4", ("A", "B", "C", "D")),
    ("nor", 2): ("$_NOR_", ("A", "B")),
    ("nor", 3): ("$svkit_nor3", ("A", "B", "C")),
    ("nor", 4): ("$svkit_nor4", ("A", "B", "C", "D")),
    ("xor", 2): ("$_XOR_", ("A", "B")),
    ("xnor", 2): ("$_XNOR_", ("A", "B")),
}
# Yosys internal cells the skin draws as they are (pin names already match)
NATIVE = {
    "$_BUF_": {"A", "Y"}, "$_NOT_": {"A", "Y"},
    **{t: {"A", "B", "Y"} for t in ("$_AND_", "$_NAND_", "$_OR_", "$_NOR_", "$_XOR_", "$_XNOR_")},
    "$_MUX_": {"A", "B", "S", "Y"},
    "$_DFF_P_": {"C", "D", "Q"}, "$_DFF_N_": {"C", "D", "Q"},
    **{t: {"C", "D", "R", "Q"} for t in ("$_DFF_PP0_", "$_DFF_PN0_", "$_DFF_PP1_", "$_DFF_PN1_")},
    "$_DLATCH_P_": {"E", "D", "Q"}, "$_DLATCH_N_": {"E", "D", "Q"},
}


# ------------------------------------------------------------------ Liberty boolean functions
_FTOK = re.compile(r"\s*(?:(?P<id>[A-Za-z_][\w\[\]\.]*)|(?P<c>[01])\b|(?P<op>[!'&*|+^()]))")


def parse_function(expr: str):
    """Liberty function -> (evaluator(env) -> bool, variable names). Raises ValueError."""
    toks: list[tuple[str, str]] = []
    pos = 0
    expr = expr.strip()
    while pos < len(expr):
        m = _FTOK.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"bad function {expr!r}")
        pos = m.end()
        kind = m.lastgroup
        toks.append((kind, m.group(kind)))
    names: list[str] = []
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def p_or():
        a = p_and()
        while peek()[1] in ("|", "+"):
            take()
            b = p_and()
            a = (lambda x, y: lambda e: x(e) or y(e))(a, b)
        return a

    def p_and():
        a = p_xor()
        while True:
            k, v = peek()
            if v in ("&", "*"):
                take()
            elif not (k in ("id", "c") or v in ("(", "!")):     # adjacency is AND
                return a
            b = p_xor()
            a = (lambda x, y: lambda e: x(e) and y(e))(a, b)

    def p_xor():
        a = p_un()
        while peek()[1] == "^":
            take()
            b = p_un()
            a = (lambda x, y: lambda e: x(e) != y(e))(a, b)
        return a

    def p_un():
        if peek()[1] == "!":
            take()
            a = p_un()
            return lambda e: not a(e)
        a = p_prim()
        while peek()[1] == "'":
            take()
            a = (lambda x: lambda e: not x(e))(a)
        return a

    def p_prim():
        k, v = take() if i < len(toks) else (None, None)
        if k == "id":
            if v not in names:
                names.append(v)
            return lambda e: bool(e[v])
        if k == "c":
            c = v == "1"
            return lambda e: c
        if v == "(":
            a = p_or()
            if take()[1] != ")":
                raise ValueError("unbalanced parentheses")
            return a
        raise ValueError(f"unexpected {v!r}")

    f = p_or()
    if i != len(toks):
        raise ValueError(f"trailing tokens in {expr!r}")
    return f, names


def _table(f, names: list[str]) -> tuple[bool, ...]:
    return tuple(f(dict(zip(names, bits))) for bits in itertools.product((False, True), repeat=len(names)))


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def classify_function(expr: str, inputs: list[str]) -> Optional[tuple[str, list[str]]]:
    """Recognise a combinational output function: ("nand", [pins in order]) / ("mux", [a, b, s])."""
    try:
        f, names = parse_function(expr)
    except (ValueError, IndexError):
        return None
    if not names or any(n not in inputs for n in names):
        return None
    names = sorted(names, key=_natural)
    t = _table(f, names)
    n = len(names)
    rows = list(itertools.product((False, True), repeat=n))
    shapes = {
        "and": tuple(all(r) for r in rows), "or": tuple(any(r) for r in rows),
        "xor": tuple(sum(r) % 2 == 1 for r in rows),
    }
    for k, v in list(shapes.items()):
        shapes["n" + k if k != "xor" else "xnor"] = tuple(not x for x in v)
    if n == 1:
        return {(False, True): ("buf", names), (True, False): ("not", names)}.get(t)
    for k in ("and", "nand", "or", "nor", "xor", "xnor"):
        if t == shapes[k]:
            return k, names
    if n == 3:     # 2:1 mux: some s with f = s ? hi : lo
        envs = [dict(zip(names, r)) for r in rows]
        for sel in names:
            a, b = [x for x in names if x != sel]
            for lo, hi in ((a, b), (b, a)):
                if all(f(e) == (e[hi] if e[sel] else e[lo]) for e in envs):
                    return "mux", [lo, hi, sel]
    return None


def _pin(expr: Optional[str], inputs: list[str]) -> Optional[tuple[str, bool]]:
    """A single (optionally inverted) pin, as Liberty writes ``clocked_on : "!CLK"``."""
    if not expr:
        return None
    e = expr.strip().strip("()").strip()
    inv = False
    if e.startswith("!"):
        inv, e = True, e[1:].strip()
    elif e.endswith("'"):
        inv, e = True, e[:-1].strip()
    return (e, inv) if e in inputs else None


# ------------------------------------------------------------------ cell -> symbol
_NAME_FUNCS = [("xnor", "xnor"), ("xor", "xor"), ("nand", "nand"), ("nor", "nor"), ("and", "and"),
               ("or", "or"), ("inv", "not"), ("not", "not"), ("buf", "buf"), ("mux", "mux"),
               ("sdff", None), ("dff", "dff"), ("dlat", "latch"), ("latch", "latch")]
_CLOCK = re.compile(r"(?i)^(clk|ck|cp|c|clock|clk_?i)$")
_DATA = re.compile(r"(?i)^(d|din|data)$")
_SEL = re.compile(r"(?i)^(s|s0|sel|sel0)$")
_RESET = re.compile(r"(?i)^(r|rn|rb|rst|rstn|rstb|reset|resetn|clr|clrn|clrb|cdn|rd|rdn)$")
_Q = re.compile(r"(?i)^q$")


def symbol(ctype: str, dirs: dict[str, str], lib: Optional[LibCell] = None) -> Optional[tuple[str, dict[str, str]]]:
    """(skin type, {cell pin -> symbol pin}) for a gate-level cell, or None to draw it as a box."""
    ins = sorted((p for p, d in dirs.items() if d != "out"), key=_natural)
    outs = [p for p, d in dirs.items() if d == "out"]
    if ctype in NATIVE and set(dirs) == NATIVE[ctype]:
        return ctype, {p: p for p in dirs}
    if lib is not None:
        r = _from_liberty(lib, ins, outs)
        if r is not None or lib.functions or lib.ff or lib.latch:
            return r
    return _from_name(ctype, ins, outs)


def _gate(fn: str, order: list[str], out: str) -> Optional[tuple[str, dict[str, str]]]:
    if fn == "mux":
        return "$_MUX_", {order[0]: "A", order[1]: "B", order[2]: "S", out: "Y"}
    g = GATES.get((fn, len(order)))
    if g is None:
        return None
    return g[0], {**dict(zip(order, g[1])), out: "Y"}


def _from_liberty(lib: LibCell, ins: list[str], outs: list[str]) -> Optional[tuple[str, dict[str, str]]]:
    seq = lib.ff or lib.latch
    if seq is not None:
        if len(outs) != 1:
            return None
        q = outs[0]
        state = (seq.get("vars") or "").split(",")[0].strip()
        if lib.functions.get(q, "").strip() != state:
            return None                      # only QN (inverted) or derived outputs: box
        if lib.ff is not None:
            d, c = _pin(seq.get("next_state"), ins), _pin(seq.get("clocked_on"), ins)
            if not d or not c or d[1]:
                return None
            rst = [_pin(seq.get(k), ins) for k in ("clear", "preset") if seq.get(k)]
            if len(rst) > 1 or (rst and rst[0] is None):
                return None
            used = {d[0], c[0], *(r[0] for r in rst if r)}
            if used != set(ins):
                return None                  # scan / enable pins: box
            if rst:
                return "$svkit_dffr", {d[0]: "D", c[0]: "C", rst[0][0]: "R", q: "Q"}
            return ("$_DFF_N_" if c[1] else "$_DFF_P_"), {d[0]: "D", c[0]: "C", q: "Q"}
        d, e = _pin(seq.get("data_in"), ins), _pin(seq.get("enable"), ins)
        if not d or not e or d[1] or {d[0], e[0]} != set(ins):
            return None
        return ("$_DLATCH_N_" if e[1] else "$_DLATCH_P_"), {d[0]: "D", e[0]: "E", q: "Q"}
    if len(outs) != 1 or outs[0] not in lib.functions:
        return None
    r = classify_function(lib.functions[outs[0]], ins)
    if r is None or set(r[1]) != set(ins):
        return None
    return _gate(r[0], r[1], outs[0])


def _from_name(ctype: str, ins: list[str], outs: list[str]) -> Optional[tuple[str, dict[str, str]]]:
    base = ctype.lower().split("__")[-1].lstrip("$_")
    fn = next((f for pre, f in _NAME_FUNCS if base.startswith(pre)), None)
    if fn is None or len(outs) != 1:
        return None
    out = outs[0]
    if fn == "mux":
        sel = [p for p in ins if _SEL.match(p)]
        if len(ins) != 3 or len(sel) != 1:
            return None
        a, b = [p for p in ins if p != sel[0]]
        return _gate("mux", [a, b, sel[0]], out)
    if fn in ("dff", "latch"):
        if not _Q.match(out):
            return None
        d = [p for p in ins if _DATA.match(p)]
        c = [p for p in ins if _CLOCK.match(p) or (fn == "latch" and re.match(r"(?i)^(g|e|en|enable)$", p))]
        r = [p for p in ins if _RESET.match(p)]
        if len(d) != 1 or len(c) != 1 or len(ins) != 2 + len(r) or len(r) > 1:
            return None
        if fn == "latch":
            return (None if r else ("$_DLATCH_P_", {d[0]: "D", c[0]: "E", out: "Q"}))
        if r:
            return "$svkit_dffr", {d[0]: "D", c[0]: "C", r[0]: "R", out: "Q"}
        return "$_DFF_P_", {d[0]: "D", c[0]: "C", out: "Q"}
    return _gate(fn, ins, out)


def box_type(in_labels: Iterable[str], out_labels: Iterable[str], header: str) -> str:
    li = max((len(s) for s in in_labels), default=0)
    lo = max((len(s) for s in out_labels), default=0)
    need = max((li + lo) * CHAR_W + 30, len(header) * CHAR_W + 16)
    w = next((b for b in BOX_WIDTHS if b >= need), BOX_WIDTHS[-1])
    return f"$svkit_box_{w}"


# ------------------------------------------------------------------ module builder
class _Module:
    def __init__(self) -> None:
        self.ports: dict[str, dict] = {}
        self.cells: dict[str, dict] = {}
        self._bits = itertools.count(2)            # 0/1 are reserved for constants in Yosys JSON
        self.net_bits: dict[str, list] = {}

    def bits(self, net: str, width: int = 1) -> list:
        if net in CONST_BITS:
            return [CONST_BITS[net]]
        if net not in self.net_bits:
            self.net_bits[net] = [next(self._bits) for _ in range(max(1, width))]
        return self.net_bits[net]

    def fresh(self, width: int = 1) -> list:
        return [next(self._bits) for _ in range(max(1, width))]

    def key(self, want: str) -> str:
        k, n = want, 2
        while k in self.cells or k in self.ports:
            k, n = f"{want} ({n})", n + 1
        return k

    def json(self, name: str) -> dict:
        return {"modules": {name: {"attributes": {"top": 1}, "ports": self.ports, "cells": self.cells}}}


def _fit(bits: list, width: int, m: _Module) -> list:
    return bits[:width] if len(bits) >= width else bits + m.fresh(width - len(bits))


def _plabel(name: str, width: Optional[int]) -> str:
    return f"{name}[{width - 1}:0]" if width and width > 1 else name


def block_netlist(block: dict[str, Any]) -> dict[str, Any]:
    """DesignGraph.block() -> schematic (Yosys JSON + cell info)."""
    m = _Module()
    width = {e["net"]: (e.get("width") or 1) for e in block["edges"]}
    attached: dict[tuple[str, Optional[str]], list[str]] = {}
    for e in block["edges"]:
        for node, port in ((e["src"], e.get("src_port")), (e["dst"], e.get("dst_port"))):
            lst = attached.setdefault((node, port), [])
            if e["net"] not in lst:
                lst.append(e["net"])

    def port_bits(node: str, port: Optional[str], w: int) -> list:
        nets = attached.get((node, port), [])
        bits = [b for n in nets for b in m.bits(n, width.get(n, 1))]
        return _fit(bits, w, m) if bits else m.fresh(w)

    info: dict[str, dict] = {}
    for n in block["nodes"]:
        if n.get("kind") == "port":
            p = n["ports"][0]
            w = p.get("width") or 1
            k = m.key(n["label"])
            # inout ports are drawn as inputs (netlistsvg knows two directions)
            m.ports[k] = {"direction": "output" if p.get("dir") == "out" else "input",
                          "bits": port_bits(n["id"], p["name"], w)}
            info[k] = {"kind": "port", "name": n["label"], "dir": p.get("dir"), "width": w}
            continue
        if n.get("kind") == "logic":
            ins = sorted({net for (node, _), nets in attached.items() if node == n["id"]
                          for net in nets if any(e["dst"] == n["id"] and e["net"] == net for e in block["edges"])})
            outs = sorted({e["net"] for e in block["edges"] if e["src"] == n["id"]})
            conns, dirs = {}, {}
            for net in ins:
                lab = _plabel(net, width.get(net))
                conns[lab], dirs[lab] = m.bits(net, width.get(net, 1)), "input"
            for net in outs:
                lab = _plabel(net, width.get(net)) + ("" if net not in ins else " ")
                conns[lab], dirs[lab] = m.bits(net, width.get(net, 1)), "output"
            k = m.key(n["label"])
            hdr = "assign / always"
            m.cells[k] = {"type": box_type([l for l, d in dirs.items() if d == "input"],
                                           [l for l, d in dirs.items() if d == "output"], hdr),
                          "port_directions": dirs, "connections": conns, "attributes": {"type_label": hdr}}
            info[k] = {"kind": "logic", "name": n["label"]}
            continue
        conns, dirs = {}, {}
        for p in n.get("ports") or []:
            w = p.get("width") or 1
            lab = _plabel(p["name"], p.get("width"))
            conns[lab] = port_bits(n["id"], p["name"], w)
            dirs[lab] = "output" if p.get("dir") == "out" else "input"
        k = m.key(n["id"])
        mod = n.get("module") or ""
        m.cells[k] = {"type": box_type([l for l, d in dirs.items() if d == "input"],
                                       [l for l, d in dirs.items() if d == "output"], mod),
                      "port_directions": dirs, "connections": conns, "attributes": {"type_label": mod}}
        info[k] = {"kind": "instance", "name": n["id"], "module": mod}
    return {"netlist": m.json(block.get("module") or "top"), "cells": info, "path_nets": [],
            "module": block.get("module"), "instance": block.get("instance"),
            "counts": {"instances": sum(1 for v in info.values() if v["kind"] == "instance"),
                       "ports": sum(1 for v in info.values() if v["kind"] == "port"),
                       "nets": len({e["net"] for e in block["edges"]})}}


def cone_netlist(cone: dict[str, Any], nl: Netlist) -> dict[str, Any]:
    """analysis.cone.cone() -> schematic (Yosys JSON + cell info + nets on the critical path)."""
    m = _Module()
    drawn = {e["net"] for e in cone["edges"]} | {cone["root_net"]}
    info: dict[str, dict] = {}
    keys: dict[str, str] = {}
    for n in cone["nodes"]:
        if n["kind"] != "port":
            continue
        net = n["id"][len("port:"):]
        k = m.key(net)
        keys[n["id"]] = k
        m.ports[k] = {"direction": "input", "bits": m.bits(net)}
        info[k] = {"kind": "port", "name": net, "type": n.get("cell"), "on_path": bool(n.get("on_path"))}
    for n in cone["nodes"]:
        if n["kind"] != "cell":
            continue
        cc = nl.cells[n["id"]]
        dirs = {p: v["dir"] for p, v in n["pins"].items()}
        sym = symbol(cc.type, dirs, nl.lib.get(cc.type))
        if sym and (set(sym[1]) != set(dirs) or len(set(sym[1].values())) != len(dirs)):
            sym = None                             # pins do not map one-to-one: draw a box
        conns, pdirs = {}, {}
        for p, v in n["pins"].items():
            nets = list(reversed(v["nets"]))         # Yosys JSON lists bits LSB first
            name = sym[1][p] if sym else p
            conns[name] = [m.bits(x)[0] if (x in drawn or x in CONST_BITS) else m.fresh()[0] for x in nets] or m.fresh()
            pdirs[name] = "output" if v["dir"] == "out" else "input"
        attrs: dict[str, str] = {"type_label": cc.type}
        if n.get("delay") is not None:
            attrs["delay"] = f"{n['delay']:.1f} ps"
        ctype = sym[0] if sym else box_type([p for p, d in pdirs.items() if d == "input"],
                                            [p for p, d in pdirs.items() if d == "output"], cc.type)
        k = m.key(n["id"])
        keys[n["id"]] = k
        m.cells[k] = {"type": ctype, "port_directions": pdirs, "connections": conns, "attributes": attrs}
        info[k] = {"kind": "cell", "name": n["id"], "type": cc.type, "symbol": bool(sym),
                   "on_path": bool(n.get("on_path")), "delay": n.get("delay"),
                   "pins": {p: v["dir"] for p, v in n["pins"].items()}}
    root = cone["root_net"]
    if root in m.net_bits:
        k = m.key(root if root not in m.ports else f"{root} (out)")
        m.ports[k] = {"direction": "output", "bits": m.bits(root)}
        info[k] = {"kind": "port", "name": root, "type": "cone output", "on_path": False}
    path_nets = sorted({"net_" + ",".join(str(b) for b in m.bits(e["net"]))
                        for e in cone["edges"] if e.get("on_path") and e["net"] not in CONST_BITS})
    return {"netlist": m.json(nl.top or "cone"), "cells": info, "path_nets": path_nets,
            "root_net": root, "path_cells": [keys.get(c, c) for c in cone.get("path_cells", [])],
            "unresolved": cone.get("unresolved", []), "slack": cone.get("slack"),
            "counts": {"cells": sum(1 for v in info.values() if v["kind"] == "cell"),
                       "symbols": sum(1 for v in info.values() if v.get("symbol")),
                       "on_path": sum(1 for v in info.values() if v.get("on_path") and v["kind"] == "cell")}}
