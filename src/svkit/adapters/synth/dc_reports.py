"""Design Compiler report parsers -> normalized SynthResult / TimingPath.

Tolerant, version-robust text parsers for the reports DC prints:

  report_timing, report_qor, report_area [-hierarchy], report_constraints -all_violators,
  report_power, report_reference [-hierarchy], report_resources, report_design, report_units

plus DC logs (Error/Warning messages, removed registers) and combined `.rep` files that
concatenate several reports (split on the `****` / `Report : <kind>` banners).

Units. DC prints times in the *library* time unit, without a unit suffix. The unit is
taken, in order, from: an explicit hint in the text (`report_units` "Time_unit",
`report_power` "Time Units = 1ns", report_lib "Time Unit"), inference from a known clock
period in ps (ratio of the knob period to the report's clock period), a caller-supplied
default, a magnitude heuristic (clock periods >= 100 are ps), and finally ns. Everything
returned is converted to ps (time) and mW (power).

Nothing here is project- or library-specific: cell names, libraries and hierarchy come
only from the report text.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from svkit.schemas import Msg, Power, Severity, SynthResult, TimingPath, TimingPoint

TIME_TO_PS = {"fs": 1e-3, "ps": 1.0, "ns": 1e3, "us": 1e6, "ms": 1e9, "s": 1e12}
POWER_TO_MW = {"pw": 1e-9, "nw": 1e-6, "uw": 1e-3, "mw": 1.0, "w": 1e3, "kw": 1e6}

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(rf"^{_NUM}$")
_NUM_PREFIX = re.compile(rf"^({_NUM})([*&H^$@#~%!]*)$")
_DASHES = re.compile(r"^\s*-{4,}[-\s]*$")
_STARS = re.compile(r"^\s*\*{8,}\s*$")


def sig(x: Optional[float], digits: int = 10) -> Optional[float]:
    """Round to `digits` significant digits (stable floats for golden files)."""
    if x is None:
        return None
    if x == 0:
        return 0.0          # no negative zero in golden files
    if not math.isfinite(x):
        return float(x)
    return float(f"{x:.{digits}g}")


def _num(tok: str) -> Optional[float]:
    m = _NUM_PREFIX.match(tok.strip())
    return float(m.group(1)) if m else None


def power_to_mw(value: float, unit: Optional[str], default_unit: str = "mw") -> float:
    u = (unit or default_unit).lower().replace("µ", "u")
    m = re.match(r"^([\d.eE+-]*)\s*([a-z]+)$", u)
    mult = 1.0
    if m:
        if m.group(1):
            mult = float(m.group(1))
        u = m.group(2)
    if u not in POWER_TO_MW:
        raise ValueError(f"unknown power unit {unit!r}")
    return value * mult * POWER_TO_MW[u]


def time_unit_to_ps(unit: str) -> float:
    """'ns' -> 1000, '1ps' -> 1, '10ps' -> 10, '1e-09 Second' -> 1000."""
    u = unit.strip().lower()
    m = re.match(rf"^({_NUM})?\s*([a-z]+)$", u)
    if not m:
        raise ValueError(f"bad time unit {unit!r}")
    mult = float(m.group(1)) if m.group(1) else 1.0
    name = m.group(2)
    if name in ("second", "seconds", "sec"):
        name = "s"
    if name not in TIME_TO_PS:
        raise ValueError(f"bad time unit {unit!r}")
    return mult * TIME_TO_PS[name]


# =========================================================== report splitting
@dataclass
class Section:
    kind: str                      # "timing", "area", "qor", "constraint", "power", ...
    text: str                      # full text including the banner
    design: Optional[str] = None
    options: list[str] = field(default_factory=list)   # e.g. ["-delay max", "-max_paths 2"]


_REPORT_LINE = re.compile(r"^\s*Report\s*:\s*(\S+)(.*)$")


def split_reports(text: str) -> list[Section]:
    """Split a (possibly combined) report file at `****` + `Report : <kind>` banners."""
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = _REPORT_LINE.match(ln)
        if m and i > 0 and _STARS.match(lines[i - 1]):
            starts.append((i - 1, m.group(1).lower()))
    out: list[Section] = []
    for k, (s, kind) in enumerate(starts):
        e = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        body = lines[s:e]
        design, opts = None, []
        m0 = _REPORT_LINE.match(body[1])
        if m0 and m0.group(2).strip():
            opts.append(m0.group(2).strip())
        for ln in body[2:]:
            if _STARS.match(ln):
                break
            md = re.match(r"^\s*Design\s*:\s*(\S+)", ln)
            if md:
                design = design or md.group(1)
            elif re.match(r"^\s+-\w", ln):
                opts.append(ln.strip())
        out.append(Section(kind=_canon_kind(kind), text="\n".join(body) + "\n", design=design, options=opts))
    return out


def _canon_kind(kind: str) -> str:
    k = kind.strip().lower().rstrip(":")
    aliases = {"constraints": "constraint", "references": "reference", "resource": "resources",
               "timing_path": "timing", "qor": "qor", "unit": "units"}
    return aliases.get(k, k)


def banner_design(text: str) -> Optional[str]:
    m = re.search(r"(?m)^\s*Design\s*:\s*(\S+)", text)
    return m.group(1) if m else None


# =========================================================== time unit detection
_UNIT_HINTS = [
    # report_units:  Time_unit : 1e-09 Second
    re.compile(rf"(?im)^\s*Time_unit\s*:\s*({_NUM})\s*(second|sec|s)\b"),
    # report_power:  Time Units = 1ns    report_lib:  Time Unit : 1ps
    re.compile(r"(?im)\bTime[ _]Units?\s*[=:]\s*([\d.]*)\s*(fs|ps|ns|us)\b"),
]


def detect_time_unit(text: str) -> Optional[float]:
    """ps per report time unit if the text states it, else None."""
    for rx in _UNIT_HINTS:
        m = rx.search(text)
        if m:
            v = float(m.group(1)) if m.group(1) else 1.0
            u = m.group(2).lower()
            u = "s" if u.startswith("s") else u
            return sig(v * TIME_TO_PS[u])
    return None


def infer_time_unit(raw_period: Optional[float], period_ps: Optional[float]) -> Optional[float]:
    """Choose ps-per-unit in {1, 1000} from a known period in ps and the report's raw period."""
    if not raw_period or not period_ps or raw_period <= 0 or period_ps <= 0:
        return None
    ratio = period_ps / raw_period
    best = min((1.0, 1000.0), key=lambda u: abs(math.log(ratio / u)))
    return best if abs(math.log(ratio / best)) < math.log(3) else None


# =========================================================== timing
@dataclass
class RawPath:
    """A timing path in report units (converted to TimingPath with a scale)."""
    startpoint: str
    endpoint: str
    path_group: Optional[str]
    path_type: str
    slack: float
    required: Optional[float]
    arrival: Optional[float]
    launch_edge: Optional[float]
    capture_edge: Optional[float]
    capture_clock: Optional[str]
    points: list[dict]

    def to_path(self, scale: float) -> TimingPath:
        return TimingPath(
            startpoint=self.startpoint, endpoint=self.endpoint, path_group=self.path_group,
            path_type="min" if self.path_type == "min" else "max",
            slack=sig(self.slack * scale), required=sig(self.required * scale) if self.required is not None else None,
            arrival=sig(self.arrival * scale) if self.arrival is not None else None,
            points=[TimingPoint(inst=p["inst"], pin=p["pin"], cell=p["cell"], incr=sig(p["incr"] * scale),
                                arrival=sig(p["arrival"] * scale), rise_fall=p["rf"], net=p["net"])
                    for p in self.points])


_PIN_ROW = re.compile(r"^\s*(\S+)\s+\(([^()]*)\)\s*(.*)$")
_CLOCK_EDGE = re.compile(rf"^\s*clock\s+(\S+)\s+\((rise|fall) edge\)\s+({_NUM})\s+({_NUM})")
_SLACK = re.compile(rf"^\s*slack\s*\(([^)]*)\)\s*({_NUM})?")


def _join_wrapped(lines: list[str]) -> list[str]:
    """DC wraps long names: the name alone on one line, the rest on the next (indented)."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        toks = ln.split()
        name_only = len(toks) == 1 or (len(toks) == 2 and toks[1].startswith("(") and toks[1].endswith(")"))
        if (name_only and i + 1 < len(lines) and lines[i + 1][:1].isspace()
                and lines[i + 1].strip() and not _DASHES.match(lines[i + 1])
                and not _NUM_RE.match(toks[0])):
            nxt = lines[i + 1].strip()
            if nxt.startswith("(") or _NUM_RE.match(nxt.split()[0]):
                out.append(ln.rstrip() + " " + nxt)
                i += 2
                continue
        out.append(ln)
        i += 1
    return out


def _row_numbers(rest: str) -> tuple[list[float], Optional[str]]:
    nums: list[float] = []
    rf: Optional[str] = None
    toks = rest.split()
    if toks and toks[-1] in ("r", "f", "R", "F"):
        rf = toks.pop().lower()
    for t in toks:
        v = _num(t)
        if v is not None:
            nums.append(v)
    return nums, rf


def _split_pin(name: str) -> tuple[str, str]:
    if "/" in name:
        inst, _, pin = name.rpartition("/")
        return inst, pin
    return "", name


def _parse_path_block(block: str) -> Optional[RawPath]:
    lines = block.splitlines()
    sp = re.match(r"^\s*Startpoint:\s*(\S+)", lines[0])
    if not sp:
        return None
    start = sp.group(1)
    end = group = None
    ptype = None
    for ln in lines:
        if end is None and (m := re.match(r"^\s*Endpoint:\s*(\S+)", ln)):
            end = m.group(1)
        elif group is None and (m := re.match(r"^\s*Path Group:\s*(\S+)", ln)):
            group = m.group(1)
        elif ptype is None and (m := re.match(r"^\s*Path Type:\s*(\w+)", ln)):
            ptype = m.group(1).lower()
    if end is None:
        return None
    # locate the point table
    hdr = next((i for i, ln in enumerate(lines) if re.match(r"^\s*Point\b", ln) and "Incr" in ln and "Path" in ln), None)
    if hdr is None:
        return None
    body = _join_wrapped(lines[hdr + 1:])
    points: list[dict] = []
    launch = capture = None
    capture_clock = None
    arrival = required = slack = None
    stage = "data"          # data -> required -> done
    saw_hold = False
    for ln in body:
        s = ln.strip()
        if not s or _DASHES.match(ln):
            continue
        if s.startswith("data arrival time"):
            if arrival is None:
                arrival = _last_num(s)
            stage = "required"
            continue
        if s.startswith("data required time"):
            if required is None:
                required = _last_num(s)
            continue
        if "hold time" in s or "removal time" in s:
            saw_hold = True
        if (m := _SLACK.match(ln)):
            v = m.group(2)
            slack = float(v) if v is not None else _last_num(s)
            break
        if (m := _CLOCK_EDGE.match(ln)):
            edge_t = float(m.group(4))
            if stage == "data" and launch is None:
                launch = edge_t
            elif stage == "required" and capture is None:
                capture, capture_clock = edge_t, m.group(1)
            continue
        if stage != "data":
            continue
        if (m := _PIN_ROW.match(ln)):
            name, cell, rest = m.group(1), m.group(2).strip(), m.group(3)
            nums, rf = _row_numbers(rest)
            if cell == "net":
                if points:
                    points[-1]["net"] = name
                continue
            if not nums:
                continue
            inc = nums[-2] if len(nums) >= 2 else 0.0
            arr = nums[-1]
            if cell in ("in", "out", "inout"):
                inst, pin, cellname = "", name, None
            else:
                inst, pin = _split_pin(name)
                cellname = cell or None
            points.append({"inst": inst, "pin": pin, "cell": cellname, "incr": inc, "arrival": arr,
                           "rf": rf, "net": None})
    if slack is None:
        return None   # unconstrained path
    if ptype not in ("max", "min"):
        ptype = "min" if saw_hold else "max"
    return RawPath(start, end, group, ptype, slack, required, arrival, launch, capture, capture_clock, points)


def _last_num(s: str) -> Optional[float]:
    for t in reversed(s.split()):
        v = _num(t)
        if v is not None:
            return v
    return None


def parse_timing_raw(text: str) -> list[RawPath]:
    """All paths of a report_timing output, in report units."""
    parts = re.split(r"(?m)^(?=\s*Startpoint:)", text)
    out = []
    for p in parts:
        if re.match(r"^\s*Startpoint:", p):
            rp = _parse_path_block(p)
            if rp:
                out.append(rp)
    return out


def parse_timing(text: str, scale: float = 1000.0) -> list[TimingPath]:
    """report_timing -> TimingPaths in ps (`scale` = ps per report time unit)."""
    return [rp.to_path(scale) for rp in parse_timing_raw(text)]


def raw_clock_period(paths: Iterable[RawPath]) -> Optional[float]:
    """Clock period in report units from the first setup path with launch/capture edges."""
    for p in paths:
        if p.path_type == "max" and p.capture_edge is not None:
            per = p.capture_edge - (p.launch_edge or 0.0)
            if per > 0:
                return per
    return None


def timing_header(text: str) -> dict[str, str]:
    out = {}
    if (m := re.search(r"(?m)^\s*Operating Conditions:\s*(\S+)(?:\s+Library:\s*(\S+))?", text)):
        out["operating_condition"] = m.group(1)
        if m.group(2):
            out["library"] = m.group(2)
    if (m := re.search(r"(?m)^\s*Wire Load Model Mode:\s*(\S+)", text)):
        out["wire_load_mode"] = m.group(1).rstrip(".")
    return out


# =========================================================== qor
def parse_qor(text: str) -> dict[str, Any]:
    """report_qor -> {"groups": {group: {key: value}}, "sections": {title: {key: value}}}."""
    groups: dict[str, dict[str, float]] = {}
    sections: dict[str, dict[str, Any]] = {}
    cur: Optional[dict] = None
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if (m := re.match(r"^\s*Timing Path Group\s+'([^']+)'", ln)):
            cur = groups.setdefault(m.group(1), {})
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        s = ln.strip()
        if s and ":" not in s and _DASHES.match(nxt) and not _DASHES.match(ln):
            cur = sections.setdefault(s, {})
            continue
        if (m := re.match(rf"^\s*([A-Za-z][\w /.()\-]*?)\s*:\s*({_NUM})\s*$", ln)) and cur is not None:
            cur[m.group(1).strip()] = float(m.group(2))
    return {"groups": groups, "sections": sections}


# =========================================================== area
_AREA_KEYS = {
    "Number of ports": "ports", "Number of nets": "nets", "Number of cells": "cells",
    "Number of combinational cells": "combinational_cells", "Number of sequential cells": "sequential_cells",
    "Number of macros/black boxes": "macros", "Number of buf/inv": "buf_inv_cells",
    "Number of references": "references", "Combinational area": "combinational",
    "Buf/Inv area": "buf_inv", "Noncombinational area": "noncombinational",
    "Macro/Black Box area": "macro", "Net Interconnect area": "net_interconnect",
    "Total cell area": "total_cell", "Total area": "total",
}


def parse_libraries_used(text: str) -> list[str]:
    """Names from a 'Library(s) Used:' block (file paths are deliberately dropped)."""
    m = re.search(r"(?m)^\s*Library\(s\) Used:\s*$", text)
    if not m:
        return []
    out = []
    for ln in text[m.end():].splitlines():
        if not ln.strip():
            if out:
                break
            continue
        mm = re.match(r"^\s+(\S+)\s+\(File:", ln)
        if not mm:
            break
        out.append(mm.group(1))
    return out


def parse_area(text: str, top: Optional[str] = None) -> dict[str, Any]:
    """report_area [-hierarchy] -> {"summary": {...}, "hier": {path: area}, "libraries": [...]}"""
    summary: dict[str, Any] = {}
    for ln in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z/ ]*?):\s+(\S+)\s*$", ln)
        if m and m.group(1).strip() in _AREA_KEYS:
            key = _AREA_KEYS[m.group(1).strip()]
            v = _num(m.group(2))
            summary[key] = (int(v) if key in ("ports", "nets", "cells", "combinational_cells",
                                               "sequential_cells", "macros", "buf_inv_cells", "references")
                            and v is not None else v)
    return {"summary": summary, "hier": parse_area_hierarchy(text, top), "libraries": parse_libraries_used(text)}


def parse_area_hierarchy(text: str, top: Optional[str] = None) -> dict[str, float]:
    m = re.search(r"(?m)^\s*Hierarchical area distribution", text)
    if not m:
        return {}
    lines = text[m.end():].splitlines()
    # the column header ends at a dashes line with several dash groups; rows end at the next one
    # (the indented underline below "Global cell area" is not a table separator)
    sep = [i for i, ln in enumerate(lines) if re.match(r"^-{3,}(\s+-{3,})+\s*$", ln)]
    if not sep:
        sep = [i for i, ln in enumerate(lines) if re.match(r"^\s*-{3,}(\s+-{3,})+\s*$", ln)][1:]
    if not sep:
        return {}
    first = sep[0]
    last = next((i for i in sep[1:] if i > first), len(lines))
    rows = _join_wrapped(lines[first + 1:last])
    out: dict[str, float] = {}
    top_name = None
    for ln in rows:
        toks = ln.split()
        if len(toks) < 2:
            continue
        v = _num(toks[1])
        if v is None:
            continue
        name = toks[0]
        if top_name is None:
            top_name = name
        key = "" if name == top_name or (top and name == top) else name
        out[key] = sig(v)
    return out


# =========================================================== constraints
_CONSTRAINT_HDR = re.compile(r"^\s*([a-z_]+(?:/[a-z_]+)?)\s*(?:\('([^']+)'\s+group\))?\s*$")
_CONSTRAINT_ROW = re.compile(
    rf"^\s*(\S+)\s+({_NUM})\s+({_NUM})\s*([rf])?\s+({_NUM})\s*\((VIOLATED|MET)[^)]*\)")


def parse_constraints(text: str) -> dict[str, Any]:
    """report_constraints -all_violators -> {"setup": [...], "hold": [...], "drv": {kind: [...]}, "clean": bool}

    Entries are dicts {name, group, required, actual, slack} in report units (time for setup/hold,
    native units for design-rule constraints)."""
    out: dict[str, Any] = {"setup": [], "hold": [], "drv": {}, "clean": False}
    if re.search(r"(?i)has no violated constraints", text):
        out["clean"] = True
    cur_kind = cur_group = None
    for ln in _join_wrapped(text.splitlines()):
        if (m := _CONSTRAINT_HDR.match(ln)) and m.group(1) not in ("endpoint", "net", "pin", "port"):
            name = m.group(1)
            if "/" in name or name.startswith(("max_", "min_")) or name in ("critical_range",):
                cur_kind, cur_group = name, m.group(2)
                continue
        if cur_kind and (m := _CONSTRAINT_ROW.match(ln)):
            e = {"name": m.group(1), "group": cur_group, "required": float(m.group(2)),
                 "actual": float(m.group(3)), "slack": float(m.group(5))}
            if cur_kind.endswith("/setup") or cur_kind == "max_delay":
                out["setup"].append(e)
            elif cur_kind.endswith("/hold") or cur_kind == "min_delay":
                out["hold"].append(e)
            else:
                out["drv"].setdefault(cur_kind, []).append(e)
    return out


# =========================================================== power
_POWER_LINE = re.compile(
    rf"^\s*(Cell Internal Power|Net Switching Power|Total Dynamic Power|Cell Leakage Power)\s*=\s*({_NUM})\s*([a-zA-Zµ]*W)?")


def parse_power(text: str) -> dict[str, Optional[float]]:
    """report_power -> {internal, switching, dynamic, leakage, total} in mW."""
    dyn_unit = leak_unit = None
    if (m := re.search(r"(?im)Dynamic Power Units\s*=\s*([\d.eE+-]*\s*[a-zµ]*W)\b", text)):
        dyn_unit = m.group(1).replace(" ", "")
    if (m := re.search(r"(?im)Leakage Power Units\s*=\s*([\d.eE+-]*\s*[a-zµ]*W)\b", text)):
        leak_unit = m.group(1).replace(" ", "")
    vals: dict[str, Optional[float]] = {"internal": None, "switching": None, "dynamic": None,
                                        "leakage": None, "total": None}
    keymap = {"Cell Internal Power": "internal", "Net Switching Power": "switching",
              "Total Dynamic Power": "dynamic", "Cell Leakage Power": "leakage"}
    for ln in text.splitlines():
        if (m := _POWER_LINE.match(ln)):
            key = keymap[m.group(1)]
            default = leak_unit if key == "leakage" else dyn_unit
            vals[key] = sig(power_to_mw(float(m.group(2)), m.group(3) or default or "mW"))
    # power-group table "Total" row: internal switching leakage total (each with optional unit)
    tot = re.search(rf"(?m)^Total\s+({_NUM})\s*([a-zA-Zµ]*W)?\s+({_NUM})\s*([a-zA-Zµ]*W)?\s+"
                    rf"({_NUM})\s*([a-zA-Zµ]*W)?\s+({_NUM})\s*([a-zA-Zµ]*W)?", text)
    if tot:
        g = tot.groups()
        row = {"internal": power_to_mw(float(g[0]), g[1] or dyn_unit or "mW"),
               "switching": power_to_mw(float(g[2]), g[3] or dyn_unit or "mW"),
               "leakage": power_to_mw(float(g[4]), g[5] or leak_unit or "mW"),
               "total": power_to_mw(float(g[6]), g[7] or dyn_unit or "mW")}
        for k, v in row.items():
            if vals.get(k) is None:
                vals[k] = sig(v)
    if vals["dynamic"] is None and vals["internal"] is not None and vals["switching"] is not None:
        vals["dynamic"] = sig(vals["internal"] + vals["switching"])
    if vals["total"] is None and vals["dynamic"] is not None and vals["leakage"] is not None:
        vals["total"] = sig(vals["dynamic"] + vals["leakage"])
    return vals


# =========================================================== reference
_REF_ROW = re.compile(
    rf"^\s*(\S+)\s+(?:([A-Za-z_][\w.\-]*)\s+)?({_NUM})\s+(\d+)\s+({_NUM})\s*(.*)$")


def parse_reference(text: str, top: Optional[str] = None) -> dict[str, Any]:
    """report_reference [-hierarchy] -> {"cells": {cell: flattened count}, "designs": {design: [rows]},
    "unexpanded": [hier refs without a section]}.

    With -hierarchy each sub-design gets its own table; leaf counts are multiplied by how many
    times each design is instantiated, giving flattened per-cell counts for the whole top."""
    designs: dict[str, list[dict]] = {}
    order: list[str] = []
    cur: Optional[str] = None
    for ln in _join_wrapped(text.splitlines()):
        if (m := re.match(r"^\s*Design\s*:\s*(\S+)", ln)):
            cur = m.group(1)
            if cur not in designs:
                designs[cur] = []
                order.append(cur)
            continue
        if cur is None or ln.strip().startswith(("Total", "Reference")):
            continue
        if (m := _REF_ROW.match(ln)):
            ref, lib, unit, cnt, total, attrs = m.groups()
            attr_set = {a for a in re.split(r"[,\s]+", attrs.strip()) if a}
            designs[cur].append({"ref": ref, "library": lib, "unit_area": float(unit), "count": int(cnt),
                                 "total_area": float(total), "hier": "h" in attr_set or not lib,
                                 "attrs": sorted(attr_set)})
    if not order:
        return {"cells": {}, "designs": {}, "unexpanded": []}
    root = top if top in designs else order[0]
    mult: dict[str, int] = {root: 1}
    # propagate instance multiplicities top-down (designs appear parent-first, but iterate to fixpoint)
    for _ in range(len(order) + 1):
        new = {root: 1}
        for d in order:
            if d not in mult:
                continue
            for r in designs[d]:
                if r["hier"]:
                    new[r["ref"]] = new.get(r["ref"], 0) + mult[d] * r["count"]
        if new == mult:
            break
        mult = new
    cells: dict[str, int] = {}
    unexpanded = []
    for d in order:
        if d not in mult:
            continue
        for r in designs[d]:
            if r["hier"]:
                if r["ref"] not in designs:
                    unexpanded.append(r["ref"])
                continue
            cells[r["ref"]] = cells.get(r["ref"], 0) + r["count"] * mult[d]
    return {"cells": dict(sorted(cells.items())), "designs": designs, "unexpanded": sorted(set(unexpanded))}


# =========================================================== resources
def parse_resources(text: str) -> list[dict[str, Any]]:
    """report_resources [-hierarchy] -> list of {"table", "design"?, "file"?, rows: [{col: val}]}."""
    tables: list[dict[str, Any]] = []
    lines = text.splitlines()
    title = None
    title_open = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            title_open = False
        elif not s.startswith(("|", "=")) and not _STARS.match(ln):
            if "Report" in s and not s.startswith("Report :"):
                title, title_open = s, True
            elif title_open and ln[:1].isspace():
                title = f"{title} {s}"
        if s.startswith("=") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            # header rows until the next '=' line
            j = i + 1
            hdr_rows = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                hdr_rows.append(_cells(lines[j]))
                j += 1
            if j >= len(lines) or not lines[j].strip().startswith("="):
                i = j
                continue
            ncol = max(len(r) for r in hdr_rows)
            cols = [" ".join(r[c] for r in hdr_rows if c < len(r) and r[c]).strip().lower() or f"col{c}"
                    for c in range(ncol)]
            j += 1
            rows: list[dict[str, str]] = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                cells = _cells(lines[j])
                if rows and cells and not cells[0]:
                    for c, v in zip(cols, cells):
                        if v:
                            rows[-1][c] = (rows[-1].get(c, "") + " " + v).strip()
                else:
                    rows.append({c: v for c, v in zip(cols, cells)})
                j += 1
            t: dict[str, Any] = {"table": title or "", "columns": cols, "rows": rows}
            if title and (m := re.search(r"for (?:this hierarchy|design '?([^'\s]+?)'?)\s+in file\s+(\S+)", title)):
                if m.group(1):
                    t["design"] = m.group(1)
                t["file"] = Path(m.group(2)).name
            tables.append(t)
            title = None
            i = j + 1
            continue
        i += 1
    return tables


def _cells(line: str) -> list[str]:
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


# =========================================================== design / units
def parse_design(text: str) -> dict[str, Any]:
    """report_design -> {"operating_conditions": {...}, "libraries": [...], "wire_load_model": ..., ...}"""
    out: dict[str, Any] = {"libraries": parse_libraries_used(text)}
    oc: dict[str, Any] = {}
    if (m := re.search(r"(?m)^\s*Operating Condition Name\s*:\s*(\S+)", text)):
        oc["name"] = m.group(1)
        tail = text[m.end():]
        for key, rx in (("library", r"Library\s*:\s*(\S+)"), ("process", rf"Process\s*:\s*({_NUM})"),
                        ("temperature", rf"Temperature\s*:\s*({_NUM})"), ("voltage", rf"Voltage\s*:\s*({_NUM})")):
            mm = re.search(rf"(?m)^\s*{rx}", tail[:600])
            if mm:
                oc[key] = mm.group(1) if key == "library" else float(mm.group(1))
    elif (m := re.search(r"(?m)^\s*Operating Conditions?:\s*(\S+)", text)) and m.group(1) not in ("",):
        oc["name"] = m.group(1)
    if oc:
        out["operating_conditions"] = oc
    if (m := re.search(r"(?m)^\s*Wire Loading Model:\s*$", text)):
        mm = re.search(r"(?m)^\s*Name\s*:\s*(\S+)", text[m.end():m.end() + 800])
        if mm:
            out["wire_load_model"] = mm.group(1)
    if (m := re.search(r"(?m)^\s*Wire Loading Model Mode:\s*(\S+)", text)):
        out["wire_load_mode"] = m.group(1).rstrip(".")
    return out


# =========================================================== log
_MSG_START = re.compile(r"^(Error|Warning|Information)\s*:\s*(.*)$")
_MSG_CODE = re.compile(r"\(([A-Z][A-Z0-9_]*-\d+)\)\s*$")
_FILE_LINE = re.compile(r"(?:^|\s)'?(\S+\.(?:sv|v|svh|vh|vhd|vhdl|tcl|sdc))'?(?::|, line |\s*line\s*)(\d+)")
_REMOVED = re.compile(r"register\s+'([^']+)'", re.I)


@dataclass
class LogResult:
    messages: list[Msg]
    removed_registers: list[dict]
    counts: dict[str, int]


def _iter_messages(text: str) -> Iterable[tuple[str, str]]:
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _MSG_START.match(lines[i])
        if not m:
            i += 1
            continue
        sev, body = m.group(1), m.group(2).strip()
        j = i + 1
        while not _MSG_CODE.search(body) and j < len(lines) and j - i < 6:
            nxt = lines[j]
            if not nxt.strip() or _MSG_START.match(nxt) or not nxt[:1].isspace():
                break
            body += " " + nxt.strip()
            j += 1
        yield sev, re.sub(r"\s+", " ", body)
        i = j


def parse_log(text: str) -> LogResult:
    """DC log -> deduplicated warnings/errors (one Msg per code with a count) + removed registers."""
    counts = {"error": 0, "warning": 0, "information": 0}
    seen: dict[tuple[str, str], dict] = {}
    removed: list[dict] = []
    removed_keys = set()
    for sev, body in _iter_messages(text):
        counts[sev.lower()] += 1
        cm = _MSG_CODE.search(body)
        code = cm.group(1) if cm else None
        txt = _MSG_CODE.sub("", body).strip()
        low = txt.lower()
        if "register" in low and ("removed" in low or "remove" in low or "constant" in low
                                  or (code or "").startswith("OPT-12")):
            rm = _REMOVED.search(txt)
            if rm:
                dm = re.search(r"design\s+'([^']+)'", txt)
                reason = ("constant" if "constant" in low else "merged" if "merged" in low
                          else "unloaded" if ("unloaded" in low or code == "OPT-1207") else "removed")
                key = (rm.group(1), dm.group(1) if dm else None)
                if key not in removed_keys:
                    removed_keys.add(key)
                    removed.append({"register": rm.group(1), "design": key[1], "reason": reason, "code": code})
        if sev == "Information":
            continue
        k = (sev, code or txt)
        if k in seen:
            seen[k]["n"] += 1
            continue
        fm = _FILE_LINE.search(txt)
        seen[k] = {"sev": sev, "code": code, "text": txt, "n": 1,
                   "file": fm.group(1) if fm else None, "line": int(fm.group(2)) if fm else None}
    msgs = []
    for v in seen.values():
        text_ = v["text"] + (f" (x{v['n']})" if v["n"] > 1 else "")
        msgs.append(Msg(severity=Severity.error if v["sev"] == "Error" else Severity.warning, code=v["code"],
                        text=text_, file=v["file"], line=v["line"]))
    msgs.sort(key=lambda m: 0 if m.severity == Severity.error else 1)
    return LogResult(msgs, removed, counts)


# =========================================================== combine
def looks_like_log(text: str) -> bool:
    return bool(re.search(r"(?m)^(Error|Warning|Information)\s*:.*\([A-Z]+-\d+\)", text))


def parse_texts(reports: list[str], logs: list[str] | None = None, *, top: str = "", tool: str = "dc",
                vt_suffix: dict[str, str] | None = None, period_ps: float | None = None,
                time_unit: str | float | None = None, corner: str | None = None) -> SynthResult:
    """Combine report texts (each may be a combined .rep) and logs into one SynthResult.

    period_ps / corner : known values (e.g. from knobs) — they win over report values.
    time_unit          : default report time unit ("ns", "ps", or ps-per-unit) when the reports don't say.
    """
    from svkit.analysis.vt import vt_mix as _vt_mix

    logs = logs or []
    sections: dict[str, list[Section]] = {}
    all_text = "\n".join(reports)
    for t in reports:
        secs = split_reports(t)
        if not secs and t.strip():
            secs = [Section(kind=_guess_kind(t), text=t, design=banner_design(t))]
        for s in secs:
            sections.setdefault(s.kind, []).append(s)
    # logs that also contain reports (reports echoed to stdout) contribute kinds not seen otherwise
    for t in logs:
        for s in split_reports(t):
            if s.kind not in sections:
                sections.setdefault(s.kind, []).append(s)

    def texts(kind: str) -> list[str]:
        return [s.text for s in sections.get(kind, [])]

    design = top or next((s.design for ss in sections.values() for s in ss if s.design), None) or ""
    extra: dict[str, Any] = {"reports": sorted(sections)}

    raw_paths = [p for t in texts("timing") for p in parse_timing_raw(t)]
    qor = [parse_qor(t) for t in texts("qor")]
    qor_groups: dict[str, dict] = {}
    for q in qor:
        qor_groups.update(q["groups"])

    # ---- time unit
    raw_period = raw_clock_period(raw_paths)
    if raw_period is None:
        per = [g.get("Critical Path Clk Period") for g in qor_groups.values() if g.get("Critical Path Clk Period")]
        raw_period = per[0] if per else None
    hint = detect_time_unit(all_text + "\n" + "\n".join(logs))
    if hint is not None:
        scale, src = hint, "report"
    elif (inf := infer_time_unit(raw_period, period_ps)) is not None:
        scale, src = inf, "inferred from clock period"
    elif time_unit is not None:
        scale = float(time_unit) if isinstance(time_unit, (int, float)) else time_unit_to_ps(str(time_unit))
        src = "configured"
    elif raw_period is not None and raw_period >= 100:
        scale, src = 1.0, "heuristic (period >= 100 units)"
    else:
        scale, src = 1000.0, "default"
    extra["time_unit"] = {1.0: "ps", 1000.0: "ns"}.get(scale, f"{scale}ps")
    extra["time_unit_source"] = src

    def ps(v: Optional[float]) -> Optional[float]:
        return sig(v * scale) if v is not None else None

    res = SynthResult(tool=tool, top=design)

    # ---- timing paths
    paths = [rp.to_path(scale) for rp in raw_paths]
    setup = sorted([p for p in paths if p.path_type == "max"], key=lambda p: p.slack)
    hold = sorted([p for p in paths if p.path_type == "min"], key=lambda p: p.slack)
    res.paths = setup + hold
    if setup:
        res.wns = setup[0].slack
        res.tns = sig(sum(min(0.0, p.slack) for p in _worst_per_endpoint(setup)))
    if hold:
        res.hold_wns = hold[0].slack
        res.hold_tns = sig(sum(min(0.0, p.slack) for p in _worst_per_endpoint(hold)))

    # ---- qor (authoritative for WNS/TNS over a handful of reported paths)
    if qor_groups:
        extra["qor"] = qor_groups
        slacks = [g["Critical Path Slack"] for g in qor_groups.values() if "Critical Path Slack" in g]
        if slacks:
            res.wns = ps(min(slacks))
        tnss = [g["Total Negative Slack"] for g in qor_groups.values() if "Total Negative Slack" in g]
        if tnss:
            res.tns = ps(-sum(abs(x) for x in tnss))
        hv = [g["Worst Hold Violation"] for g in qor_groups.values() if "Worst Hold Violation" in g]
        if hv and any(abs(x) > 0 for x in hv):
            res.hold_wns = ps(-max(abs(x) for x in hv))
            th = [g.get("Total Hold Violation", 0.0) for g in qor_groups.values()]
            res.hold_tns = ps(-sum(abs(x) for x in th))
    qsec: dict[str, dict] = {}
    for q in qor:
        for k, v in q["sections"].items():
            qsec.setdefault(k, {}).update(v)
    if qsec:
        extra["qor_sections"] = qsec

    # ---- constraints (exact for violators)
    cons = [parse_constraints(t) for t in texts("constraint")]
    if cons:
        su = [e for c in cons for e in c["setup"]]
        ho = [e for c in cons for e in c["hold"]]
        if su:
            res.wns = ps(min(e["slack"] for e in su))
            res.tns = ps(sum(min(0.0, e["slack"]) for e in su))
        elif all(c["clean"] for c in cons) or not any(c["setup"] for c in cons):
            if res.tns is None and res.wns is not None:
                res.tns = 0.0
        if ho:
            res.hold_wns = ps(min(e["slack"] for e in ho))
            res.hold_tns = ps(sum(min(0.0, e["slack"]) for e in ho))
        drv: dict[str, dict] = {}
        for c in cons:
            for kind, rows in c["drv"].items():
                d = drv.setdefault(kind, {"count": 0, "worst_slack": None, "worst": None})
                for r in rows:
                    d["count"] += 1
                    if d["worst_slack"] is None or r["slack"] < d["worst_slack"]:
                        d["worst_slack"], d["worst"] = r["slack"], r["name"]
        if drv:
            extra["constraint_violations"] = drv
            for kind, d in drv.items():
                res.warnings.append(Msg(severity=Severity.warning, code=kind,
                                        text=f"{d['count']} {kind} violation(s); worst {d['worst']} "
                                             f"(slack {d['worst_slack']:g})"))
        extra["setup_violators"] = len(su)
        extra["hold_violators"] = len(ho)

    # ---- period
    if period_ps is not None:
        res.period = sig(float(period_ps))
    elif raw_period is not None:
        res.period = ps(raw_period)

    # ---- area
    areas = [parse_area(t, design) for t in texts("area")]
    libs: list[str] = []
    for a in areas:
        s = a["summary"]
        if s.get("total_cell") is not None and res.area_total is None:
            res.area_total = sig(s["total_cell"])
        if a["hier"]:
            res.area_by_hier = a["hier"]
        if s:
            extra.setdefault("area", {}).update({k: v for k, v in s.items() if v is not None})
        libs += a["libraries"]
    if res.area_total is None and qsec.get("Area", {}).get("Cell Area") is not None:
        res.area_total = sig(qsec["Area"]["Cell Area"])

    # ---- references
    for t in texts("reference"):
        ref = parse_reference(t, design)
        if ref["cells"]:
            res.cells_by_type = ref["cells"]
            res.cell_count = sum(ref["cells"].values())
        if ref["unexpanded"]:
            extra["unexpanded_hier_refs"] = ref["unexpanded"]
    if res.cell_count is None:
        leaf = qsec.get("Cell Count", {}).get("Leaf Cell Count")
        if leaf is not None:
            res.cell_count = int(leaf)
        elif any(a["summary"].get("cells") is not None for a in areas):
            res.cell_count = next(a["summary"]["cells"] for a in areas if a["summary"].get("cells") is not None)
    if vt_suffix and res.cells_by_type:
        res.vt_mix = _vt_mix(res.cells_by_type, vt_suffix)

    # ---- power
    for t in texts("power"):
        pw = parse_power(t)
        res.power = Power(dynamic=pw["dynamic"], leakage=pw["leakage"], total=pw["total"])
        extra["power"] = {k: v for k, v in pw.items() if v is not None}

    # ---- resources
    rtabs = [tb for t in texts("resources") for tb in parse_resources(t)]
    if rtabs:
        extra["resources"] = rtabs

    # ---- design / corner / libraries
    oc_name = None
    for t in texts("design"):
        d = parse_design(t)
        libs += d["libraries"]
        if "operating_conditions" in d:
            extra["operating_conditions"] = d["operating_conditions"]
            oc_name = d["operating_conditions"].get("name")
        for k in ("wire_load_model", "wire_load_mode"):
            if k in d:
                extra[k] = d[k]
    if oc_name is None:
        for t in texts("timing"):
            h = timing_header(t)
            if "operating_condition" in h:
                oc_name = h["operating_condition"]
                break
    res.corner = corner or oc_name
    if libs:
        extra["libraries"] = list(dict.fromkeys(libs))

    # ---- logs
    removed: list[dict] = []
    counts: dict[str, int] = {}
    for t in logs:
        lr = parse_log(t)
        res.warnings = lr.messages + res.warnings
        removed += lr.removed_registers
        for k, v in lr.counts.items():
            counts[k] = counts.get(k, 0) + v
    if logs:
        extra["log_counts"] = counts
        extra["removed_registers"] = removed

    res.extra = extra
    return res


def _worst_per_endpoint(paths: list[TimingPath]) -> list[TimingPath]:
    seen: dict[str, TimingPath] = {}
    for p in paths:
        if p.endpoint not in seen or p.slack < seen[p.endpoint].slack:
            seen[p.endpoint] = p
    return list(seen.values())


def _guess_kind(text: str) -> str:
    """Report text without a banner: guess by content."""
    if re.search(r"(?m)^\s*Startpoint:", text):
        return "timing"
    if "Timing Path Group" in text:
        return "qor"
    if re.search(r"Total cell area|Hierarchical area distribution", text):
        return "area"
    if re.search(r"Cell Internal Power|Cell Leakage Power", text):
        return "power"
    if re.search(r"max_delay/setup|min_delay/hold|no violated constraints", text):
        return "constraint"
    if re.search(r"(?m)^Reference\s+Library\s+Unit Area", text):
        return "reference"
    if "Operating Condition Name" in text:
        return "design"
    return "unknown"


def parse_files(files: list[Path], *, top: str = "", **kw: Any) -> SynthResult:
    """Offline parse of report / log files. Files that look like DC logs (messages with codes
    and no report banners, or a .log/.out suffix) are parsed as logs."""
    reports, logs = [], []
    for f in files:
        t = Path(f).read_text(errors="replace")
        is_log = Path(f).suffix in (".log", ".out") or (looks_like_log(t) and not split_reports(t))
        (logs if is_log else reports).append(t)
    return parse_texts(reports, logs, top=top, **kw)
