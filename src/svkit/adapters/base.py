"""Adapter interface (shared contract — docs/ARCHITECTURE.md §4).

An adapter knows how to RUN one tool and PARSE its outputs into a normalized
schema (svkit.schemas). Adapters never touch the Run DB or the UI; the core
runner hands them a RunContext and stores what they return.

Registration: built-in adapters are listed in `svkit.adapters.registry`;
third-party packages add `[project.entry-points."svkit.adapters"]` entries
(name = "module:Class").
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal, Optional

from svkit.config import Config, SimFlow, StaFlow, SynthFlow
from svkit.core.fsutil import copy_file
from svkit.schemas import SimResult, SynthResult, TimingPath

AdapterKind = Literal["sim", "synth", "sta"]


@dataclass
class KnobPlan:
    """The concrete edits a set of knob values implies (produced by svkit.core.knobs).

    Adapters apply the parts they understand:
      * env, files: applied by the core before `run()` (already done when run() is called)
      * tcl_prelude: TCL lines to execute BEFORE sourcing the user's script (tcl_var knobs)
      * tcl_append:  TCL lines to run after the script body / before `anchor` (see tcl_append_before)
      * tcl_replace: (pattern, replacement) pairs applied to a COPY of the user's script in the work dir
      * defines:     Verilog defines for simulation
      * values:      the raw knob values (e.g. clock_period in ps for builtin flows / generated SDC)
    """
    values: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)        # relpath -> content
    tcl_prelude: list[str] = field(default_factory=list)
    tcl_append: list[tuple[Optional[str], str]] = field(default_factory=list)   # (before-anchor regex | None, text)
    tcl_replace: list[tuple[str, str]] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)

    @property
    def period_ps(self) -> Optional[float]:
        v = self.values.get("clock_period")
        return float(v) if v is not None else None


@dataclass
class RunContext:
    cfg: Config
    root: Path                     # user's project root (READ-ONLY for synth runs)
    workdir: Path                  # where the tool runs: project root, or an isolated copy
    run_dir: Path                  # .svkit/runs/<id>/ — copy artifacts here
    run_id: str
    flow_name: str                 # key under [flows.<kind>.<name>]
    plan: KnobPlan = field(default_factory=KnobPlan)
    env: dict[str, str] = field(default_factory=dict)   # full environment for subprocesses
    log: Callable[[str], None] = lambda line: None      # live log line sink (-> WebSocket)
    cancel: threading.Event = field(default_factory=threading.Event)
    artifacts: dict[str, str] = field(default_factory=dict)   # filled by keep()
    cache_dir: Optional[Path] = None   # svkit-owned build cache (.svkit/build); adapters may write here

    def keep(self, src: Path | str, name: str | None = None) -> str:
        """Copy an output file into run_dir and register it as an artifact. Returns artifact name."""
        src = Path(src)
        if not src.is_absolute():
            src = self.workdir / src
        name = name or src.name
        dst = self.run_dir / name
        if src.resolve() != dst.resolve():
            copy_file(src, dst)
        self.artifacts[name] = name
        return name

    def sh(self, cmd: str | list[str], *, cwd: Path | None = None, timeout: int | None = None,
           log_file: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
        """Run a command, streaming output to `log` and (optionally) a log file. Honours cancel."""
        env = {**os.environ, **self.env, **(extra_env or {})}
        shell = isinstance(cmd, str)
        proc = subprocess.Popen(cmd, shell=shell, cwd=cwd or self.workdir, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", start_new_session=True,
                                preexec_fn=lambda: os.nice(5))
        with _LIVE_LOCK:
            _LIVE.add(proc.pid)
        fh = open(log_file, "a") if log_file else None
        timer = None
        if timeout:
            timer = threading.Timer(timeout, lambda: _kill(proc))
            timer.start()
        watcher = threading.Thread(target=lambda: (self.cancel.wait(), _kill(proc)), daemon=True)
        watcher.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if fh:
                    fh.write(line)
                self.log(line.rstrip("\n"))
            return proc.wait()
        finally:
            if timer:
                timer.cancel()
            if fh:
                fh.close()
            if proc.poll() is None:
                _kill(proc)
            with _LIVE_LOCK:
                _LIVE.discard(proc.pid)


# process groups of tools started by RunContext.sh() that are still running
_LIVE: set[int] = set()
_LIVE_LOCK = threading.Lock()


def kill_children() -> int:
    """SIGTERM every tool process group still running. Each tool runs in its own session, so a
    Ctrl-C or SIGTERM aimed at svkit never reaches it (a dc_shell would keep its license).
    Returns how many groups were signalled."""
    with _LIVE_LOCK:
        pgids = list(_LIVE)
    n = 0
    for pg in pgids:
        try:
            os.killpg(pg, signal.SIGTERM)
            n += 1
        except ProcessLookupError:
            pass
    return n


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


class Adapter(ABC):
    name: ClassVar[str]                 # registry key, e.g. "verilator"
    kind: ClassVar[AdapterKind]
    tools: ClassVar[tuple[str, ...]] = ()   # executables needed (checked by available())

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        return all(shutil.which(self.cfg.tool(t)) for t in self.tools)


class SimAdapter(Adapter):
    kind: ClassVar[AdapterKind] = "sim"

    @abstractmethod
    def run(self, ctx: RunContext, flow: SimFlow, test: str) -> SimResult:
        """Build (if needed) and run one test in ctx.workdir. Must keep() the waves and log,
        and evaluate pass/fail (golden/pass_regex/fail_regex — see svkit.core.golden)."""


class SynthAdapter(Adapter):
    kind: ClassVar[AdapterKind] = "synth"

    @abstractmethod
    def run(self, ctx: RunContext, flow: SynthFlow) -> SynthResult:
        """Run synthesis (+ timing) in ctx.workdir with ctx.plan applied; keep() reports,
        netlist and log; return the parsed result."""

    @abstractmethod
    def parse(self, files: list[Path], *, top: str) -> SynthResult:
        """Parse existing report files (no tool run). Must work offline on golden fixtures."""


class StaAdapter(Adapter):
    kind: ClassVar[AdapterKind] = "sta"

    @abstractmethod
    def run(self, ctx: RunContext, flow: StaFlow, *, netlist: Path, top: str,
            liberty: list[Path], period_ps: float | None) -> SynthResult:
        """Run STA on a gate netlist; returns a SynthResult with timing fields filled
        (wns/tns/hold_wns/paths/period) that the synth adapter merges with its own."""

    @abstractmethod
    def parse_timing(self, text: str) -> list[TimingPath]:
        """Parse a timing report (report_checks / report_timing) into paths."""


class AdapterNotFound(KeyError):
    pass
