"""Design Compiler synthesis adapter ("dc").

wrap mode (preferred)
    Runs `flow.cmd` (e.g. `make syn-dc`) in the isolated work dir the runner prepared. Knobs
    reach DC through the work-dir copy of `flow.script` (edited by the runner with
    `knobs.prepare_script`), environment variables and knob files. Reports (`flow.reports`, a
    combined `.rep` is split by its banners), the netlist and the log are kept and parsed.

builtin mode
    Generates a DC TCL script from `[project]` (analyze / elaborate / link / create_clock /
    compile / reports / netlist) in the work dir and runs `dc_shell-t -f` on it. Libraries come
    from `[library]` (`.db` entries of `corners[<corner>]` or `liberty`) or from the environment
    (`SVKIT_DC_TARGET_LIB`, `SVKIT_DC_LINK_LIB`, `SVKIT_DC_SEARCH_PATH`); nothing is hardcoded.

Time unit: DC reports use the library time unit. The parser detects it from the reports or
infers it from the knob clock period; `[flows.synth.dc].env.SVKIT_DC_TIME_UNIT = "ps"` sets the
fallback (default ns). In builtin mode the same setting converts the clock period for
`create_clock`.
"""
from __future__ import annotations

import glob as _glob
import os
from pathlib import Path
from typing import Any, Optional

from svkit.adapters.base import KnobPlan, RunContext, SynthAdapter
from svkit.adapters.synopsys import synopsys_seat
from svkit.adapters.synth import dc_reports as R
from svkit.config import ConfigError, SynthFlow
from svkit.core import knobs as K
from svkit.core import project as P
from svkit.schemas import Msg, Severity, SynthResult

TIME_UNIT_ENV = "SVKIT_DC_TIME_UNIT"
LIB_ENV = ("SVKIT_DC_TARGET_LIB", "SVKIT_DC_LINK_LIB", "SVKIT_DC_SEARCH_PATH")
BUILTIN_SCRIPT = "svkit_dc.tcl"
BUILTIN_REPORTS = {
    "design.rpt": "report_design",
    "units.rpt": "report_units",
    "qor.rpt": "report_qor",
    "timing.rpt": "report_timing -path full -delay_type max -max_paths {n} -input_pins -nets "
                  "-transition_time -capacitance",
    "timing_min.rpt": "report_timing -path full -delay_type min -max_paths {n}",
    "constraints.rpt": "report_constraints -all_violators",
    "area.rpt": "report_area -hierarchy",
    "power.rpt": "report_power",
    "reference.rpt": "report_reference -hierarchy",
    "resources.rpt": "report_resources -hierarchy",
}


class DcAdapter(SynthAdapter):
    name = "dc"
    tools = ("dc_shell-t",)

    # ------------------------------------------------------------ offline parse
    def parse(self, files: list[Path], *, top: str = "", period_ps: float | None = None,
              time_unit: str | None = None, corner: str | None = None) -> SynthResult:
        """Parse existing DC report / log files (a combined .rep is split by its banners)."""
        return R.parse_files([Path(f) for f in files], top=top, vt_suffix=self.cfg.library.vt_suffix,
                             period_ps=period_ps, time_unit=time_unit, corner=corner)

    # ------------------------------------------------------------ run
    def run(self, ctx: RunContext, flow: SynthFlow) -> SynthResult:
        if flow.sta:
            ctx.log(f"[svkit] dc: DC reports its own timing; flows.synth.dc.sta = {flow.sta!r} is not run")
        with synopsys_seat(ctx.cancel, ctx.log):
            if flow.mode == "builtin":
                reports, netlist, logs, rc, cmd = self._run_builtin(ctx, flow)
            else:
                reports, netlist, logs, rc, cmd = self._run_wrap(ctx, flow)
        return self._collect(ctx, flow, reports, netlist, logs, rc, cmd)

    # ------------------------------------------------------------ wrap
    def _vars(self, ctx: RunContext, flow: SynthFlow) -> dict[str, Any]:
        return dict(workdir=ctx.workdir, run_dir=ctx.run_dir, script=flow.script)

    def _run_wrap(self, ctx: RunContext, flow: SynthFlow):
        cwd = ctx.workdir / flow.cwd
        v = self._vars(ctx, flow)
        report_tmpls = [P.tmpl(r, self.cfg, **v) for r in flow.reports]
        netlist = self._path(cwd, flow.netlist, ctx, flow)
        user_log = self._path(cwd, flow.log, ctx, flow)
        # The work dir is a copy of the user's tree and may contain reports from an earlier run:
        # remove them so a failed run can never be parsed as a fresh one.
        for t in report_tmpls:
            for p in _expand(cwd, t):
                p.unlink(missing_ok=True)
        for p in (netlist, user_log):
            if p is not None:
                p.unlink(missing_ok=True)
        cmd = P.tmpl(flow.cmd or "", self.cfg, **v)
        stdout_log = ctx.run_dir / ("synth.stdout.log" if user_log else "synth.log")
        ctx.log(f"[svkit] dc (wrap): {cmd}  (in {cwd})")
        rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=stdout_log, extra_env=flow.env)
        reports = [p for t in report_tmpls for p in _expand(cwd, t)]
        logs = [user_log] if user_log is not None and user_log.is_file() else [stdout_log]
        return reports, netlist, logs, rc, cmd

    def _path(self, cwd: Path, tmpl: Optional[str], ctx: RunContext, flow: SynthFlow) -> Optional[Path]:
        if not tmpl:
            return None
        p = Path(P.tmpl(tmpl, self.cfg, **self._vars(ctx, flow)))
        return p if p.is_absolute() else cwd / p

    # ------------------------------------------------------------ builtin
    def _run_builtin(self, ctx: RunContext, flow: SynthFlow):
        cwd = ctx.workdir
        script = cwd / BUILTIN_SCRIPT
        script.write_text(self.builtin_script(ctx, flow))
        ctx.keep(script, "synth_script.tcl")
        (cwd / "reports").mkdir(exist_ok=True)
        cmd = [self.cfg.tool("dc_shell-t"), *flow.args, "-f", BUILTIN_SCRIPT]
        log = ctx.run_dir / "synth.log"
        ctx.log("[svkit] dc (builtin): " + " ".join(cmd))
        rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=log, extra_env=flow.env)
        reports = [cwd / "reports" / n for n in BUILTIN_REPORTS if (cwd / "reports" / n).is_file()]
        return reports, cwd / "netlist.v", [log], rc, " ".join(cmd)

    def _time_scale(self, flow: SynthFlow) -> float:
        u = flow.env.get(TIME_UNIT_ENV) or os.environ.get(TIME_UNIT_ENV) or "ns"
        return R.time_unit_to_ps(u)

    def _libraries(self, plan: KnobPlan) -> list[Path]:
        lib = self.cfg.library
        corner = plan.values.get("corner")
        entries = lib.corners.get(str(corner)) if corner is not None and str(corner) in lib.corners else lib.liberty
        out = []
        for e in entries or []:
            if e.endswith(".db"):
                p = Path(os.path.expandvars(os.path.expanduser(e)))
                out.append(p if p.is_absolute() else (self.cfg.root / p))
        return out

    def builtin_script(self, ctx: RunContext, flow: SynthFlow) -> str:
        """The generated DC TCL for builtin mode (knobs applied)."""
        cfg, plan = self.cfg, ctx.plan
        proj = cfg.project
        env_all = {**os.environ, **ctx.env, **flow.env}
        dbs = self._libraries(plan)
        if not dbs and not env_all.get("SVKIT_DC_TARGET_LIB"):
            raise ConfigError(
                "builtin DC flow needs libraries: list .db files in [library].liberty (or "
                "[library].corners.<corner>), or set SVKIT_DC_TARGET_LIB (and optionally "
                "SVKIT_DC_LINK_LIB / SVKIT_DC_SEARCH_PATH) in the environment or [flows.synth.dc].env")
        scale = self._time_scale(flow)
        period_ps = plan.period_ps
        if period_ps is None:
            period_ps = 10000.0
            ctx.log("[svkit] dc: no clock_period knob value; constraining the clock to 10000 ps")
        period = R.sig(period_ps / scale)
        sources = [_in_workdir(f, cfg.root, ctx.workdir) for f in P.resolve_sources(cfg)]
        if not sources:
            raise ConfigError("builtin DC flow: [project].sources matched no files")
        incdirs = [_in_workdir(d, cfg.root, ctx.workdir) for d in P.include_dirs(cfg)]
        defines = dict(proj.defines)
        if proj.sources.filelist:
            defines.update(P.parse_filelist(cfg.root / proj.sources.filelist)[2])
        defs = " ".join(_define(k, v) for k, v in defines.items() if v is not False)

        L: list[str] = ["# generated by svkit (builtin Design Compiler flow)"]
        L += plan.tcl_prelude if not flow.script else []
        L += [f"set svkit_top {proj.top}",
              "if {![info exists compile_cmd]} {set compile_cmd {compile -map_effort medium}}"]
        if dbs:
            L += [f"set search_path [concat $search_path {{{' '.join(_tcl(str(d.parent)) for d in dict.fromkeys(dbs))}}}]",
                  f"set target_library {{{' '.join(_tcl(d.name) for d in dbs)}}}",
                  "set link_library [concat {*} $target_library]"]
        else:
            L += ["set target_library $::env(SVKIT_DC_TARGET_LIB)",
                  "if {[info exists ::env(SVKIT_DC_LINK_LIB)]} {set link_library $::env(SVKIT_DC_LINK_LIB)} "
                  "else {set link_library [concat {*} $target_library]}",
                  "if {[info exists ::env(SVKIT_DC_SEARCH_PATH)]} "
                  "{set search_path [concat $search_path $::env(SVKIT_DC_SEARCH_PATH)]}"]
        if incdirs:
            L.append(f"set search_path [concat $search_path {{{' '.join(_tcl(str(d)) for d in incdirs)}}}]")
        L += ["define_design_lib WORK -path ./svkit_dc_work",
              f"analyze -format sverilog {'-define {' + defs + '} ' if defs else ''}"
              f"{{{' '.join(_tcl(str(s)) for s in sources)}}}",
              f"elaborate {proj.top}",
              f"current_design {proj.top}",
              "link",
              "uniquify",
              f"if {{[sizeof_collection [get_ports -quiet {proj.clock}]] > 0}} {{",
              f"  create_clock -name {proj.clock} -period {period:g} [get_ports {proj.clock}]",
              "} else {",
              f"  create_clock -name {proj.clock} -period {period:g}",
              "}"]
        if flow.script:
            L.append(f"source {_tcl(flow.script)}")
        L += ["# svkit:compile", "eval $compile_cmd", "file mkdir reports"]
        for fn, rep in BUILTIN_REPORTS.items():
            L.append(f"catch {{redirect -file reports/{fn} {{{rep.format(n=5)}}}}}")
        L += ["write -format verilog -hierarchy -output netlist.v", "exit", ""]
        text = "\n".join(L)
        if not flow.script and (plan.tcl_replace or plan.tcl_append):
            # plain appends go before compile (constraints); anchored ones where the user asked
            edits = KnobPlan(tcl_replace=list(plan.tcl_replace),
                             tcl_append=[(a or r"^# svkit:compile", t) for a, t in plan.tcl_append])
            text = K.edit_script(text, edits)
        return text

    # ------------------------------------------------------------ collect
    def _collect(self, ctx: RunContext, flow: SynthFlow, reports: list[Path], netlist: Optional[Path],
                 logs: list[Path], rc: int, cmd: str) -> SynthResult:
        logs = [p for p in logs if p.is_file()]
        if not reports:
            raise RuntimeError(f"`{cmd}` exited with code {rc} and produced no DC reports "
                               f"(expected {', '.join(flow.reports) or 'builtin reports'}); see synth.log")
        # keep artifacts
        used: set[str] = set(ctx.artifacts)
        for p in reports:
            name = _unique(p.name, used)
            ctx.keep(p, name)
            secs = R.split_reports(p.read_text(errors="replace"))
            if len(secs) > 1:   # combined .rep: keep the individual reports too
                for s in secs:
                    sn = _unique(f"{p.stem}.{s.kind}.rpt", used)
                    (ctx.run_dir / sn).write_text(s.text)
                    ctx.artifacts[sn] = sn
        for lp in logs:
            if lp.parent != ctx.run_dir:
                ctx.keep(lp, "synth.log")
        for n in ("synth.log", "synth.stdout.log"):
            if (ctx.run_dir / n).is_file():
                ctx.artifacts[n] = n
        if flow.mode == "wrap" and flow.script and (ctx.workdir / flow.script).is_file():
            ctx.keep(ctx.workdir / flow.script, "synth_script.tcl")
        net_name = None
        if netlist is not None and netlist.is_file():
            net_name = ctx.keep(netlist, "netlist.v")
        fatal = [f for lp in logs for f in R.fatal_problems(lp.read_text(errors="replace"))]
        if fatal:
            raise RuntimeError("DC did not produce a usable mapped netlist: " + "; ".join(fatal)
                               + " (see synth.log)")

        corner = ctx.plan.values.get("corner")
        res = R.parse_texts([p.read_text(errors="replace") for p in reports],
                            [p.read_text(errors="replace") for p in logs],
                            top=self.cfg.project.top, tool="dc", vt_suffix=self.cfg.library.vt_suffix,
                            period_ps=ctx.plan.period_ps,
                            time_unit=flow.env.get(TIME_UNIT_ENV) or os.environ.get(TIME_UNIT_ENV),
                            corner=str(corner) if corner is not None else None)
        res.netlist = net_name
        if rc != 0:
            res.warnings.insert(0, Msg(severity=Severity.error, code="EXIT",
                                       text=f"`{cmd}` exited with code {rc}"))
        if net_name is None and netlist is not None:
            res.warnings.append(Msg(severity=Severity.warning, code="NETLIST",
                                    text=f"netlist {netlist.name} was not produced"))
        return res


# ---------------------------------------------------------------- helpers
def _expand(cwd: Path, t: str) -> list[Path]:
    p = Path(t) if Path(t).is_absolute() else cwd / t
    if any(c in t for c in "*?["):
        return [Path(x) for x in sorted(_glob.glob(str(p))) if Path(x).is_file()]
    return [p] if p.is_file() else []


def _unique(name: str, used: set[str]) -> str:
    base, n = name, 1
    while name in used:
        stem, dot, ext = base.partition(".")
        name = f"{stem}_{n}{dot}{ext}"
        n += 1
    used.add(name)
    return name


def _in_workdir(p: Path, root: Path, workdir: Path) -> Path:
    try:
        q = workdir / p.resolve().relative_to(root.resolve())
        return q if q.exists() else p
    except ValueError:
        return p


def _tcl(s: str) -> str:
    return "{" + s + "}" if any(c in s for c in " $[]\"\\;") else s


def _define(k: str, v: Any) -> str:
    return k if v is True or v == "" else f"{k}={v}"
