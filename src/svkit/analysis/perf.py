"""CPI / TPI table (PRODUCT.md §6.6, MVP §4.4).

CPI comes from sim Runs (`metrics.perf`, filled by the sim adapter through the
`[perf].source` adapter). TPI = CPI x clock period.

The clock period of a run is FIXED when the run finishes (`fix_period`, called by the
runner): it is stored in `metrics.perf_clock = {period, source}` and never recomputed.
Which period a finishing run gets is a project setting (`period_choice`, kept in the
Run DB so the web UI and the CLI share it):

  toml   : the `default` of the `clock_period` knob in svkit.toml (ps), else the
           latest finished synth Run's `period`  (the default)
  sweep  : the T_min of the latest finished clock sweep, else the latest synth period
  manual : a period the user typed (ps)

Runs recorded before periods were fixed have no `perf_clock`; the first read fixes
them with the rule svkit used before v0.12: the sweep T_min (else synth period) in
effect when the run was made.
"""
from __future__ import annotations

from typing import Optional

from svkit.core.rundb import RunDB
from svkit.schemas import PerfResult

PERIOD_FROM: tuple[str, ...] = ("toml", "sweep", "manual")
CONFIG_SOURCE = "svkit.toml"
MANUAL_SOURCE = "manual"
SETTING = "perf_period"
FIXED = "perf_clock"            # metrics key holding a run's fixed {period, source}

Period = tuple[Optional[float], Optional[str]]


def config_period(cfg) -> Optional[float]:
    """The `clock_period` knob's default from svkit.toml, in ps (None if not set)."""
    knob = (getattr(cfg, "knobs", None) or {}).get("clock_period")
    if knob is None or isinstance(knob.default, bool) or knob.default in (None, ""):
        return None
    try:
        return float(knob.default)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ the project setting
def check_choice(period_from: str, ps: Optional[float] = None) -> dict:
    """A valid setting dict, or ValueError."""
    if period_from not in PERIOD_FROM:
        raise ValueError(f"the clock period comes from one of {', '.join(PERIOD_FROM)}, not {period_from!r}")
    if period_from == "manual":
        if ps is None or not float(ps) > 0:
            raise ValueError("a manual clock period needs a value in ps greater than 0")
        return {"from": "manual", "ps": float(ps)}
    return {"from": period_from}


def period_choice(db: RunDB) -> dict:
    """Where finishing runs take their clock period from: {"from": ..., "ps"?: ...}; svkit.toml by default."""
    v = db.get_setting(SETTING)
    try:
        return check_choice(v["from"], v.get("ps"))
    except (TypeError, KeyError, ValueError, AttributeError):
        return {"from": "toml"}


def set_period_choice(db: RunDB, period_from: str, ps: Optional[float] = None) -> dict:
    choice = check_choice(period_from, ps)
    db.set_setting(SETTING, choice)
    return choice


# ------------------------------------------------------------------ periods known now / at a time
def _sweep_periods(db: RunDB) -> list[tuple[float, float, str]]:
    """Every finished sweep's T_min as (created_at, period ps, source), oldest first."""
    return sorted((g.created_at, float(g.result["t_min"]), f"sweep:{g.id}")
                  for g in db.list_groups("sweep", limit=100000)
                  if g.status == "done" and g.result and g.result.get("t_min") is not None)


def _synth_periods(db: RunDB) -> list[tuple[float, float, str]]:
    """Every synth clock period as (created_at, period ps, source), oldest first."""
    return sorted((r.created_at, float(r.metrics["period"]), f"synth:{r.id}")
                  for r in db.list_runs(kind="synth", status="done", limit=100000)
                  if (r.metrics or {}).get("period") is not None)


def period_at(known_lists, t: Optional[float]) -> Period:
    """The period known at time `t`, trying each list in order of preference (each oldest first).
    t=None means now (the latest). For a run older than everything, the earliest known."""
    for known in known_lists:
        before = known if t is None else [k for k in known if k[0] <= t]
        if before:
            return before[-1][1], before[-1][2]
    for known in known_lists:
        if known:
            return known[0][1], known[0][2]
    return None, None


def current_period(cfg, db: RunDB, choice: Optional[dict] = None) -> Period:
    """(period ps, source) a run finishing now gets — source is "svkit.toml", "sweep:<group id>",
    "synth:<run id>" or "manual". `choice` defaults to the project setting."""
    choice = choice or period_choice(db)
    if choice["from"] == "manual":
        return float(choice["ps"]), MANUAL_SOURCE
    if choice["from"] == "toml" and (p := config_period(cfg)) is not None:
        return p, CONFIG_SOURCE
    lists = (_sweep_periods(db), _synth_periods(db)) if choice["from"] == "sweep" else (_synth_periods(db),)
    return period_at(lists, None)


# ------------------------------------------------------------------ fixing a run's period
def fix_period(cfg, db: RunDB, run_id: str) -> None:
    """Store the clock period a sim run gets as it finishes (only runs that reported perf)."""
    r = db.get_run(run_id)
    m = dict(r.metrics or {})
    if not m.get("perf"):
        return
    period, source = current_period(cfg, db)
    m[FIXED] = {"period": period, "source": source}
    db.update_run(run_id, metrics=m)


def _fixed(db: RunDB, r, legacy: list) -> Period:
    """The run's fixed period; a run recorded before periods were fixed gets the pre-v0.12 rule
    (sweep, else synth, in effect when it was made), stored now so it never moves again."""
    m = r.metrics or {}
    if FIXED in m:
        return m[FIXED].get("period"), m[FIXED].get("source")
    if not legacy:
        legacy.extend([_sweep_periods(db), _synth_periods(db)])
    period, source = period_at(legacy, r.created_at)
    db.update_run(r.id, metrics={**m, FIXED: {"period": period, "source": source}})
    return period, source


def _result(perf: dict, test: str, period: Period) -> PerfResult:
    p = PerfResult.model_validate({**perf, "test": test})
    if p.cpi is None and p.cycles is not None and p.instrs:
        p.cpi = p.cycles / p.instrs
    p.period, p.period_source = period
    p.tpi = p.cpi * p.period if (p.cpi is not None and p.period is not None) else None
    return p


# ------------------------------------------------------------------ views
def perf_table(cfg, db: RunDB, *, group_id: str | None = None) -> list[PerfResult]:
    """Latest sim Run per test that reports perf (optionally within one group), sorted by test,
    each with the clock period fixed when it finished."""
    legacy: list = []
    latest: dict[str, PerfResult] = {}
    for r in db.list_runs(kind="sim", group_id=group_id, limit=100000):   # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        test = perf.get("test") or r.test or r.id
        if test in latest:
            continue
        latest[test] = _result(perf, test, _fixed(db, r, legacy))
    return [latest[k] for k in sorted(latest)]


def perf_history(cfg, db: RunDB, test: str, *, limit: int = 500) -> list[dict]:
    """Every sim Run of `test` that reported perf, oldest first, with the batch it ran in and the
    clock period fixed when that run finished."""
    legacy: list = []
    groups: dict[str, Optional[str]] = {}
    out: list[dict] = []
    for r in db.list_runs(kind="sim", test=test, limit=100000):         # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        p = _result(perf, test, _fixed(db, r, legacy))
        if r.group_id and r.group_id not in groups:
            try:
                groups[r.group_id] = db.get_group(r.group_id).name
            except KeyError:
                groups[r.group_id] = None
        out.append({**p.model_dump(mode="json"), "run_id": r.id, "run_name": r.name, "status": r.status,
                    "created_at": r.created_at, "cached_from": r.cached_from, "group_id": r.group_id,
                    "group_name": groups.get(r.group_id) if r.group_id else None})
        if len(out) >= limit:
            break
    return out[::-1]
