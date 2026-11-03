"""Synopsys VCS simulation adapter ("vcs").

wrap mode (preferred)
    Runs `flow.cmd` (e.g. `make sim-vcs TEST={test}` or `make {test}.out`) and reads what the
    user's build writes: `flow.log`, `flow.waves` (.vcd / .fst kept as is, .vpd converted with
    `vcs -vpd2vcd +splitpacked`, .fsdb converted with `fsdb2vcd` when Verdi is present) and the
    golden `actual` file. The test name is exported as `SVKIT_TEST`; sim knobs of the `define`
    kind are exported as `SVKIT_VCS_DEFINES` ("+define+A=1 +define+B") for builds that want them.

builtin mode
    Compiles `[project]` sources + testbench once per source hash into
    `.svkit/build/vcs-<hash>/simv` (thread- and process-safe), then runs
    `simv +TEST=<test>` in the run's working directory. `flow.args` are compile options;
    arguments after a literal "--" are passed to simv.

Pass/fail always comes from svkit.core.golden.evaluate (identical across simulators); VCS
compile errors, $error/$fatal and assertion failures in the log also fail the test.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from svkit.adapters.base import RunContext, SimAdapter
from svkit.adapters.synopsys import synopsys_seat
from svkit.adapters.waves import vpd as V
from svkit.config import SimFlow
from svkit.core import golden as G
from svkit.core import project as P
from svkit.schemas import Msg, Severity, SimResult

BASE_COMPILE_ARGS = ["-sverilog", "-full64", "-timescale=1ns/1ps", "-debug_access+all", "+vcs+lic+wait"]
_BUILD_LOCKS: dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()
_TIME_PS = {"fs": 1e-3, "ps": 1.0, "ns": 1e3, "us": 1e6, "ms": 1e9, "s": 1e12}


# ================================================================ log parsing
_COMPILE_MSG = re.compile(r"^(Error|Warning|Lint)-\[([A-Za-z0-9_\-]+)\]\s*(.*)$")
_FILE_LINE = re.compile(r'^\s*"?([^",\s]+)"?\s*,\s*(\d+)\s*$')
_RUNTIME_MSG = re.compile(r'^(Error|Fatal|Warning):\s*"([^"]+)",\s*(\d+):\s*(.*)$')
_ASSERT_FAIL = re.compile(r'^"([^"]+)",\s*(\d+):\s*(\S+):\s*started at\s*(\S+)\s+failed at\s*(\S+)')
_SIM_TIME = re.compile(r"(?m)^\s*Time:\s*(\d+)\s*(fs|ps|ns|us|ms|s)\b")


def parse_vcs_log(text: str) -> tuple[list[Msg], list[str]]:
    """VCS compile / runtime log -> (deduplicated messages, failure reasons)."""
    lines = text.splitlines()
    found: dict[tuple, dict] = {}
    failures: list[str] = []

    def add(sev: Severity, code: str, txt: str, file: Optional[str], line: Optional[int]) -> None:
        k = (sev, code)
        if k in found:
            found[k]["n"] += 1
            return
        found[k] = {"sev": sev, "code": code, "text": txt, "file": file, "line": line, "n": 1}

    for i, ln in enumerate(lines):
        if (m := _COMPILE_MSG.match(ln)):
            kind, code, title = m.groups()
            file = line_no = None
            detail = ""
            for nxt in lines[i + 1:i + 4]:
                if not nxt.strip():
                    if file or detail:
                        break
                    continue
                if file is None and (fm := _FILE_LINE.match(nxt)):
                    file, line_no = fm.group(1), int(fm.group(2))
                    continue
                if _COMPILE_MSG.match(nxt) or _RUNTIME_MSG.match(nxt):
                    break
                if not detail:
                    detail = nxt.strip()
            txt = title.strip() + (f": {detail}" if detail else "")
            sev = Severity.error if kind == "Error" else Severity.warning
            add(sev, code, txt, file, line_no)
            if kind == "Error":
                failures.append(f"VCS Error-[{code}] {title.strip()}" + (f" at {file}:{line_no}" if file else ""))
        elif (m := _RUNTIME_MSG.match(ln)):
            kind, file, line_no, where = m.groups()
            detail = next((x.strip() for x in lines[i + 1:i + 3] if x.strip()), "")
            if _RUNTIME_MSG.match(detail) or _COMPILE_MSG.match(detail):
                detail = ""
            txt = f"{where.strip()}" + (f": {detail}" if detail else "")
            sev = Severity.warning if kind == "Warning" else Severity.error
            code = {"Error": "$error", "Fatal": "$fatal", "Warning": "$warning"}[kind]
            add(sev, f"{code}@{file}:{line_no}", txt, file, int(line_no))
            if kind != "Warning":
                failures.append(f"{code} at {file}:{line_no}" + (f": {detail}" if detail else ""))
        elif (m := _ASSERT_FAIL.match(ln)):
            file, line_no, name, t0, t1 = m.groups()
            add(Severity.error, f"assert@{name}", f"assertion {name} failed at {t1} (started {t0})",
                file, int(line_no))
            failures.append(f"assertion {name} failed at {t1} ({file}:{line_no})")
    msgs = []
    for v in found.values():
        code = v["code"].split("@", 1)[0]
        msgs.append(Msg(severity=v["sev"], code=code,
                        text=v["text"] + (f" (x{v['n']})" if v["n"] > 1 else ""), file=v["file"], line=v["line"]))
    msgs.sort(key=lambda m: 0 if m.severity == Severity.error else 1)
    return msgs, failures


def final_time_ps(text: str) -> Optional[float]:
    """Final simulation time from the VCS report footer ("Time: 1234500 ps")."""
    ms = list(_SIM_TIME.finditer(text))
    if not ms:
        return None
    m = ms[-1]
    return int(m.group(1)) * _TIME_PS[m.group(2)]


def vcd_timescale_ps(path: Path) -> Optional[float]:
    try:
        with open(path, "r", errors="replace") as fh:
            head = fh.read(65536)
    except OSError:
        return None
    m = re.search(r"\$timescale\s+(\d+)\s*(fs|ps|ns|us|ms|s)\s+\$end", head)
    return int(m.group(1)) * _TIME_PS[m.group(2)] if m else None


# ================================================================ adapter
class VcsAdapter(SimAdapter):
    name = "vcs"
    tools = ("vcs",)

    def run(self, ctx: RunContext, flow: SimFlow, test: str) -> SimResult:
        cwd = ctx.workdir / flow.cwd
        res = SimResult(tool="vcs", test=test)
        t0 = time.time()
        with synopsys_seat(ctx.cancel, ctx.log):
            if flow.mode == "builtin":
                rc, stdout_log, compile_fail = self._run_builtin(ctx, flow, test, cwd, res)
            else:
                rc, stdout_log = self._run_wrap(ctx, flow, test, cwd)
                compile_fail = None
            log_texts = []
            if stdout_log.is_file():
                log_texts.append(stdout_log.read_text(errors="replace"))
                ctx.artifacts[stdout_log.name] = stdout_log.name
            user_log = self._path(cwd, flow.log, ctx, test)
            if user_log is not None and _fresh(user_log, t0):
                ctx.keep(user_log, "sim.log")
                log_texts.append(user_log.read_text(errors="replace"))
            elif user_log is not None and flow.mode == "wrap":
                res.warnings.append(Msg(severity=Severity.warning, code="LOG",
                                        text=f"sim log {flow.log} was not produced by this run"))
            # waves (conversion may need vcs, so it stays inside the seat)
            if compile_fail is None:
                self._waves(ctx, flow, test, cwd, t0, res)
        log_text = "\n".join(log_texts)
        msgs, failures = parse_vcs_log(log_text)
        if flow.mode == "builtin" and compile_fail is None and (ctx.run_dir / "compile.log").is_file():
            msgs += parse_vcs_log((ctx.run_dir / "compile.log").read_text(errors="replace"))[0]
        res.warnings = msgs + res.warnings

        if compile_fail is not None:
            res.passed, res.reason = False, compile_fail
            return res

        # ---- pass / fail (shared rules) + VCS-specific failures
        actual = expected = None
        if flow.golden is not None:
            actual = self._path(cwd, flow.golden.actual, ctx, test)
            expected = Path(P.tmpl(flow.golden.expected, self.cfg, test=test))
            if not expected.is_absolute():
                expected = self.cfg.root / expected
        if actual is not None and actual.exists() and not _fresh(actual, t0):
            res.passed, res.reason = False, f"expected output {actual} was not produced (stale file from an earlier run)"
        else:
            passed, reason, div = G.evaluate(exit_code=rc, log_text=log_text, pass_regex=flow.pass_regex,
                                             fail_regex=flow.fail_regex, actual=actual, expected=expected,
                                             golden=flow.golden)
            res.passed, res.reason, res.divergence = passed, reason, div
            if failures and res.passed is not False:
                res.passed, res.reason = False, failures[0]
        if actual is not None and actual.is_file() and _fresh(actual, t0):
            ctx.keep(actual, actual.name if actual.name not in ctx.artifacts else f"actual.{actual.name}")

        # ---- sim time in wave ticks
        tps = final_time_ps(log_text)
        if tps is not None and res.waves == "waves.vcd":
            ts = vcd_timescale_ps(ctx.run_dir / "waves.vcd")
            if ts:
                res.sim_time = int(round(tps / ts))

        # ---- perf (Track 1's regex perf source, if present)
        if self.cfg.perf.source is not None:
            try:
                from svkit.adapters.perf.regex import parse_perf  # type: ignore
            except ImportError:
                parse_perf = None
            if parse_perf is not None:
                try:
                    res.perf = parse_perf(self.cfg, test, cwd)
                except Exception as e:  # perf is optional; never fail the sim on it
                    res.warnings.append(Msg(severity=Severity.warning, code="PERF", text=f"perf parse failed: {e}"))
        return res

    # ------------------------------------------------------------ helpers
    def _path(self, cwd: Path, tmpl: Optional[str], ctx: RunContext, test: str) -> Optional[Path]:
        if not tmpl:
            return None
        p = Path(P.tmpl(tmpl, self.cfg, test=test, workdir=ctx.workdir, run_dir=ctx.run_dir))
        return p if p.is_absolute() else cwd / p

    def _env(self, ctx: RunContext, flow: SimFlow, test: str) -> dict[str, str]:
        env = dict(flow.env)
        env["SVKIT_TEST"] = test
        if ctx.plan.defines:
            env["SVKIT_VCS_DEFINES"] = " ".join(_define_arg(k, v) for k, v in ctx.plan.defines.items())
        return env

    # ------------------------------------------------------------ wrap
    def _run_wrap(self, ctx: RunContext, flow: SimFlow, test: str, cwd: Path) -> tuple[int, Path]:
        cmd = P.tmpl(flow.cmd or "", self.cfg, test=test, workdir=ctx.workdir, run_dir=ctx.run_dir)
        out = ctx.run_dir / ("sim.stdout.log" if flow.log else "sim.log")
        ctx.log(f"[svkit] vcs (wrap): {cmd}  (in {cwd})")
        rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=out, extra_env=self._env(ctx, flow, test))
        return rc, out

    # ------------------------------------------------------------ builtin
    def compile_args(self, ctx: RunContext, flow: SimFlow) -> tuple[list[str], list[str]]:
        """(compile argv without the vcs executable, simv runtime args)."""
        cfg, proj = self.cfg, self.cfg.project
        comp_extra, run_extra = _split_args(flow.args)
        defines: dict[str, Any] = dict(proj.defines)
        if proj.sources.filelist:
            defines.update(P.parse_filelist(cfg.root / proj.sources.filelist)[2])
        defines.update(ctx.plan.defines)
        srcs = P.resolve_sources(cfg) + P.resolve_sources(cfg, proj.tb_sources)
        srcs = list(dict.fromkeys(srcs))
        if not srcs:
            from svkit.config import ConfigError
            raise ConfigError("builtin VCS flow: [project].sources / tb_sources matched no files")
        args = [*BASE_COMPILE_ARGS, "-top", proj.tb_top or proj.top]
        args += [f"+incdir+{d}" for d in P.include_dirs(cfg)]
        args += [_define_arg(k, v) for k, v in defines.items() if v is not False]
        args += [str(s) for s in srcs]
        args += comp_extra
        return args, run_extra

    def _build(self, ctx: RunContext, flow: SimFlow) -> tuple[Optional[Path], Path, Optional[str]]:
        """Compile once per (sources, args, defines). Returns (simv or None, compile log, failure)."""
        args, _ = self.compile_args(ctx, flow)
        vcs = self.cfg.tool("vcs")
        key = hashlib.sha256(json.dumps({"src": P.source_hash(self.cfg), "args": args, "vcs": vcs},
                                        sort_keys=True).encode()).hexdigest()[:16]
        build_root = ctx.run_dir.parent.parent / "build"     # .svkit/build (runs live in .svkit/runs/<id>)
        build_root.mkdir(parents=True, exist_ok=True)
        bdir = build_root / f"vcs-{key}"
        with _BUILD_LOCKS_GUARD:
            tlock = _BUILD_LOCKS.setdefault(key, threading.Lock())
        with tlock, open(build_root / f"vcs-{key}.lock", "a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                if (bdir / "simv").is_file():
                    ctx.log(f"[svkit] vcs: reusing build {bdir.name}")
                    return bdir / "simv", bdir / "compile.log", None
                tmp = build_root / f"vcs-{key}.tmp{os.getpid()}"
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir()
                cmd = [vcs, *args, "-o", "simv", "-Mdir=csrc", "-l", "compile.log"]
                ctx.log("[svkit] vcs (builtin compile): " + " ".join(cmd))
                rc = ctx.sh(cmd, cwd=tmp, timeout=flow.timeout_s, extra_env=dict(flow.env))
                clog = tmp / "compile.log"
                if rc != 0 or not (tmp / "simv").is_file():
                    failed = ctx.run_dir / "compile.log"
                    if clog.is_file():
                        shutil.copy2(clog, failed)
                    else:
                        failed.write_text("")
                    shutil.rmtree(tmp, ignore_errors=True)
                    return None, failed, f"VCS compile failed (exit {rc})"
                shutil.rmtree(bdir, ignore_errors=True)
                os.replace(tmp, bdir)
                return bdir / "simv", bdir / "compile.log", None
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _run_builtin(self, ctx: RunContext, flow: SimFlow, test: str, cwd: Path,
                     res: SimResult) -> tuple[int, Path, Optional[str]]:
        simv, clog, fail = self._build(ctx, flow)
        if clog.is_file():
            ctx.keep(clog, "compile.log")
        out = ctx.run_dir / "sim.log"
        if simv is None:
            # surface compile messages through the same log parsing
            out.write_text(clog.read_text(errors="replace") if clog.is_file() else "")
            return 1, out, fail
        _, run_extra = self.compile_args(ctx, flow)
        cmd = [str(simv), f"+TEST={test}", "+vcs+lic+wait", *run_extra]
        ctx.log("[svkit] vcs (builtin run): " + " ".join(cmd))
        rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=out, extra_env=self._env(ctx, flow, test))
        return rc, out, None

    # ------------------------------------------------------------ waves
    def _waves(self, ctx: RunContext, flow: SimFlow, test: str, cwd: Path, t0: float, res: SimResult) -> None:
        src = self._path(cwd, flow.waves, ctx, test)
        if src is None and flow.mode == "builtin":
            cands = [p for ext in ("*.vcd", "*.fst", "*.vpd", "*.fsdb") for p in cwd.glob(ext) if _fresh(p, t0)]
            src = max(cands, key=lambda p: p.stat().st_mtime) if cands else None
        if src is None:
            return
        if not src.is_file() or not _fresh(src, t0):
            res.warnings.append(Msg(severity=Severity.warning, code="WAVES",
                                    text=f"waveform {src.name} was not produced by this run"))
            return
        limit = self.cfg.waves.max_size_mb
        if limit and src.stat().st_size > limit * 1024 * 1024 and src.suffix.lower() in (".vcd", ".fst"):
            name = f"waves{src.suffix.lower()}"
            if ctx.workdir == self.cfg.root:      # in-place run: the file survives, link to it
                (ctx.run_dir / name).unlink(missing_ok=True)
                os.symlink(src.resolve(), ctx.run_dir / name)
                ctx.artifacts[name] = name
                res.waves = name
            res.warnings.append(Msg(severity=Severity.info, code="WAVES",
                                    text=f"{src.name} exceeds [waves].max_size_mb={limit}; "
                                         + ("linked, not copied" if res.waves else "not archived")))
            return
        suf = src.suffix.lower()
        if suf in (".vcd", ".fst"):
            res.waves = ctx.keep(src, f"waves{suf}")
        elif suf in (".vpd", ".fsdb"):
            ok, msg = V.convert_to_vcd(ctx, src, ctx.run_dir / "waves.vcd")
            if ok:
                ctx.artifacts["waves.vcd"] = "waves.vcd"
                res.waves = "waves.vcd"
            else:
                ctx.log(f"[svkit] warning: {msg}")
                res.warnings.append(Msg(severity=Severity.warning, code="WAVES", text=msg))
        else:
            res.warnings.append(Msg(severity=Severity.warning, code="WAVES",
                                    text=f"unsupported wave format {src.suffix}"))


def _fresh(p: Path, t0: float) -> bool:
    """File exists and was written by this run (mtime granularity slack of 2 s)."""
    try:
        return p.stat().st_mtime >= t0 - 2.0
    except OSError:
        return False


def _define_arg(k: str, v: Any) -> str:
    return f"+define+{k}" if v is True or v == "" else f"+define+{k}={v}"


def _split_args(args: list[str]) -> tuple[list[str], list[str]]:
    if "--" in args:
        i = args.index("--")
        return list(args[:i]), list(args[i + 1:])
    return list(args), []
