"""Gate-level netlist reader: Yosys JSON (``write_json``) and structural Verilog (DC ``.vg``,
Yosys ``write_verilog``), plus a minimal Liberty pin-direction reader.

The structural-Verilog reader is a small hand-written tokenizer/parser (module, port/wire
declarations, ``assign``, cell instances with named or ordered port connections) - fast enough
for 100k-cell netlists and independent of cell libraries. Hierarchical netlists are flattened
from the top with ``/`` as the instance separator (the convention timing reports use).

Pin directions come from Yosys JSON when available, else from Liberty (``[library].liberty``),
else from pin-name heuristics.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

OUTPUT_PIN_NAMES = {"Y", "Q", "QN", "QB", "Z", "ZN", "O", "ON", "CO", "SO", "OUT", "X", "SUM", "COUT", "D_OUT"}
WEAK_OUTPUT_PIN_NAMES = {"S"}   # sum output of adders, but select input of muxes
CONST_NETS = {"1'b0", "1'b1", "1'bx", "1'bz"}


@dataclass
class Cell:
    name: str
    type: str
    pins: dict[str, list[str]] = field(default_factory=dict)   # pin -> nets (MSB first for buses)
    dirs: dict[str, str] = field(default_factory=dict)         # pin -> "in" | "out" | "inout"


@dataclass
class Netlist:
    top: str
    cells: dict[str, Cell] = field(default_factory=dict)
    ports: dict[str, str] = field(default_factory=dict)        # port bit net -> direction
    driver: dict[str, tuple[str, str]] = field(default_factory=dict)       # net -> (cell, pin)
    loads: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # net -> [(cell, pin)]
    alias: dict[str, str] = field(default_factory=dict)        # net name -> canonical net
    source: str = ""

    # -------------------------------------------------------------- build helpers
    def canon(self, net: str) -> str:
        seen = 0
        while net in self.alias and seen < 64:
            nxt = self.alias[net]
            if nxt == net:
                break
            net = nxt
            seen += 1
        return net

    def index(self) -> "Netlist":
        self.driver.clear()
        self.loads.clear()
        for c in self.cells.values():
            for pin, nets in c.pins.items():
                d = c.dirs.get(pin, "in")
                for i, n in enumerate(nets):
                    n = self.canon(n)
                    nets[i] = n
                    pname = pin if len(nets) == 1 else f"{pin}[{len(nets) - 1 - i}]"
                    if d == "out":
                        self.driver[n] = (c.name, pname)
                    else:
                        self.loads.setdefault(n, []).append((c.name, pname))
        return self

    # -------------------------------------------------------------- lookups
    def pin_net(self, cell: str, pin: str) -> Optional[str]:
        c = self.cells.get(cell)
        if c is None:
            return None
        if pin in c.pins:
            nets = c.pins[pin]
            return nets[-1] if nets else None
        m = re.fullmatch(r"(.+)\[(\d+)\]", pin)
        if m and m.group(1) in c.pins:
            nets = c.pins[m.group(1)]
            i = int(m.group(2))
            if 0 <= i < len(nets):
                return nets[len(nets) - 1 - i]
        return None

    def pin_dir(self, cell: str, pin: str) -> Optional[str]:
        c = self.cells.get(cell)
        if c is None:
            return None
        base = re.sub(r"\[\d+\]$", "", pin)
        return c.dirs.get(pin) or c.dirs.get(base)

    def find_cell(self, name: str) -> Optional[str]:
        """Resolve an instance name from a timing report against this netlist (escapes, separators)."""
        lk = self.__dict__.get("_lookup")
        if lk is None:
            lk = {}
            for n in self.cells:
                for k in _name_keys(n):
                    lk.setdefault(k, n)
            self.__dict__["_lookup"] = lk
        for k in _name_keys(name):
            if k in lk:
                return lk[k]
        return None

    def find_net(self, name: str) -> Optional[str]:
        lk = self.__dict__.get("_nlookup")
        if lk is None:
            lk = {}
            for n in [*self.driver, *self.loads, *self.ports, *self.alias]:
                for k in _name_keys(n):
                    lk.setdefault(k, self.canon(n))
            self.__dict__["_nlookup"] = lk
        for k in _name_keys(name):
            if k in lk:
                return lk[k]
        return None


def _name_keys(n: str) -> list[str]:
    """Equivalent spellings of a hierarchical name: escaped / unescaped, '/' vs '.' separators."""
    s = n.strip()
    plain = s.replace("\\", "").replace(" ", "")
    keys = [s, plain]
    keys.append(plain.replace(".", "/"))
    keys.append(plain.replace("/", "."))
    keys.append(plain.replace("/", "_"))
    return list(dict.fromkeys(keys))


# ------------------------------------------------------------------ Liberty
def read_liberty_dirs(paths: Iterable[Path | str]) -> dict[str, dict[str, str]]:
    """Minimal Liberty reader: ``{cell: {pin: "in"|"out"|"inout"}}`` (bus/bundle pins included)."""
    out: dict[str, dict[str, str]] = {}
    tok = re.compile(r"/\*.*?\*/|\"(?:\\.|[^\"])*\"|[{}();:]|[^\s{}();:\"]+", re.S)
    for p in paths:
        try:
            text = Path(p).read_text(errors="replace")
        except OSError:
            continue
        text = text.replace("\\\n", " ")
        stack: list[tuple[str, str]] = []   # (group, name)
        toks = [t for t in tok.findall(text) if not t.startswith("/*")]
        i = 0
        pending_group = None
        while i < len(toks):
            t = toks[i]
            if t == "}":
                if stack:
                    stack.pop()
                i += 1
                continue
            # group:  word ( args ) {
            if i + 1 < len(toks) and toks[i + 1] == "(":
                j = i + 2
                args = []
                while j < len(toks) and toks[j] != ")":
                    if toks[j] != ",":
                        args.append(toks[j].strip('"'))
                    j += 1
                if j + 1 < len(toks) and toks[j + 1] == "{":
                    stack.append((t, args[0] if args else ""))
                    i = j + 2
                    continue
                i = j + 1
                continue
            # attribute:  word : value ;
            if i + 2 < len(toks) and toks[i + 1] == ":":
                if t == "direction" and stack and stack[-1][0] in ("pin", "bus", "bundle"):
                    cell = next((nm for g, nm in reversed(stack) if g == "cell"), None)
                    if cell:
                        v = toks[i + 2].strip('"').lower()
                        out.setdefault(cell, {})[stack[-1][1]] = {"input": "in", "output": "out"}.get(v, "inout")
                i += 3
                continue
            i += 1
    return out


def heuristic_dir(pin: str, cell_pins: Iterable[str] = ()) -> str:
    """Guess a pin direction from its name. ``S`` is an output only when the cell has no
    other recognisable output (adder sum vs. mux select)."""
    base = re.sub(r"\[\d+\]$", "", pin).upper()
    if base in OUTPUT_PIN_NAMES:
        return "out"
    if base in WEAK_OUTPUT_PIN_NAMES:
        others = {re.sub(r"\[\d+\]$", "", p).upper() for p in cell_pins} - {base}
        return "in" if others & OUTPUT_PIN_NAMES else "out"
    return "in"


# ------------------------------------------------------------------ Yosys JSON
def read_yosys_json(path: Path | str, top: Optional[str] = None) -> Netlist:
    data = json.loads(Path(path).read_text())
    mods = data.get("modules", {})
    if not mods:
        raise ValueError(f"{path}: no modules in Yosys JSON")
    if top is None or top not in mods:
        tops = [n for n, m in mods.items() if str(m.get("attributes", {}).get("top", "0")).strip("0") != ""]
        top = top if top in mods else (tops[0] if tops else next(iter(mods)))
    nl = Netlist(top=top, source=str(path))
    _yosys_module(nl, mods, top, "", {})
    return nl.index()


def _yosys_module(nl: Netlist, mods: dict, name: str, prefix: str, portmap: dict) -> None:
    m = mods[name]
    names: dict = {}
    for nn, info in m.get("netnames", {}).items():
        bits = info.get("bits", [])
        hidden = info.get("hide_name", 0)
        off = info.get("offset", 0)
        upto = info.get("upto", 0)
        for i, b in enumerate(bits):
            if isinstance(b, str):
                continue
            idx = (off + len(bits) - 1 - i) if upto else (off + i)
            label = nn if len(bits) == 1 else f"{nn}[{idx}]"
            cur = names.get(b)
            if cur is None or (cur[1] and not hidden) or (cur[1] == hidden and len(label) < len(cur[0])):
                names[b] = (label, hidden)

    def net(b) -> str:
        if isinstance(b, str):
            return {"0": "1'b0", "1": "1'b1", "x": "1'bx", "z": "1'bz"}.get(b, f"1'b{b}")
        if b in portmap:
            return portmap[b]
        return prefix + names.get(b, (f"$bit{b}", 1))[0]

    if not prefix:
        for pn, p in m.get("ports", {}).items():
            d = {"input": "in", "output": "out"}.get(p.get("direction"), "inout")
            for b in p.get("bits", []):
                n = net(b)
                nl.ports[n] = d
    for cn, c in m.get("cells", {}).items():
        ctype = c.get("type", "?")
        inst = prefix + cn
        conns = c.get("connections", {})
        if ctype in mods:   # hierarchical: flatten
            sub = mods[ctype]
            pm = {}
            for pn, bits in conns.items():
                sbits = sub.get("ports", {}).get(pn, {}).get("bits", [])
                for sb, b in zip(sbits, bits):
                    pm[sb] = net(b)
            _yosys_module(nl, mods, ctype, inst + "/", pm)
            continue
        dirs = {p: {"input": "in", "output": "out"}.get(d, "inout") for p, d in c.get("port_directions", {}).items()}
        pins = {p: [net(b) for b in reversed(bits)] for p, bits in conns.items()}
        for p in pins:
            dirs.setdefault(p, heuristic_dir(p, pins))
        nl.cells[inst] = Cell(inst, ctype, pins, dirs)


# ------------------------------------------------------------------ structural Verilog
_TOK = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<attr>\(\*.*?\*\))
  | (?P<esc>\\\S+)
  | (?P<num>\d*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_?]+|\d+)
  | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<p>[()\[\]{},;.:=#`])
  | (?P<other>.)
""", re.S | re.X)


def _tokens(text: str) -> list[str]:
    out = []
    for m in _TOK.finditer(text):
        k = m.lastgroup
        if k in ("ws", "lc", "bc", "attr"):
            continue
        t = m.group()
        if k == "esc":
            t = t[1:]        # escaped identifier: drop the backslash (terminating space already split)
        out.append(t)
    return out


@dataclass
class _VMod:
    name: str
    ports: list[str] = field(default_factory=list)
    dirs: dict[str, str] = field(default_factory=dict)
    widths: dict[str, tuple[int, int]] = field(default_factory=dict)   # name -> (msb, lsb)
    insts: list[tuple[str, str, object]] = field(default_factory=list)  # (type, name, conns)
    assigns: list[tuple[list[str], list[str]]] = field(default_factory=list)


class _VParser:
    def __init__(self, toks: list[str]):
        self.t = toks
        self.i = 0

    def peek(self, k=0) -> str:
        j = self.i + k
        return self.t[j] if j < len(self.t) else ""

    def next(self) -> str:
        t = self.peek()
        self.i += 1
        return t

    def expect(self, s: str) -> None:
        t = self.next()
        if t != s:
            raise ValueError(f"expected {s!r}, got {t!r} at token {self.i}")

    def skip_to(self, s: str) -> None:
        while self.i < len(self.t) and self.peek() != s:
            self.i += 1
        self.i += 1

    def modules(self) -> dict[str, _VMod]:
        mods = {}
        while self.i < len(self.t):
            if self.next() == "module":
                m = self.module()
                mods[m.name] = m
        return mods

    def module(self) -> _VMod:
        m = _VMod(self.next())
        if self.peek() == "#":
            self.next()
            self._skip_group()
        if self.peek() == "(":
            self.next()
            depth = 1
            pending_dir, pending_w = None, None
            while depth and self.i < len(self.t):
                t = self.next()
                if t == "(":
                    depth += 1
                elif t == ")":
                    depth -= 1
                elif t in ("input", "output", "inout"):
                    pending_dir, pending_w = {"input": "in", "output": "out"}.get(t, "inout"), None
                elif t == "[":
                    pending_w = self._range_after_bracket()
                elif t in ("wire", "reg", "logic", "signed", ","):
                    continue
                else:
                    m.ports.append(t)
                    if pending_dir:
                        m.dirs[t] = pending_dir
                        if pending_w:
                            m.widths[t] = pending_w
            if self.peek() == ";":
                self.next()
        while self.i < len(self.t):
            t = self.next()
            if t == "endmodule":
                break
            if t in ("input", "output", "inout", "wire", "reg", "tri", "supply0", "supply1", "wand", "wor", "logic"):
                self.decl(m, t)
            elif t == "assign":
                self.assign(m)
            elif t in ("parameter", "localparam", "defparam", "specify", "`timescale", "`define"):
                self.skip_to(";") if t != "specify" else self._skip_to_word("endspecify")
            elif t == ";":
                continue
            elif t and (t[0].isalpha() or t[0] in "_$") or t.startswith("\\"):
                self.instance(m, t)
        return m

    def _skip_to_word(self, w: str) -> None:
        while self.i < len(self.t) and self.next() != w:
            pass

    def _skip_group(self) -> None:
        self.expect("(")
        depth = 1
        while depth and self.i < len(self.t):
            t = self.next()
            depth += t == "("
            depth -= t == ")"

    def _range_after_bracket(self) -> Optional[tuple[int, int]]:
        a = self.next()
        b = a
        if self.peek() == ":":
            self.next()
            b = self.next()
        self.skip_to("]")
        try:
            return int(a), int(b)
        except ValueError:
            return None

    def decl(self, m: _VMod, kind: str) -> None:
        d = {"input": "in", "output": "out", "inout": "inout"}.get(kind)
        w = None
        while self.i < len(self.t):
            t = self.next()
            if t == ";":
                return
            if t == "[":
                w = self._range_after_bracket()
            elif t in ("wire", "reg", "logic", "signed", ","):
                continue
            elif t == "=":
                self.skip_to(";")
                return
            else:
                if d:
                    m.dirs[t] = d
                    if t not in m.ports:
                        m.ports.append(t)
                if w:
                    m.widths[t] = w

    def assign(self, m: _VMod) -> None:
        while True:
            lhs = self.expr()
            self.expect("=")
            rhs = self.expr()
            m.assigns.append((lhs, rhs))
            t = self.next()
            if t != ",":
                return

    def expr(self) -> list:
        """A net expression: identifier, bit/part-select, constant, or concatenation -> list of atoms."""
        t = self.next()
        if t == "{":
            out = []
            while True:
                if self.peek() == "}":
                    self.next()
                    return out
                # replication {N{...}}
                if re.fullmatch(r"\d+", self.peek()) and self.peek(1) == "{":
                    n = int(self.next())
                    self.next()
                    inner = []
                    while self.peek() != "}":
                        inner += self.expr()
                        if self.peek() == ",":
                            self.next()
                    self.next()
                    out += inner * n
                else:
                    out += self.expr()
                if self.peek() == ",":
                    self.next()
        if "'" in t:
            return [("const", t)]
        if t == "(":
            e = self.expr()
            self.expect(")")
            return e
        name = t
        if self.peek() == "[":
            self.next()
            a = self.next()
            if self.peek() == ":":
                self.next()
                b = self.next()
                self.expect("]")
                return [("slice", name, int(a), int(b))]
            self.expect("]")
            return [("bit", name, int(a))]
        return [("name", name)]

    def instance(self, m: _VMod, ctype: str) -> None:
        if self.peek() == "#":
            self.next()
            self._skip_group()
        name = self.next()
        if self.peek() == "[":   # array of instances: unsupported, skip
            self.skip_to(";")
            return
        if self.peek() != "(":
            self.skip_to(";")
            return
        self.next()
        conns: object
        if self.peek() == ".":
            named = {}
            while True:
                t = self.next()
                if t == ")":
                    break
                if t == ",":
                    continue
                if t == ".":
                    pin = self.next()
                    self.expect("(")
                    if self.peek() == ")":
                        self.next()
                        named[pin] = []
                    else:
                        named[pin] = self.expr()
                        self.expect(")")
            conns = named
        else:
            ordered = []
            while True:
                if self.peek() == ")":
                    self.next()
                    break
                ordered.append(self.expr())
                if self.peek() == ",":
                    self.next()
            conns = ordered
        if self.peek() == ";":
            self.next()
        m.insts.append((ctype, name, conns))


def read_verilog(path: Path | str, top: Optional[str] = None,
                 lib_dirs: Optional[dict[str, dict[str, str]]] = None) -> Netlist:
    text = Path(path).read_text(errors="replace")
    mods = _VParser(_tokens(text)).modules()
    if not mods:
        raise ValueError(f"{path}: no module found")
    if top is None or top not in mods:
        used = {t for m in mods.values() for t, _, _ in m.insts}
        cands = [n for n in mods if n not in used]
        top = top if top in mods else (cands[-1] if cands else list(mods)[-1])
    nl = Netlist(top=top, source=str(path))
    _vflatten(nl, mods, mods[top], "", {}, lib_dirs or {})
    return nl.index()


def _bits(m: _VMod, atom, prefix: str, pmap: dict) -> list[str]:
    def nm(n: str) -> str:
        return pmap.get(n, prefix + n)

    k = atom[0]
    if k == "const":
        v = atom[1]
        size, _, rest = v.partition("'")
        base = rest[0].lower() if rest else "d"
        digits = rest[1:].replace("_", "")
        try:
            n = int(size) if size else 32
        except ValueError:
            n = 1
        if base == "b":
            s = digits
        else:
            try:
                s = format(int(digits, {"h": 16, "o": 8, "d": 10}.get(base, 10)), "b")
            except ValueError:
                s = "x" * n
        s = s.rjust(n, "0")[-n:]
        return [f"1'b{c.lower()}" for c in s]
    if k == "name":
        n = atom[1]
        if n in m.widths:
            hi, lo = m.widths[n]
            rng = range(hi, lo - 1, -1) if hi >= lo else range(hi, lo + 1)
            return [nm(f"{n}[{i}]") for i in rng]
        return [nm(n)]
    if k == "bit":
        return [nm(f"{atom[1]}[{atom[2]}]")]
    if k == "slice":
        a, b = atom[2], atom[3]
        rng = range(a, b - 1, -1) if a >= b else range(a, b + 1)
        return [nm(f"{atom[1]}[{i}]") for i in rng]
    return []


def _expand(m: _VMod, expr: list, prefix: str, pmap: dict) -> list[str]:
    out = []
    for a in expr:
        out += _bits(m, a, prefix, pmap)
    return out


def _vflatten(nl: Netlist, mods: dict, m: _VMod, prefix: str, pmap: dict, lib: dict) -> None:
    if not prefix:
        for p in m.ports:
            for b in _bits(m, ("name", p), "", {}):
                nl.ports[b] = m.dirs.get(p, "inout")
    for lhs, rhs in m.assigns:
        lb, rb = _expand(m, lhs, prefix, pmap), _expand(m, rhs, prefix, pmap)
        for a, b in zip(reversed(lb), reversed(rb)):
            if a != b:
                # the assign target becomes an alias of the source net (feed-through / port alias)
                nl.alias[a] = b
    for ctype, iname, conns in m.insts:
        inst = prefix + iname
        if ctype in mods:
            sub = mods[ctype]
            pm = {}
            if isinstance(conns, dict):
                items = list(conns.items())
            else:
                items = list(zip(sub.ports, conns))
            for pin, ex in items:
                outer = _expand(m, ex, prefix, pmap)
                inner = _bits(sub, ("name", pin), "", {})
                for ib, ob in zip(reversed(inner), reversed(outer)):
                    pm[ib] = ob
            _vflatten(nl, mods, sub, inst + "/", pm, lib)
            continue
        pins: dict[str, list[str]] = {}
        if isinstance(conns, dict):
            for pin, ex in conns.items():
                pins[pin] = _expand(m, ex, prefix, pmap)
        else:
            for i, ex in enumerate(conns):
                pins[f"_{i}"] = _expand(m, ex, prefix, pmap)
        ldirs = lib.get(ctype, {})
        dirs = {p: ldirs.get(p) or heuristic_dir(p, pins) for p in pins}
        nl.cells[inst] = Cell(inst, ctype, pins, dirs)


# ------------------------------------------------------------------ entry point
def load(path: Path | str, *, top: Optional[str] = None, liberty: Iterable[Path | str] = ()) -> Netlist:
    """Read a gate netlist by extension: ``.json`` (Yosys) or Verilog (``.v``/``.vg``/``.sv``)."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        return read_yosys_json(p, top)
    lib = read_liberty_dirs(liberty) if liberty else {}
    return read_verilog(p, top, lib)
