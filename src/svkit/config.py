"""`svkit.toml` schema and loader (shared contract — see docs/ARCHITECTURE.md §2).

Everything project-specific lives in the user's svkit.toml. Nothing here may
assume a particular project, path, library or file format.

Template strings: any string field documented as a *template* may contain
`{test}`, `{top}`, `{root}`, `{workdir}`, `{run_dir}` and, for knob-related
fields, `{value}`. They are expanded with `str.format_map` (unknown keys are
left untouched).
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

CONFIG_NAME = "svkit.toml"


class ConfigError(Exception):
    pass


class _M(BaseModel):
    model_config = {"extra": "forbid"}


# ------------------------------------------------------------------ project
class Sources(_M):
    glob: list[str] = Field(default_factory=list)       # relative to project root
    filelist: Optional[str] = None                      # a .f file (one path / +incdir+ / +define+ per line)
    exclude: list[str] = Field(default_factory=list)    # globs to drop (e.g. testbenches for synth)

    @field_validator("glob", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        return [v] if isinstance(v, str) else v


class Reset(_M):
    name: str = "reset"
    active: Literal["high", "low"] = "high"


class Project(_M):
    name: str
    top: str                                            # synthesis / design top module
    tb_top: Optional[str] = None                        # simulation top (testbench), defaults to top
    sources: Sources = Field(default_factory=Sources)
    tb_sources: Sources = Field(default_factory=Sources)  # testbench-only sources (sim only)
    include: list[str] = Field(default_factory=list)
    defines: dict[str, Union[str, int, bool]] = Field(default_factory=dict)
    clock: str = "clock"
    reset: Reset = Field(default_factory=Reset)
    # files/dirs NOT copied into isolated work dirs (in addition to .git/.svkit)
    workdir_exclude: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ flows
class Golden(_M):
    actual: str            # template, relative to the run's cwd
    expected: str          # template, relative to project root
    ignore: list[str] = Field(default_factory=list)   # regexes; matching lines are skipped in both
    # mapping a mismatching line to sim time (golden-diff jump, Phase 2):
    time_regex: Optional[str] = None    # regex with one group capturing time or cycle from the actual line
    time_unit: Literal["ticks", "cycle"] = "ticks"


class Tests(_M):
    glob: Optional[str] = None           # e.g. "programs/*.mem"; test name = file stem
    names: list[str] = Field(default_factory=list)   # explicit names (in addition to glob)


class SimFlow(_M):
    """[flows.sim.<adapter>] — adapter is 'vcs', 'verilator', ...

    mode 'wrap'   : run `cmd` (the user's existing build) — preferred.
    mode 'builtin': the adapter compiles project sources itself in an isolated work dir.
    """
    mode: Literal["wrap", "builtin"] = "wrap"
    cmd: Optional[str] = None            # template; required for wrap mode
    cwd: str = "."                       # relative to project (or work dir) root
    isolate: bool = False                # run inside an isolated copy of the project
    waves: Optional[str] = None          # template: produced waveform (vcd/fst)
    log: Optional[str] = None            # template: sim log to keep
    golden: Optional[Golden] = None
    tests: Tests = Field(default_factory=Tests)
    pass_regex: Optional[str] = None     # if set, log must match to pass
    fail_regex: Optional[str] = None     # if matches log, test fails
    timeout_s: int = 3600
    args: list[str] = Field(default_factory=list)   # extra tool args (builtin mode)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "wrap" and not self.cmd:
            raise ValueError("wrap mode needs `cmd`")
        return self


class SynthFlow(_M):
    """[flows.synth.<adapter>] — adapter is 'dc', 'yosys', ...

    The synth flow of a run produces a SynthResult. For tools without timing
    (yosys) the flow names an STA adapter in `sta` that is run on the netlist.
    """
    mode: Literal["wrap", "builtin"] = "wrap"
    cmd: Optional[str] = None            # template, e.g. "make cpu.vg" or "dc_shell-t -f {script}"
    script: Optional[str] = None         # the user's TCL/ys script (wrapped, never edited)
    cwd: str = "."
    reports: list[str] = Field(default_factory=list)  # templates: report files to parse
    netlist: Optional[str] = None        # template: gate netlist (.vg / .v / .json)
    log: Optional[str] = None
    license_seats: int = 0               # 0 = unlimited; caps concurrent runs of this adapter
    sta: Optional[str] = None            # name of an [flows.sta.<x>] entry (open-source flows)
    append_before: Optional[str] = None  # regex anchor for experiment `tcl_append` (e.g. '^\\s*compile'); None = end
    timeout_s: int = 6 * 3600
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "wrap" and not self.cmd:
            raise ValueError("wrap mode needs `cmd`")
        return self


class StaFlow(_M):
    """[flows.sta.<adapter>] — e.g. 'opensta'."""
    mode: Literal["wrap", "builtin"] = "builtin"
    cmd: Optional[str] = None
    sdc: Optional[str] = None            # user's SDC (template); if None, svkit generates clock-only SDC
    reports: list[str] = Field(default_factory=list)
    max_paths: int = 5
    env: dict[str, str] = Field(default_factory=dict)


class Flows(_M):
    sim: dict[str, SimFlow] = Field(default_factory=dict)
    synth: dict[str, SynthFlow] = Field(default_factory=dict)
    sta: dict[str, StaFlow] = Field(default_factory=dict)
    default_sim: Optional[str] = None     # adapter name; default = first defined
    default_synth: Optional[str] = None


# ------------------------------------------------------------------ knobs
class Knob(_M):
    """How svkit changes one setting. Exactly one mechanism must be set.

    file        : write `{value}` (or `format`) to this file in the work dir
    env         : export this environment variable
    tcl_var     : `set <name> <value>` in the generated TCL prelude, before the user's script
    tcl_replace : regex/text in the user's script replaced by `with` (last resort)
    tcl_append  : TCL appended after `before` anchor (or end of script) — value is the text
    define      : a Verilog `+define+NAME=value` for sim
    """
    file: Optional[str] = None
    env: Optional[str] = None
    tcl_var: Optional[str] = None
    tcl_replace: Optional[str] = None     # pattern (regex); `with` is a template using {value}
    with_: Optional[str] = Field(default=None, alias="with")
    tcl_append: Optional[bool] = None
    before: Optional[str] = None          # regex anchor for tcl_append (default: end of script)
    define: Optional[str] = None
    format: str = "{value}"               # how the value is rendered
    unit: Optional[Literal["ps", "ns", "fs", "us"]] = None   # for time knobs (clock_period)
    applies_to: list[Literal["sim", "synth", "sta"]] = Field(default_factory=lambda: ["synth", "sta"])
    default: Optional[Union[str, int, float, bool]] = None   # current value, if known

    model_config = {"extra": "forbid", "populate_by_name": True}

    @model_validator(mode="after")
    def _exactly_one(self):
        mechs = [m for m in ("file", "env", "tcl_var", "tcl_replace", "tcl_append", "define")
                 if getattr(self, m) not in (None, False)]
        if len(mechs) != 1:
            raise ValueError(f"knob needs exactly one of file/env/tcl_var/tcl_replace/tcl_append/define, got {mechs or 'none'}")
        if self.tcl_replace and self.with_ is None:
            raise ValueError("tcl_replace needs `with`")
        return self

    @property
    def mechanism(self) -> str:
        for m in ("file", "env", "tcl_var", "tcl_replace", "tcl_append", "define"):
            if getattr(self, m) not in (None, False):
                return m
        raise AssertionError


KnobValue = Union[str, int, float, bool, list]

# ad-hoc mechanisms usable directly inside an [experiments] entry without a [knobs] definition:
#   tcl_append = "set_max_fanout 8 [current_design]"   (str or list of str; appended after the user's script body)
#   env        = { NAME = "value" }
RESERVED_EXPERIMENT_KEYS = ("tcl_append", "env")


# ------------------------------------------------------------------ perf / library
class RegexPerfSource(_M):
    regex_file: str                       # template with {test}
    cycles: str                           # regex with one capture group
    instrs: str
    stalls: dict[str, str] = Field(default_factory=dict)   # cause -> regex


class Perf(_M):
    source: Optional[RegexPerfSource] = None


class Library(_M):
    vt_suffix: dict[str, str] = Field(default_factory=dict)   # cell-name suffix -> Vt class
    liberty: list[str] = Field(default_factory=list)          # .lib files (open-source flow)
    corners: dict[str, list[str]] = Field(default_factory=dict)  # corner -> liberty files


class Server(_M):
    port: int = 8470
    host: str = "127.0.0.1"


class Waves(_M):
    max_size_mb: int = 0                 # 0 = unlimited; skip archiving larger waves (link instead)


# ------------------------------------------------------------------ root
class Config(_M):
    project: Project
    flows: Flows = Field(default_factory=Flows)
    knobs: dict[str, Knob] = Field(default_factory=dict)
    experiments: dict[str, dict[str, KnobValue]] = Field(default_factory=dict)
    perf: Perf = Field(default_factory=Perf)
    library: Library = Field(default_factory=Library)
    server: Server = Field(default_factory=Server)
    waves: Waves = Field(default_factory=Waves)
    tools: dict[str, str] = Field(default_factory=dict)   # tool name -> executable path override

    # set by loader, not from TOML
    root: Path = Field(default=Path("."), exclude=True)

    @model_validator(mode="after")
    def _xref(self):
        for name, exp in self.experiments.items():
            for k in exp:
                if k not in self.knobs and k not in RESERVED_EXPERIMENT_KEYS:
                    raise ValueError(f"experiment {name!r} uses unknown knob {k!r} (define it under [knobs])")
        for name, sf in self.flows.synth.items():
            if sf.sta and sf.sta not in self.flows.sta:
                raise ValueError(f"flows.synth.{name}.sta = {sf.sta!r} but there is no [flows.sta.{sf.sta}]")
        return self

    # convenience
    def sim_flow(self, name: Optional[str] = None) -> tuple[str, SimFlow]:
        return _pick(self.flows.sim, name or self.flows.default_sim, "sim")

    def synth_flow(self, name: Optional[str] = None) -> tuple[str, SynthFlow]:
        return _pick(self.flows.synth, name or self.flows.default_synth, "synth")

    def tool(self, name: str) -> str:
        return self.tools.get(name, name)


def _pick(d: dict, name: Optional[str], kind: str):
    if not d:
        raise ConfigError(f"no [flows.{kind}.*] defined in {CONFIG_NAME}")
    if name is None:
        name = next(iter(d))
    if name not in d:
        raise ConfigError(f"no [flows.{kind}.{name}] in {CONFIG_NAME} (have: {', '.join(d)})")
    return name, d[name]


def find_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for d in (p, *p.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    raise ConfigError(f"no {CONFIG_NAME} found in {p} or its parents (run `svkit init`)")


def load(path: Path | str | None = None) -> Config:
    """Load and validate svkit.toml. `path` may be the file or the project dir."""
    if path is None:
        root = find_root()
        f = root / CONFIG_NAME
    else:
        p = Path(path).resolve()
        f = p / CONFIG_NAME if p.is_dir() else p
        root = f.parent
    try:
        data = tomllib.loads(f.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{f} not found")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{f}: {e}")
    return parse(data, root)


def parse(data: dict[str, Any], root: Path) -> Config:
    try:
        cfg = Config.model_validate(data)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            lines.append(f"  {loc}: {err['msg']}")
        raise ConfigError(f"invalid {CONFIG_NAME}:\n" + "\n".join(lines)) from None
    cfg.root = root
    return cfg


class _Default(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def expand(template: str, **vars: Any) -> str:
    """Expand a template string, leaving unknown {placeholders} intact."""
    return template.format_map(_Default({k: v for k, v in vars.items() if v is not None}))
