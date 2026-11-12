"""CPI / TPI table (PRODUCT.md §6.6, MVP §4.4).

CPI comes from sim Runs (`metrics.perf`, filled by the sim adapter through the
`[perf].source` adapter). TPI = CPI x clock period. Where the period comes from is
chosen per request (`period_from`):

  toml   : the `default` of the `clock_period` knob in svkit.toml (ps), else the
           latest finished synth Run's `period`  (the default)
  sweep  : the T_min of the latest finished clock sweep, else the latest synth period;
           in the history, the one in effect when each run was made
  manual : a period the user typed (ps), for every run

It is computed on request, so it follows new synth / sweep Runs automatically.
"""
from __future__ import annotations

from typing import Callable, Literal, Optional

from svkit.core.rundb import RunDB
from svkit.schemas import PerfResult

PeriodFrom = Literal["toml", "sweep", "manual"]
PERIOD_FROM: tuple[str, ...] = ("toml", "sweep", "manual")
CONFIG_SOURCE = "svkit.toml"
MANUAL_SOURCE = "manual"

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


def period_resolver(cfg, db: RunDB, period_from: str = "toml",
                    manual: Optional[float] = None) -> Callable[[Optional[float]], Period]:
    """A function: run time (None = now) -> (period ps, source) for the chosen `period_from`."""
    if period_from == "manual":
        if manual is None or not manual > 0:
            raise ValueError("period_from=manual needs a clock period in ps greater than 0")
        return lambda _t: (float(manual), MANUAL_SOURCE)
    if period_from == "sweep":
        lists = (_sweep_periods(db), _synth_periods(db))
        return lambda t: period_at(lists, t)
    if period_from == "toml":
        fixed = config_period(cfg)
        if fixed is not None:
            return lambda _t: (fixed, CONFIG_SOURCE)
        synths = (_synth_periods(db),)
        return lambda t: period_at(synths, t)
    raise ValueError(f"period_from must be one of {', '.join(PERIOD_FROM)}, got {period_from!r}")


def current_period(cfg, db: RunDB, period_from: str = "toml", manual: Optional[float] = None) -> Period:
    """(period ps, source) — source is "svkit.toml", "sweep:<group id>", "synth:<run id>" or "manual"."""
    return period_resolver(cfg, db, period_from, manual)(None)


def perf_table(cfg, db: RunDB, *, group_id: str | None = None, period_from: str = "toml",
               period: Optional[float] = None) -> list[PerfResult]:
    """Latest sim Run per test that reports perf (optionally within one group), sorted by test.
    `period_from` / `period` choose the clock period (see the module docstring)."""
    period, source = current_period(cfg, db, period_from, period)
    latest: dict[str, PerfResult] = {}
    for r in db.list_runs(kind="sim", group_id=group_id, limit=100000):   # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        test = perf.get("test") or r.test or r.id
        if test in latest:
            continue
        p = PerfResult.model_validate({**perf, "test": test})
        if p.cpi is None and p.cycles is not None and p.instrs:
            p.cpi = p.cycles / p.instrs
        p.period, p.period_source = period, source
        p.tpi = p.cpi * period if (p.cpi is not None and period is not None) else None
        latest[test] = p
    return [latest[k] for k in sorted(latest)]


def perf_history(cfg, db: RunDB, test: str, *, limit: int = 500, period_from: str = "toml",
                 period: Optional[float] = None) -> list[dict]:
    """Every sim Run of `test` that reported perf, oldest first, with the batch it ran in. The clock
    period follows `period_from`: svkit.toml or a manual period is the same for every run; the sweep
    (and the synth fallback) uses the one in effect when that run was made."""
    resolve = period_resolver(cfg, db, period_from, period)
    groups: dict[str, Optional[str]] = {}
    out: list[dict] = []
    for r in db.list_runs(kind="sim", test=test, limit=100000):         # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        p = PerfResult.model_validate({**perf, "test": test})
        if p.cpi is None and p.cycles is not None and p.instrs:
            p.cpi = p.cycles / p.instrs
        p.period, p.period_source = resolve(r.created_at)
        p.tpi = p.cpi * p.period if (p.cpi is not None and p.period is not None) else None
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
