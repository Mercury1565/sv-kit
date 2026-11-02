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
