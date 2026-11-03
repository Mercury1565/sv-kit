"""Minimum clock-period sweep (PRODUCT.md §6.4, MVP §4.2).

    T0 = --start or the `clock_period` knob default -> synth -> WNS0
    T* ~= T0 - WNS0
    parallel grids (width = jobs, capped by the flow's license seats) around T*
    P = tightest passing period overall, F = loosest failing period below P
    refine between F and P until P - F < tol

Hold slack does not depend on the period (both ends of a hold check use the same edge), so
when even the loosest point meets setup but fails hold, a looser clock cannot help: the
sweep then judges points on setup alone and says so in `SweepResult.note`.

Synthesis is non-monotonic (a tighter target can pass where a looser one failed),
so P is always the tightest passing point seen, and failing points above P are
ignored when choosing the bracket. Each point is its own (cached) synth Run of
kind `sweep-point` in a `sweep` group; the group's `result` holds the current
SweepResult after every point, and a `group.updated` event is published.
"""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from svkit.config import ConfigError
from svkit.schemas import SweepPoint, SweepResult

KNOB = "clock_period"
MAX_GROWTH = 64        # give up when nothing passes at MAX_GROWTH x the start period


def passed(metrics: Optional[dict[str, Any]], *, hold: bool = True) -> bool:
    if not metrics:
        return False
    wns, hold_wns = metrics.get("wns"), metrics.get("hold_wns")
    return wns is not None and wns >= 0 and (not hold or hold_wns is None or hold_wns >= 0)


def _quantum(tol: float) -> float:
    """Grid resolution: whole ps unless the tolerance asks for finer steps."""
    if tol >= 4:
        return 1.0
    return 10 ** math.floor(math.log10(max(tol, 1e-6) / 4))


def _snap(t: float, q: float):
    v = round(round(t / q) * q, 6)
    return int(v) if float(v).is_integer() else v


class _Sweep:
    def __init__(self, runner, *, tol: float, width: int, flow: Optional[str], max_points: int, group_id: str):
        self.r = runner
        self.tol = float(tol)
        self.width = width
        self.flow = flow
        self.max_points = max_points
        self.gid = group_id
        self.q = _quantum(self.tol)
        self.points: dict[float, SweepPoint] = {}
        self.lock = threading.Lock()
        self.hold = True                 # False once hold is shown to be period-independent
        self.note: Optional[str] = None

    # ------------------------------------------------------------ bookkeeping
    def result(self, status: str = "running") -> SweepResult:
        pts = sorted(self.points.values(), key=lambda p: p.period)
        ok = [p for p in pts if p.passed]
        best = ok[0] if ok else None
        return SweepResult(group_id=self.gid, knob=KNOB, tol=self.tol, points=pts,
                           t_min=best.period if best else None, t_min_run_id=best.run_id if best else None,
                           status=status, criterion="setup+hold" if self.hold else "setup", note=self.note)

    def publish(self, status: str = "running") -> SweepResult:
        res = self.result(status)
        dump = res.model_dump(mode="json")
        group_status = "running" if status == "running" else ("done" if status == "done" else "failed")
        self.r.db.update_group(self.gid, status=group_status, result=dump)
        self.r.bus.publish({"type": "group.updated", "group_id": self.gid, "status": group_status, "result": dump})
        return res

    def run_point(self, t) -> SweepPoint:
        run = self.r.synth(flow=self.flow, sets={KNOB: t}, kind="sweep-point", group_id=self.gid)
        m = run.metrics if run.status == "done" else None
        power = ((m or {}).get("power") or {}).get("total")
        p = SweepPoint(period=float(t), run_id=run.id, wns=(m or {}).get("wns"), hold_wns=(m or {}).get("hold_wns"),
                       area_total=(m or {}).get("area_total"), power_total=power, passed=passed(m, hold=self.hold),
                       note=None if run.status == "done" else (run.message or run.status))
        with self.lock:
            self.points[float(t)] = p
            self.publish()
        return p

    def run_grid(self, periods: list[float]) -> None:
        todo, seen = [], set(self.points)
        for t in periods:
            if t <= 0:
                continue
            ts = _snap(t, self.q)
            if ts > 0 and float(ts) not in seen:
                seen.add(float(ts))
                todo.append(ts)
        room = self.max_points - len(self.points)
        todo = todo[:max(0, room)]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=min(self.width, len(todo))) as ex:
            list(ex.map(self.run_point, todo))

    def check_hold(self) -> None:
        """Drop the hold requirement when the loosest point meets setup but still fails hold."""
        if not self.hold or not self.points or self.bracket()[0] is not None:
            return
        loose = self.points[max(self.points)]
        if loose.wns is None or loose.wns < 0 or loose.hold_wns is None or loose.hold_wns >= 0:
            return
        worst = min(p.hold_wns for p in self.points.values() if p.hold_wns is not None)
        self.hold = False
        self.note = (f"hold fails even at {loose.period:g} ps, where setup has {loose.wns:g} ps of slack "
                     f"(worst hold WNS {worst:g} ps). A looser clock cannot fix hold, so T_min is judged on "
                     "setup only; fix hold in the synthesis script (e.g. set_fix_hold) for a full result.")
        with self.lock:
            for p in self.points.values():
                p.passed = p.wns is not None and p.wns >= 0
            self.publish()

    # ------------------------------------------------------------ search
    def bracket(self) -> tuple[Optional[SweepPoint], Optional[SweepPoint]]:
        """(P, F): tightest passing point, loosest failing point below it."""
        pts = sorted(self.points.values(), key=lambda p: p.period)
        ok = [p for p in pts if p.passed]
        if not ok:
            return None, (pts[-1] if pts else None)
        P = ok[0]
        below = [p for p in pts if not p.passed and p.period < P.period]
        return P, (below[-1] if below else None)

    def next_grid(self, rnd: int) -> list[float]:
        P, F = self.bracket()
        grow = 2 ** min(rnd, 6)
        if P is None:                                   # nothing passes yet: go looser
            hi = max(self.points)
            s = max(self.tol, 0.1 * hi) * grow
            ests = [p.period - p.wns for p in self.points.values() if p.wns is not None and p.wns < 0]
            c = max([hi + s] + [e for e in ests if e > hi])
            return [c + s * k for k in range(self.width)]
        if F is None:                                   # nothing fails below P: go tighter
            s = max(self.tol, 0.05 * P.period) * grow
            c = P.period - max(s, P.wns or 0.0)
            if c <= 0:
                c = P.period / 2
            return [c - s * k for k in range(self.width)] if c - s * (self.width - 1) > 0 else \
                [P.period * (k + 1) / (self.width + 1) for k in range(self.width)]
        lo, hi = F.period, P.period
        return [lo + (hi - lo) * (k + 1) / (self.width + 1) for k in range(self.width)]

    def done(self) -> bool:
        P, F = self.bracket()
        return P is not None and F is not None and P.period - F.period <= self.tol


def _start_period(cfg, start: Optional[float]) -> float:
    knob = cfg.knobs.get(KNOB)
    if knob is None:
        raise ConfigError(f"the clock sweep needs a [knobs.{KNOB}] entry in svkit.toml "
                          f"(e.g. {KNOB} = {{ env = \"CLOCK_PERIOD\", unit = \"ps\", default = 1000 }})")
    if start is not None:
        return float(start)
    if knob.default is None:
        raise ConfigError(f"[knobs.{KNOB}] has no `default`; pass --start <ps>")
    try:
        return float(knob.default)
    except (TypeError, ValueError):
        raise ConfigError(f"[knobs.{KNOB}].default must be a number of ps, got {knob.default!r}")


def sweep_clock(runner, *, tol: float = 50, jobs: int = 2, flow: str | None = None, start: float | None = None,
                max_points: int = 40) -> SweepResult:
    """Find the minimum clock period meeting setup and hold. Returns the final SweepResult."""
    cfg = runner.cfg
    t0 = _start_period(cfg, start)
    if tol <= 0:
        raise ConfigError("--tol must be > 0")
    flow_name, fl = cfg.synth_flow(flow)
    width = max(1, int(jobs))
    if fl.license_seats > 0:
        width = min(width, fl.license_seats)
    g = runner.db.create_group("sweep", {"tol": tol, "jobs": jobs, "flow": flow_name, "start": t0,
                                         "knob": KNOB, "width": width, "max_points": max_points})
    sw = _Sweep(runner, tol=tol, width=width, flow=flow, max_points=max_points, group_id=g.id)
    try:
        sw.publish()
        p0 = sw.run_point(_snap(t0, sw.q))
        if p0.wns is None:
            # no slack from the starting point (tool error): nothing to estimate from
            return sw.publish("done" if sw.result().t_min is not None else "failed")
        sw.check_hold()
        t_star = t0 - p0.wns
        s = max(tol, 0.02 * abs(t_star))
        sw.run_grid([t_star + s * (k - (width - 1) / 2) for k in range(width)])
        rnd = 0
        while not sw.done() and len(sw.points) < max_points:
            sw.check_hold()
            if sw.done():
                break
            if sw.bracket()[0] is None and max(sw.points) > MAX_GROWTH * t0:
                sw.note = f"no passing period up to {max(sw.points):g} ps ({MAX_GROWTH}x the start); stopped"
                break
            before = len(sw.points)
            sw.run_grid(sw.next_grid(rnd))
            rnd += 1
            P, F = sw.bracket()
            # widen only while expanding; the refine phase never needs to grow
            if P is not None and F is not None:
                rnd = 0
            if len(sw.points) == before:        # grid resolution exhausted
                break
        res = sw.result()
        return sw.publish("done" if res.t_min is not None else "failed")
    except BaseException:
        sw.publish("failed")
        raise
