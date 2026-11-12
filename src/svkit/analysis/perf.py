"""CPI / TPI table (PRODUCT.md §6.6, MVP §4.4).

CPI comes from sim Runs (`metrics.perf`, filled by the sim adapter through the
`[perf].source` adapter). TPI = CPI x clock period, where the period is the
`default` of the `clock_period` knob in svkit.toml (ps), else the `period` of the
latest finished synth Run. It is computed on request, so it follows edits to
svkit.toml and new synth Runs automatically.
"""
from __future__ import annotations

from typing import Optional

from svkit.core.rundb import RunDB
from svkit.schemas import PerfResult

CONFIG_SOURCE = "svkit.toml"


def config_period(cfg) -> Optional[float]:
    """The `clock_period` knob's default from svkit.toml, in ps (None if not set)."""
    knob = (getattr(cfg, "knobs", None) or {}).get("clock_period")
    if knob is None or isinstance(knob.default, bool) or knob.default in (None, ""):
        return None
    try:
        return float(knob.default)
    except (TypeError, ValueError):
        return None


def current_period(cfg, db: RunDB) -> tuple[Optional[float], Optional[str]]:
    """(period ps, source) — source is "svkit.toml" or "synth:<run id>"."""
    p = config_period(cfg)
    if p is not None:
        return p, CONFIG_SOURCE
    for r in db.list_runs(kind="synth", status="done", limit=1000):
        p = (r.metrics or {}).get("period")
        if p is not None:
            return float(p), f"synth:{r.id}"
    return None, None


def perf_table(cfg, db: RunDB, *, group_id: str | None = None) -> list[PerfResult]:
    """Latest sim Run per test that reports perf (optionally within one group), sorted by test."""
    period, source = current_period(cfg, db)
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


def _synth_periods(db: RunDB) -> list[tuple[float, float, str]]:
    """Every synth clock period as (created_at, period ps, source), oldest first."""
    return sorted((r.created_at, float(r.metrics["period"]), f"synth:{r.id}")
                  for r in db.list_runs(kind="synth", status="done", limit=100000)
                  if (r.metrics or {}).get("period") is not None)


def period_at(synths, t: float) -> tuple[Optional[float], Optional[str]]:
    """The latest synth period known at time `t`; for a run older than all of them, the earliest."""
    before = [k for k in synths if k[0] <= t]
    if before:
        return before[-1][1], before[-1][2]
    if synths:
        return synths[0][1], synths[0][2]
    return None, None


def perf_history(cfg, db: RunDB, test: str, *, limit: int = 500) -> list[dict]:
    """Every sim Run of `test` that reported perf, oldest first, with the batch it ran in. The clock
    period is the one in svkit.toml for every run; without it, the synth period in effect when that
    run was made (so TPI follows the design as it was then)."""
    fixed = config_period(cfg)
    synths = [] if fixed is not None else _synth_periods(db)
    groups: dict[str, Optional[str]] = {}
    out: list[dict] = []
    for r in db.list_runs(kind="sim", test=test, limit=100000):         # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        p = PerfResult.model_validate({**perf, "test": test})
        if p.cpi is None and p.cycles is not None and p.instrs:
            p.cpi = p.cycles / p.instrs
        p.period, p.period_source = (fixed, CONFIG_SOURCE) if fixed is not None else period_at(synths, r.created_at)
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
