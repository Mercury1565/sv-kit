"""CPI / TPI table (PRODUCT.md §6.6, MVP §4.4).

CPI comes from sim Runs (`metrics.perf`, filled by the sim adapter through the
`[perf].source` adapter). TPI = CPI x clock period, where the period is the T_min
of the latest finished sweep, else the `period` of the latest finished synth Run.
It is computed on request, so it follows new synth / sweep Runs automatically.
"""
from __future__ import annotations

from typing import Optional

from svkit.core.rundb import RunDB
from svkit.schemas import PerfResult


def current_period(db: RunDB) -> tuple[Optional[float], Optional[str]]:
    """(period ps, source) — source is "sweep:<group id>" or "synth:<run id>"."""
    for g in db.list_groups("sweep", limit=1000):
        if g.status == "done" and g.result and g.result.get("t_min") is not None:
            return float(g.result["t_min"]), f"sweep:{g.id}"
    for r in db.list_runs(kind="synth", status="done", limit=1000):
        p = (r.metrics or {}).get("period")
        if p is not None:
            return float(p), f"synth:{r.id}"
    return None, None


def perf_table(cfg, db: RunDB, *, group_id: str | None = None) -> list[PerfResult]:
    """Latest sim Run per test that reports perf (optionally within one group), sorted by test."""
    period, source = current_period(db)
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


def _periods(db: RunDB) -> tuple[list[tuple[float, float, str]], list[tuple[float, float, str]]]:
    """(sweeps, synths): every known clock period as (created_at, period ps, source), oldest first."""
    sweeps = [(g.created_at, float(g.result["t_min"]), f"sweep:{g.id}") for g in db.list_groups("sweep", limit=100000)
              if g.status == "done" and g.result and g.result.get("t_min") is not None]
    synths = [(r.created_at, float(r.metrics["period"]), f"synth:{r.id}")
              for r in db.list_runs(kind="synth", status="done", limit=100000)
              if (r.metrics or {}).get("period") is not None]
    return sorted(sweeps), sorted(synths)


def period_at(sweeps, synths, t: float) -> tuple[Optional[float], Optional[str]]:
    """The clock period svkit knew at time `t` (the same preference as `current_period`: the latest
    sweep's T_min, else the latest synth period). For a run older than any of them, the earliest known."""
    for known in (sweeps, synths):
        before = [k for k in known if k[0] <= t]
        if before:
            return before[-1][1], before[-1][2]
    for known in (sweeps, synths):
        if known:
            return known[0][1], known[0][2]
    return None, None


def perf_history(db: RunDB, test: str, *, limit: int = 500) -> list[dict]:
    """Every sim Run of `test` that reported perf, oldest first, with the batch it ran in and the
    clock period in effect when it ran (so TPI follows the design as it was then)."""
    sweeps, synths = _periods(db)
    groups: dict[str, Optional[str]] = {}
    out: list[dict] = []
    for r in db.list_runs(kind="sim", test=test, limit=100000):         # newest first
        perf = (r.metrics or {}).get("perf")
        if not perf:
            continue
        p = PerfResult.model_validate({**perf, "test": test})
        if p.cpi is None and p.cycles is not None and p.instrs:
            p.cpi = p.cycles / p.instrs
        p.period, p.period_source = period_at(sweeps, synths, r.created_at)
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
