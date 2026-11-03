"""Yosys synthesis adapter (open-source flow), with timing/power from an STA adapter.

Modes
  builtin : svkit generates a yosys script in the work dir: read_verilog -sv (defines, include dirs),
            hierarchy, synth -top, dfflibmap/abc against the Liberty files of the selected corner,
            `stat -json`, and writes netlist.v / netlist.json.
  wrap    : run the user's `flow.cmd`; read `flow.reports` (yosys `stat -json` output) and
            `flow.netlist`. The command sees SVKIT_LIBERTY (space-separated, corner applied),
            SVKIT_CORNER, SVKIT_TOP and SVKIT_PERIOD_PS in its environment.

Knobs understood by the builtin mode (read from KnobPlan.values, all optional):
  clock_period (ps)  -> `abc -D` delay target and the STA clock
  corner             -> Liberty files from [library].corners[corner] (else [library].liberty)
  dont_use           -> cell-name globs excluded from dfflibmap/abc (string or list)
`flow.args` are extra arguments for the `synth` command (e.g. ["-flatten"]).
If `flow.sta` names an STA flow, that adapter is run on the netlist and its timing/power merged.
"""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Optional

from svkit.adapters import registry
from svkit.adapters.base import RunContext, StaAdapter, SynthAdapter
from svkit.adapters.sim.verilator import order_sources
from svkit.analysis.vt import vt_mix
from svkit.config import Config, ConfigError, SynthFlow
from svkit.core import project as P
from svkit.schemas import Msg, Severity, SynthResult

OUT = "svkit_out"


# ---------------------------------------------------------------------------- parsing (offline)
def _strip(name: str) -> str:
    return name[1:] if name.startswith("\\") else name


def parse_stat_json(stat: dict[str, Any], *, top: str, netlist: Optional[dict[str, Any]] = None) -> SynthResult:
    """Yosys `stat -json [-liberty]` (+ optional `write_json` netlist for instance names) -> SynthResult."""
    mods = {_strip(k): v for k, v in (stat.get("modules") or {}).items()}
    design = stat.get("design") or (mods.get(top) if top in mods else (next(iter(mods.values())) if mods else {}))
    res = SynthResult(tool="yosys", top=top)
    cbt = {k: int(v) for k, v in (design.get("num_cells_by_type") or {}).items() if _strip(k) not in mods}
    res.cells_by_type = dict(sorted(cbt.items()))
    res.cell_count = sum(cbt.values())
    if design.get("area") is not None:
        res.area_total = float(design["area"])
    # ---- per-hierarchy area
    if mods and res.area_total is not None:
        res.area_by_hier = area_by_hier(mods, top=top, design_area=res.area_total, netlist=netlist)
    if design.get("sequential_area") is not None:
        res.extra["sequential_area"] = float(design["sequential_area"])
    return res


def _children(mods: dict[str, Any], name: str) -> dict[str, int]:
    """Submodule types instantiated in module `name` (from num_cells_by_type) -> count."""
    by = (mods.get(name) or {}).get("num_cells_by_type") or {}
    return {_strip(k): int(v) for k, v in by.items() if _strip(k) in mods}


def area_by_hier(mods: dict[str, Any], *, top: str, design_area: float,
                 netlist: Optional[dict[str, Any]] = None) -> dict[str, float]:
    """Inclusive area per hierarchical instance path ('' = top).

    Yosys versions differ on whether a module's `area` includes its submodules; detect it by
    comparing the top module's area with the design total."""
    if top not in mods:
        return {"": design_area}
    has_children = bool(_children(mods, top))
    top_area = float(mods[top].get("area") or 0.0)
    inclusive = (not has_children) or abs(top_area - design_area) < 1e-6 * max(1.0, design_area)
    memo: dict[str, float] = {}

    def incl(m: str) -> float:
        if m not in memo:
            a = float(mods.get(m, {}).get("area") or 0.0)
            if not inclusive:
                a += sum(n * incl(c) for c, n in _children(mods, m).items())
            memo[m] = a
        return memo[m]

    out: dict[str, float] = {"": design_area}
    insts = _instances(netlist, set(mods)) if netlist else None

    def walk(m: str, path: str, depth: int) -> None:
        if depth > 64:
            return
        if insts is not None:
            kids = insts.get(m, [])
        else:   # no netlist: name children by module type (#k when instantiated several times)
            kids = []
            for c, n in _children(mods, m).items():
                kids += [(c if n == 1 else f"{c}#{i}", c) for i in range(n)]
        for iname, mtype in kids:
            p = f"{path}/{iname}" if path else iname
            out[p] = incl(mtype)
            walk(mtype, p, depth + 1)

    walk(top, "", 0)
    return out


def _instances(netlist: dict[str, Any], modnames: set[str]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for mname, m in (netlist.get("modules") or {}).items():
        lst = []
        for cname, c in (m.get("cells") or {}).items():
            t = _strip(c.get("type", ""))
            if t in modnames:
                lst.append((_strip(cname), t))
        out[_strip(mname)] = sorted(lst)
    return out


def parse_log_warnings(text: str, limit: int = 100) -> list[Msg]:
    out, seen = [], set()
    for line in text.splitlines():
        m = re.match(r"^(Warning|ERROR):\s*(.*)$", line.strip())
        if m and m.group(2) not in seen:
            seen.add(m.group(2))
            out.append(Msg(severity=Severity.error if m.group(1) == "ERROR" else Severity.warning,
                           text=m.group(2), code="yosys"))
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------------------- helpers
def liberty_for(cfg: Config, corner: Optional[str]) -> list[Path]:
    lib = cfg.library
    if corner is not None and str(corner) in lib.corners:
        files = lib.corners[str(corner)]
    else:
        if corner is not None and lib.corners:
            raise ConfigError(f"corner {corner!r} not in [library].corners (have: {', '.join(lib.corners)})")
        files = lib.liberty
    out = []
    for f in files:
        p = Path(P.tmpl(f, cfg))
        out.append((p if p.is_absolute() else cfg.root / p).resolve())
    return out


def liberty_cells(liberty: list[Path]) -> list[str]:
    cells: list[str] = []
    for lib in liberty:
        try:
            txt = lib.read_text(errors="replace")
        except OSError:
            continue
        cells += re.findall(r"\bcell\s*\(\s*\"?([A-Za-z0-9_$.\[\]]+)\"?\s*\)", txt)
    return sorted(set(cells))


def expand_dont_use(value: Any, liberty: list[Path]) -> list[str]:
    if value in (None, "", False):
        return []
    pats = value if isinstance(value, list) else re.split(r"[\s,]+", str(value))
    pats = [p for p in (str(x).strip() for x in pats) if p]
    cells = liberty_cells(liberty)
    out = [c for c in cells if any(fnmatch.fnmatchcase(c, p) for p in pats)]
    return out


def _q(p: Path | str) -> str:
    s = str(p)
    return '"' + s + '"' if re.search(r"\s", s) else s


def build_script(cfg: Config, flow: SynthFlow, *, liberty: list[Path], period_ps: Optional[float],
                 dont_use: list[str], defines: dict[str, str] | None = None) -> str:
    top = cfg.project.top
    files = order_sources(P.resolve_sources(cfg))
    if not files:
        raise ConfigError("no [project].sources resolved for synthesis")
    rd = ["read_verilog", "-sv"]
    for k, v in {**cfg.project.defines, **(defines or {})}.items():
        if v is False:
            continue
        rd.append(f"-D{k}" if v is True or v == "" else f"-D{k}={v}")
    for d in P.include_dirs(cfg):
        rd.append(f"-I{_q(d)}")
    rd += [_q(f) for f in files]
    libs = " ".join(f"-liberty {_q(l)}" for l in liberty)
    du = " ".join(f"-dont_use {c}" for c in dont_use)
    abc = f"abc {libs}" + (f" -D {period_ps:g}" if period_ps else "") + (f" {du}" if du else "")
    lines = [
        "# generated by svkit (YosysAdapter, builtin mode)",
        " ".join(rd),
        f"hierarchy -check -top {top}",
        f"synth -top {top} " + " ".join(flow.args),
        f"dfflibmap {libs}" + (f" {du}" if du else ""),
        abc,
        "opt_clean -purge",
        f"tee -q -o {OUT}/stat.json stat -json {libs}",
        f"tee -q -o {OUT}/stat.txt stat {libs}",
        f"write_verilog -noattr {OUT}/netlist.v",
        f"write_json {OUT}/netlist.json",
    ]
    return "\n".join(l for l in lines if l) + "\n"


# ---------------------------------------------------------------------------- adapter
class YosysAdapter(SynthAdapter):
    name = "yosys"
    tools = ("yosys",)

    def parse(self, files: list[Path], *, top: str) -> SynthResult:
        stat = netlist = None
        for f in files:
            if f.suffix != ".json" or not f.is_file():
                continue
            data = json.loads(f.read_text())
            if "design" in data or ("modules" in data and any("num_cells" in m for m in data["modules"].values())):
                stat = data
            elif "modules" in data:
                netlist = data
        if stat is None:
            raise ValueError(f"no yosys `stat -json` report among {[str(f) for f in files]}")
        res = parse_stat_json(stat, top=top, netlist=netlist)
        res.vt_mix = vt_mix(res.cells_by_type, self.cfg.library.vt_suffix)
        return res

    def run(self, ctx: RunContext, flow: SynthFlow) -> SynthResult:
        cfg = ctx.cfg
        vals = ctx.plan.values
        corner = vals.get("corner")
        liberty = liberty_for(cfg, corner)
        period = ctx.plan.period_ps
        cwd = (ctx.workdir / flow.cwd).resolve()
        log = ctx.run_dir / "synth.log"
        if flow.mode == "builtin":
            if not liberty:
                raise ConfigError("yosys builtin flow needs [library].liberty (or [library].corners)")
            dont_use = expand_dont_use(vals.get("dont_use"), liberty)
            (cwd / OUT).mkdir(parents=True, exist_ok=True)
            script = cwd / "svkit_synth.ys"
            script.write_text(build_script(cfg, flow, liberty=liberty, period_ps=period, dont_use=dont_use,
                                           defines=ctx.plan.defines))
            ctx.keep(script, "synth.ys")
            rc = ctx.sh([cfg.tool("yosys"), "-s", str(script)], cwd=cwd, timeout=flow.timeout_s, log_file=log,
                        extra_env=flow.env)
            ctx.artifacts["synth.log"] = "synth.log"
            if rc != 0:
                raise RuntimeError(f"yosys failed (exit {rc}); see artifact synth.log")
            reports = [cwd / OUT / "stat.json", cwd / OUT / "netlist.json"]
            netlist_v = cwd / OUT / "netlist.v"
            ctx.keep(cwd / OUT / "stat.json", "stat.json")
            ctx.keep(cwd / OUT / "stat.txt", "stat.txt")
        else:
            env = {**flow.env, "SVKIT_LIBERTY": " ".join(str(l) for l in liberty), "SVKIT_TOP": cfg.project.top}
            if corner is not None:
                env["SVKIT_CORNER"] = str(corner)
            if period is not None:
                env["SVKIT_PERIOD_PS"] = f"{period:g}"
            cmd = P.tmpl(flow.cmd, cfg, workdir=ctx.workdir, run_dir=ctx.run_dir)
            ctx.log(f"[svkit] {cmd}  (cwd {cwd})")
            rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=log, extra_env=env)
            ctx.artifacts["synth.log"] = "synth.log"
            if rc != 0:
                raise RuntimeError(f"synthesis command failed (exit {rc}); see artifact synth.log")
            reports = []
            for r in flow.reports:
                f = Path(P.tmpl(r, cfg, workdir=ctx.workdir, run_dir=ctx.run_dir))
                f = f if f.is_absolute() else cwd / f
                if not f.is_file():
                    raise RuntimeError(f"expected report {f} was not produced")
                reports.append(f)
                ctx.keep(f, f.name)
            netlist_v = None
            if flow.netlist:
                n = Path(P.tmpl(flow.netlist, cfg, workdir=ctx.workdir, run_dir=ctx.run_dir))
                n = n if n.is_absolute() else cwd / n
                if n.suffix == ".json":
                    reports.append(n)
                else:
                    netlist_v = n
            if flow.log:
                lf = Path(P.tmpl(flow.log, cfg, workdir=ctx.workdir, run_dir=ctx.run_dir))
                lf = lf if lf.is_absolute() else cwd / lf
                if lf.is_file():
                    ctx.keep(lf, "tool-" + lf.name)

        res = self.parse(reports, top=cfg.project.top)
        res.tool = "yosys"
        res.corner = str(corner) if corner is not None else None
        res.period = period
        res.warnings += parse_log_warnings(log.read_text(errors="replace") if log.exists() else "")
        for f in reports:
            if f.name.endswith(".json") and f.is_file() and "netlist" in f.name:
                ctx.keep(f, "netlist.json")
        if netlist_v is not None and netlist_v.is_file():
            res.netlist = ctx.keep(netlist_v, "netlist.v")

        if flow.sta:
            if netlist_v is None or not netlist_v.is_file():
                raise RuntimeError(f"flows.synth.yosys.sta = {flow.sta!r} but no gate-level Verilog netlist")
            sta = registry.get(flow.sta, cfg)
            assert isinstance(sta, StaAdapter)
            t = sta.run(ctx, cfg.flows.sta[flow.sta], netlist=netlist_v, top=cfg.project.top, liberty=liberty,
                        period_ps=period)
            res.tool = f"yosys+{flow.sta}"
            res.period = t.period
            res.wns, res.tns, res.hold_wns, res.hold_tns = t.wns, t.tns, t.hold_wns, t.hold_tns
            res.paths, res.power = t.paths, t.power
            res.warnings += t.warnings
            res.extra.update(t.extra)
        return res
