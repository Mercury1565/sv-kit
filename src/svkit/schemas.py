"""Normalized result schemas — the ONLY data shapes the server/UI ever see.

Every adapter (Synopsys or open-source) normalizes its tool output into these
models. Units are fixed and documented per field so adapters convert once:

* time / delay / slack / period : picoseconds (float)
* area                          : the library's native area unit (usually um^2), float
* power                         : milliwatts (float)
* sim time in waveforms         : integer ticks of the waveform's timescale; the
                                  wave API always reports the timescale alongside.

This module is a shared contract (see docs/ARCHITECTURE.md §3). Track agents
must not change it; propose changes to the lead.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------- messages
class Severity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"


class Msg(_Model):
    severity: Severity = Severity.warning
    code: Optional[str] = None          # tool message id, e.g. "LINT-1", "%Warning-WIDTH"
    text: str
    file: Optional[str] = None
    line: Optional[int] = None


# ---------------------------------------------------------------- timing
class TimingPoint(_Model):
    """One row of a timing path (a pin, usually)."""
    inst: str                     # hierarchical instance name ('' for a top-level port)
    pin: str                      # pin or port name, e.g. "A", "Q", "clk"
    cell: Optional[str] = None    # library cell, e.g. "NAND2_X1_R"; None for ports/nets
    incr: float = 0.0             # ps added by this point
    arrival: float = 0.0          # ps cumulative arrival
    rise_fall: Optional[Literal["r", "f"]] = None
    net: Optional[str] = None


class TimingPath(_Model):
    startpoint: str
    endpoint: str
    path_group: Optional[str] = None
    path_type: Literal["max", "min"] = "max"   # max = setup, min = hold
    slack: float                               # ps; negative = violated
    required: Optional[float] = None           # ps
    arrival: Optional[float] = None            # ps (data arrival time)
    points: list[TimingPoint] = Field(default_factory=list)


# ---------------------------------------------------------------- synthesis
class Power(_Model):
    dynamic: Optional[float] = None   # mW (internal + switching)
    leakage: Optional[float] = None   # mW
    total: Optional[float] = None     # mW


class SynthResult(_Model):
    """Normalized synthesis + timing result (one synth Run)."""
    schema_version: int = SCHEMA_VERSION
    tool: str                                 # "dc", "yosys+opensta", ...
    top: str
    period: Optional[float] = None            # ps, the constrained clock period
    corner: Optional[str] = None
    wns: Optional[float] = None               # ps, worst setup slack (may be > 0)
    tns: Optional[float] = None               # ps, total negative setup slack (<= 0)
    hold_wns: Optional[float] = None          # ps, worst hold slack
    hold_tns: Optional[float] = None
    area_total: Optional[float] = None
    area_unit: str = "um^2"
    area_by_hier: dict[str, float] = Field(default_factory=dict)   # hier path -> area ('' = top)
    cell_count: Optional[int] = None
    cells_by_type: dict[str, int] = Field(default_factory=dict)    # lib cell -> count
    vt_mix: dict[str, int] = Field(default_factory=dict)           # "RVT" -> count (via [library].vt_suffix)
    power: Power = Field(default_factory=Power)
    paths: list[TimingPath] = Field(default_factory=list)           # top critical paths, worst first
    warnings: list[Msg] = Field(default_factory=list)
    netlist: Optional[str] = None                                   # artifact name of the netlist
    extra: dict[str, Any] = Field(default_factory=dict)             # tool-specific extras (shown raw)

    @property
    def met(self) -> bool:
        """Timing met: setup WNS >= 0 and no hold violation (None counts as met)."""
        return (self.wns is None or self.wns >= 0) and (self.hold_wns is None or self.hold_wns >= 0)


# ---------------------------------------------------------------- simulation
class SimResult(_Model):
    schema_version: int = SCHEMA_VERSION
    tool: str                                 # "vcs", "verilator"
    test: str
    passed: Optional[bool] = None             # None = no golden / undetermined
    reason: Optional[str] = None              # why it failed: "golden mismatch at line 12", "exit 1", ...
    sim_time: Optional[int] = None            # final sim time in waves ticks if known
    waves: Optional[str] = None               # artifact name of the waveform (vcd/fst)
    divergence: Optional["Divergence"] = None
    perf: Optional["PerfResult"] = None
    warnings: list[Msg] = Field(default_factory=list)


class Divergence(_Model):
    """First mismatch between actual and golden output (golden-diff jump), or, for tests without
    golden output (source = "log"), the first log line reporting a failed check."""
    line: int                       # 1-based line number in the actual output (or the sim log)
    expected: Optional[str] = None
    actual: Optional[str] = None
    time: Optional[int] = None      # sim time (waves ticks) if it could be mapped
    cycle: Optional[int] = None
    source: Optional[Literal["golden", "log"]] = None   # None = golden (older runs)


# ---------------------------------------------------------------- perf
class PerfResult(_Model):
    test: str
    cycles: Optional[int] = None
    instrs: Optional[int] = None
    cpi: Optional[float] = None
    stalls: dict[str, int] = Field(default_factory=dict)
    period: Optional[float] = None        # ps used for TPI
    period_source: Optional[str] = None   # "svkit.toml", "sweep:<id>", "synth:<id>" or "manual"
    tpi: Optional[float] = None           # ps per instruction = cpi * period


# ---------------------------------------------------------------- X trace
class XCause(str, Enum):
    no_reset = "no_reset"                 # class 1: register never reset / never assigned
    uninit_memory = "uninit_memory"       # class 2: uninitialized memory word read
    index_out_of_range = "index_out_of_range"   # class 2: out-of-range index
    multi_driver = "multi_driver"         # class 3 (post-MVP)
    external_input = "external_input"     # class 4: X/Z from testbench or a top-level port
    latch = "latch"                       # class 5 (post-MVP)
    unknown = "unknown"                   # bound hit / unsupported construct


class DriverRef(_Model):
    kind: Literal["assign", "always_comb", "always_ff", "always_latch", "port", "initial", "input", "unknown"]
    file: Optional[str] = None
    line: Optional[int] = None
    text: Optional[str] = None            # one-line source excerpt


class XTraceNode(_Model):
    signal: str                           # full hierarchical name, dot-separated
    time: int                             # waves ticks at which the value was evaluated
    value: str                            # value as printed, e.g. "8'bxxxx0101"
    driver: Optional[DriverRef] = None
    cause: Optional[XCause] = None        # set on root-cause leaves
    note: Optional[str] = None            # human explanation, e.g. "mem[5] never written"
    children: list["XTraceNode"] = Field(default_factory=list)


SimResult.model_rebuild()
XTraceNode.model_rebuild()


# ---------------------------------------------------------------- compare / sweep
class MetricDelta(_Model):
    metric: str                     # "wns", "area_total", "power.total", "vt_mix.LVT", ...
    unit: Optional[str] = None
    better: Literal["higher", "lower", "none"]
    values: list[Optional[float]]   # one per run, in requested order
    deltas: list[Optional[float]]   # value - values[0] (baseline = first run); first is 0/None
    pct: list[Optional[float]]      # relative delta in %
    verdict: list[Literal["better", "worse", "same", "na"]]


class CompareResult(_Model):
    run_ids: list[str]
    labels: list[str]               # experiment name or run id
    metrics: list[MetricDelta]


class SweepPoint(_Model):
    period: float                   # ps
    run_id: str
    wns: Optional[float] = None
    hold_wns: Optional[float] = None
    area_total: Optional[float] = None
    power_total: Optional[float] = None
    passed: bool
    note: Optional[str] = None      # why an error point failed (tool error text)


class SweepResult(_Model):
    group_id: str
    knob: str = "clock_period"
    tol: float                      # ps
    points: list[SweepPoint] = Field(default_factory=list)   # sorted by period
    t_min: Optional[float] = None   # ps, tightest passing period
    t_min_run_id: Optional[str] = None
    status: Literal["running", "done", "failed"] = "running"
    criterion: Literal["setup+hold", "setup"] = "setup+hold"   # "setup": hold failed independent of period
    note: Optional[str] = None      # why the criterion changed or the search stopped early
