"""Verilator simulation adapter (open-source flow).

Modes
  builtin : compile `[project].sources + tb_sources` with Verilator 5
            (`--binary --timing --trace`), top = `[project].tb_top`, then run the binary once per
            test with `+TEST=<name>` (env `SVKIT_TEST` too). The executable is built once per
            content hash under `<root>/.svkit/build/verilator-<hash>/` and shared by all tests.
  wrap    : run the user's own command (`flow.cmd`, e.g. `make sim-verilator TEST={test}`).

Both modes then keep the waveform / log / golden output, decide pass/fail with
`svkit.core.golden.evaluate` and read perf counters through `[perf].source`.
The post-processing lives in `finish_sim()` so other sim adapters can reuse it.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from svkit.adapters.base import RunContext, SimAdapter
from svkit.adapters.perf.regex import parse_perf
from svkit.config import Config, SimFlow
from svkit.core import golden as G
from svkit.core import project as P
from svkit.core.fsutil import copy_file
from svkit.schemas import Msg, Severity, SimResult

_BUILD_LOCKS: dict[str, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()

_WARN_RE = re.compile(r"^%(Warning|Error)(?:-([A-Z0-9_]+))?:\s*(?:([^:\s]+):(\d+):(?:\d+:)?)?\s*(.*)$")
MAX_WARNINGS = 200


# ---------------------------------------------------------------------------- shared helpers
def order_sources(files: list[Path]) -> list[Path]:
    """Stable-sort so files that declare a `package` (and `interface`-only files) come first:
    single-pass front ends need packages defined before use."""
    def rank(f: Path) -> int:
        try:
            txt = f.read_text(errors="replace")
        except OSError:
            return 1
        return 0 if re.search(r"^\s*package\s+\w+", txt, re.MULTILINE) else 1
    return sorted(files, key=rank)


def parse_tool_messages(text: str, limit: int = MAX_WARNINGS) -> list[Msg]:
    """Verilator-style `%Warning-CODE: file:line:col: text` / `%Error: ...` lines -> Msg."""
    out: list[Msg] = []
    seen: set[tuple] = set()
    for line in text.splitlines():
        m = _WARN_RE.match(line.strip())
        if not m:
            continue
        kind, code, file, ln, msg = m.groups()
        key = (kind, code, file, ln, msg)
        if key in seen:
            continue
        seen.add(key)
        out.append(Msg(severity=Severity.error if kind == "Error" else Severity.warning,
                       code=code, text=msg.strip(), file=file, line=int(ln) if ln else None))
        if len(out) >= limit:
            break
    return out


def vcd_end_time(path: Path) -> Optional[int]:
    """Last `#<time>` in a VCD (reads only the tail)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode(errors="replace")
    except OSError:
        return None
    ts = re.findall(r"^#(\d+)", tail, re.MULTILINE)
    return int(ts[-1]) if ts else None


def _fresh(p: Path, t0: float) -> bool:
    try:
        return p.is_file() and p.stat().st_mtime >= t0 - 1.0
    except OSError:
        return False


def newest_source_mtime(cfg) -> float:
    """mtime of the newest design / testbench source or header."""
    files = P.resolve_sources(cfg) + P.resolve_sources(cfg, cfg.project.tb_sources)
    files += [h for d in P.include_dirs(cfg) if d.is_dir() for h in d.iterdir() if h.suffix in (".svh", ".vh")]
    return max((f.stat().st_mtime for f in files if f.is_file()), default=0.0)


def output_current(ctx: RunContext, flow: SimFlow, p: Path, t0: float, warnings: list[Msg]) -> bool:
    """Written by this run, or (wrap mode) left alone by a build that judged it up to date: still
    newer than every source, like make's own rule. Older files are stale leftovers."""
    if _fresh(p, t0):
        return True
    if flow.mode != "wrap" or not p.is_file() or p.stat().st_mtime < newest_source_mtime(ctx.cfg):
        return False
    warnings.append(Msg(severity=Severity.info, code="UPTODATE",
                        text=f"{p.name} was not rewritten (the build found it up to date); using it"))
    return True


def _abs(base: Path, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else base / p


def prepare_outputs(ctx: RunContext, flow: SimFlow, test: str, cwd: Path) -> None:
    """Make sure the directories the testbench writes into exist (output/, ...)."""
    for tmpl in [flow.waves, flow.log, flow.golden.actual if flow.golden else None]:
        if tmpl:
            _abs(cwd, P.tmpl(tmpl, ctx.cfg, test=test, workdir=ctx.workdir, run_dir=ctx.run_dir)).parent.mkdir(
                parents=True, exist_ok=True)
    src = ctx.cfg.perf.source
    if src:
        _abs(cwd, P.tmpl(src.regex_file, ctx.cfg, test=test, workdir=ctx.workdir)).parent.mkdir(
            parents=True, exist_ok=True)


def finish_sim(ctx: RunContext, flow: SimFlow, test: str, *, tool: str, cwd: Path, exit_code: int,
               t0: float, log_text: str, warnings: list[Msg] | None = None) -> SimResult:
    """Shared post-processing for sim adapters: keep artifacts, evaluate pass/fail, perf."""
    cfg = ctx.cfg
    T = lambda s: P.tmpl(s, cfg, test=test, workdir=ctx.workdir, run_dir=ctx.run_dir)
    warnings = list(warnings or [])
    res = SimResult(tool=tool, test=test, warnings=warnings)

    # waveform
    wave: Optional[Path] = _abs(cwd, T(flow.waves)) if flow.waves else None
    if wave is None:   # no template: newest vcd/fst written by this run in cwd
        cands = [p for ext in ("*.vcd", "*.fst") for p in cwd.glob(ext) if _fresh(p, t0)]
        wave = max(cands, key=lambda p: p.stat().st_mtime) if cands else None
    if wave is not None and output_current(ctx, flow, wave, t0, warnings):
        lim = cfg.waves.max_size_mb
        if lim and wave.stat().st_size > lim * 1024 * 1024:
            warnings.append(Msg(text=f"waveform {wave} larger than {lim} MB, not archived"))
        else:
            suffix = ".fst" if wave.suffix == ".fst" else ".vcd"
            res.waves = ctx.keep(wave, "waves" + suffix)
            if suffix == ".vcd":
                res.sim_time = vcd_end_time(wave)
    elif flow.waves:
        warnings.append(Msg(severity=Severity.info, text=f"no waveform produced at {T(flow.waves)}"))

    # user log file, if the flow names one
    if flow.log:
        lf = _abs(cwd, T(flow.log))
        if _fresh(lf, t0):
            ctx.keep(lf, "tool-" + lf.name)
            log_text += "\n" + lf.read_text(errors="replace")

    # golden
    actual = expected = None
    if flow.golden:
        actual = _abs(cwd, T(flow.golden.actual))
        expected = _abs(cfg.root, T(flow.golden.expected))
        if actual.is_file() and output_current(ctx, flow, actual, t0, warnings):
            ctx.keep(actual, actual.name)
        elif actual.exists():   # stale file from an earlier run: treat as not produced
            actual = actual.with_name(actual.name + ".svkit-missing")
        if expected.is_file():
            ctx.keep(expected, "expected-" + expected.name)
    passed, reason, div = G.evaluate(exit_code=exit_code, log_text=log_text, pass_regex=flow.pass_regex,
                                     fail_regex=flow.fail_regex, actual=actual, expected=expected,
                                     golden=flow.golden)
    res.passed, res.reason, res.divergence = passed, reason, div

    # perf
    perf = parse_perf(cfg, test, cwd)
    if perf is not None:
        res.perf = perf
        src = cfg.perf.source
        pf = _abs(cwd, P.tmpl(src.regex_file, cfg, test=test, workdir=ctx.workdir))
        if pf.is_file() and (actual is None or pf.resolve() != actual.resolve()):
            ctx.keep(pf, pf.name)
    return res


def fixed_env(tool: str) -> dict[str, str]:
    """Verilator honours $VERILATOR_ROOT; a stale value (pointing at a removed install) breaks
    every compile. If it is set but unusable, ask the executable for its own root."""
    root = os.environ.get("VERILATOR_ROOT")
    if not root or (Path(root) / "include" / "verilated.h").is_file():
        return {}
    env = {k: v for k, v in os.environ.items() if k != "VERILATOR_ROOT"}
    try:
        out = subprocess.run([tool, "--getenv", "VERILATOR_ROOT"], env=env, capture_output=True, text=True,
                             timeout=30)
        good = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired, IndexError):
        good = ""
    return {"VERILATOR_ROOT": good} if good and Path(good).is_dir() else {}


# ---------------------------------------------------------------------------- adapter
class VerilatorAdapter(SimAdapter):
    name = "verilator"
    tools = ("verilator",)

    def run(self, ctx: RunContext, flow: SimFlow, test: str) -> SimResult:
        cfg = ctx.cfg
        exe = cfg.tool("verilator")
        env_fix = fixed_env(exe)
        cwd = (ctx.workdir / flow.cwd).resolve()
        prepare_outputs(ctx, flow, test, cwd)
        sim_log = ctx.run_dir / "sim.log"
        warnings: list[Msg] = []
        extra = {"SVKIT_TEST": test, **flow.env, **env_fix}

        if flow.mode == "wrap":
            cmd = P.tmpl(flow.cmd, cfg, test=test, workdir=ctx.workdir, run_dir=ctx.run_dir)
            ctx.log(f"[svkit] {cmd}  (cwd {cwd})")
            t0 = time.time()
            rc = ctx.sh(cmd, cwd=cwd, timeout=flow.timeout_s, log_file=sim_log, extra_env=extra)
            ctx.artifacts["sim.log"] = "sim.log"
            log_text = sim_log.read_text(errors="replace") if sim_log.exists() else ""
            warnings = parse_tool_messages(log_text)
            return finish_sim(ctx, flow, test, tool="verilator", cwd=cwd, exit_code=rc, t0=t0,
                              log_text=log_text, warnings=warnings)

        # ---- builtin: build (cached) then run
        binary, build_log, ok = self._build(ctx, flow, exe, extra)
        if build_log.is_file():
            ctx.keep(build_log, "build.log")
            warnings = parse_tool_messages(build_log.read_text(errors="replace"))
        if not ok:
            return SimResult(tool="verilator", test=test, passed=False,
                             reason="verilator compile failed (see build.log)", warnings=warnings)
        args = [str(binary), f"+TEST={test}"]
        ctx.log(f"[svkit] {' '.join(args)}  (cwd {cwd})")
        t0 = time.time()
        rc = ctx.sh(args, cwd=cwd, timeout=flow.timeout_s, log_file=sim_log, extra_env=extra)
        ctx.artifacts["sim.log"] = "sim.log"
        log_text = sim_log.read_text(errors="replace") if sim_log.exists() else ""
        return finish_sim(ctx, flow, test, tool="verilator", cwd=cwd, exit_code=rc, t0=t0,
                          log_text=log_text, warnings=warnings)

    # ------------------------------------------------------------------ build
    def compile_args(self, cfg: Config, flow: SimFlow, defines: dict[str, str]) -> tuple[list[str], list[Path]]:
        srcs = P.resolve_sources(cfg) + [f for f in P.resolve_sources(cfg, cfg.project.tb_sources)]
        seen, files = set(), []
        for f in srcs:
            if f not in seen:
                seen.add(f)
                files.append(f)
        files = order_sources(files)
        top = cfg.project.tb_top or cfg.project.top
        fst = bool(flow.waves and flow.waves.endswith(".fst"))
        args = ["--binary", "--timing", "-Wno-fatal", "--trace-fst" if fst else "--trace",
                "--top-module", top]
        for d in P.include_dirs(cfg):
            args.append(f"-I{d}")
        for k, v in {**{k: _defval(v) for k, v in cfg.project.defines.items()}, **defines}.items():
            if v is None:
                continue
            args.append(f"-D{k}" if v == "" else f"-D{k}={v}")
        args += list(flow.args)
        return args, files

    def _build(self, ctx: RunContext, flow: SimFlow, exe: str, extra_env: dict[str, str]) -> tuple[Path, Path, bool]:
        cfg = ctx.cfg
        args, files = self.compile_args(cfg, flow, ctx.plan.defines)
        h = hashlib.sha256()
        h.update(str(shutil.which(exe) or exe).encode())
        h.update("\0".join(args).encode())
        for f in files:
            h.update(str(f).encode())
            try:
                h.update(f.read_bytes())
            except OSError:
                h.update(b"<missing>")
        for d in P.include_dirs(cfg):
            if d.is_dir():
                for p in sorted(d.iterdir()):
                    if p.is_file() and p.suffix in (".svh", ".vh", ".sv", ".v"):
                        h.update(p.read_bytes())
        key = h.hexdigest()[:16]
        bdir = (ctx.cache_dir or cfg.root / ".svkit" / "build") / f"verilator-{key}"
        binary, blog = bdir / "Vsim", bdir / "build.log"
        with _BUILD_LOCKS_GUARD:
            lock = _BUILD_LOCKS.setdefault(key, threading.Lock())
        with lock:
            bdir.parent.mkdir(parents=True, exist_ok=True)
            with open(bdir.parent / f"verilator-{key}.lock", "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)      # also serialises separate svkit processes
                try:
                    if binary.is_file() and os.access(binary, os.X_OK):
                        ctx.log(f"[svkit] reusing Verilator build {bdir.name}")
                        return binary, blog, True
                    tmp = bdir.with_name(bdir.name + f".tmp{os.getpid()}")
                    shutil.rmtree(tmp, ignore_errors=True)
                    tmp.mkdir(parents=True)
                    jobs = max(1, min(4, (os.cpu_count() or 2) // 2))
                    cmd = [exe, *args, "--Mdir", str(tmp / "obj"), "-o", str(tmp / "Vsim"), "-j", str(jobs),
                           *[_rel(f, cfg.root) for f in files]]
                    ctx.log("[svkit] " + " ".join(cmd))
                    rc = ctx.sh(cmd, cwd=cfg.root, timeout=flow.timeout_s, log_file=tmp / "build.log",
                                extra_env=extra_env)
                    if rc != 0 or not (tmp / "Vsim").is_file():
                        # keep the log for the user, drop the half-built tree
                        copy_file(tmp / "build.log", ctx.run_dir / "build.log")
                        shutil.rmtree(tmp, ignore_errors=True)
                        return binary, ctx.run_dir / "build.log", False
                    shutil.rmtree(tmp / "obj", ignore_errors=True)   # only the executable is needed
                    shutil.rmtree(bdir, ignore_errors=True)
                    tmp.rename(bdir)
                    return binary, blog, True
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)


def _rel(f: Path, root: Path) -> str:
    """Project-relative path when possible (keeps tool messages short and portable)."""
    try:
        return str(f.relative_to(root.resolve()))
    except ValueError:
        return str(f)


def _defval(v) -> Optional[str]:
    if v is True:
        return ""
    if v is False:
        return None
    return str(v)
