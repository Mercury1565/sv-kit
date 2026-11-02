"""Golden-diff jump (MVP §4.3): a failing test opens at its first divergence.

The sim adapter records `metrics.divergence` (svkit.core.golden). When only a
cycle is known (`[flows.sim.*.golden].time_unit = "cycle"`), it is mapped to a
waveform time here: cycle N = the N-th (0-based) rising edge of
`[project].clock` strictly after `[project].reset` deasserts.
"""
from __future__ import annotations

from typing import Optional

from svkit.adapters.waves.locate import wave_path
from svkit.adapters.waves.wellen import WaveFile, find_vars, open_wave
from svkit.core.rundb import Run, RunDB
from svkit.schemas import Divergence


def _reset_release(wf: WaveFile, path: Optional[str], active_high: bool) -> int:
    t_start = wf.time_range()[0]
    if path is None:
        return t_start
    s = wf.signal(path)
    on = "1" if active_high else "0"
    seen_active = False
    for i in range(s.n):
        v = s.value(i)
        if v == on:
            seen_active = True
        elif seen_active and v == ("0" if active_high else "1"):
            return s.time(i)
    return t_start


def _rising_edge(wf: WaveFile, clk: str, after: int, n: int) -> Optional[int]:
    """Time of the n-th (0-based) 0->1 transition of `clk` strictly after `after`."""
    s = wf.signal(clk)
    i = s.bisect_right(after)
    if i >= s.n:
        return None
    # fast path: a clean periodic clock alternates 0/1, so edge n is 2n changes later
    first = None
    for k in (i, i + 1):
        if 0 < k < s.n and s.value(k) == "1" and s.value(k - 1) == "0":
            first = k
            break
    if first is not None and first + 2 < s.n:
        period = s.time(first + 2) - s.time(first)
        k = first + 2 * n
        if k < s.n and s.value(k) == "1" and s.value(k - 1) == "0" and s.time(k) - s.time(first) == n * period:
            return s.time(k)
    # general path: walk the changes
    count = -1
    prev = s.value(i - 1) if i > 0 else None
    for k in range(i, s.n):
        v = s.value(k)
        if v == "1" and prev == "0":
            count += 1
            if count == n:
                return s.time(k)
        prev = v
    return None


def cycle_to_time(cfg, wf: WaveFile, cycle: int) -> Optional[int]:
    top = cfg.project.tb_top or cfg.project.top
    clks = find_vars(wf, cfg.project.clock, under=top)
    if not clks:
        return None
    rsts = find_vars(wf, cfg.project.reset.name, under=top)
    release = _reset_release(wf, rsts[0] if rsts else None, cfg.project.reset.active == "high")
    return _rising_edge(wf, clks[0], release, int(cycle))


def run_divergence(cfg, db: RunDB, run: Run) -> Optional[Divergence]:
    """The run's Divergence with `time` filled when it can be mapped from `cycle`."""
    raw = (run.metrics or {}).get("divergence") if isinstance(run.metrics, dict) else None
    if not raw:
        return None
    d = Divergence.model_validate(raw)
    if d.time is None and d.cycle is not None:
        p = wave_path(db, run)
        if p is not None:
            try:
                d.time = cycle_to_time(cfg, open_wave(p), d.cycle)
            except Exception:
                d.time = None
    return d
