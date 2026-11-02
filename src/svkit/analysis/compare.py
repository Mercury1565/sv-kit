"""N-run synthesis compare (MVP §4.1): one MetricDelta row per metric, baseline = first run."""
from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from svkit.core.rundb import Run
from svkit.schemas import CompareResult, MetricDelta

REL_EPS = 1e-4      # |delta| <= 0.01 % of |baseline| counts as "same"
ABS_EPS = 1e-9

# (metric, unit, better); unit None = take from the result (area_unit)
_CORE: list[tuple[str, Optional[str], Literal["higher", "lower", "none"]]] = [
    ("wns", "ps", "higher"),
    ("tns", "ps", "higher"),
    ("hold_wns", "ps", "higher"),
    ("hold_tns", "ps", "higher"),
    ("period", "ps", "lower"),
    ("area_total", None, "lower"),
    ("power.total", "mW", "lower"),
    ("power.dynamic", "mW", "lower"),
    ("power.leakage", "mW", "lower"),
    ("cell_count", None, "lower"),
]


def _get(m: Optional[dict[str, Any]], metric: str) -> Optional[float]:
    if not m:
        return None
    cur: Any = m
    head, _, rest = metric.partition(".")
    if head in ("vt_mix", "area_by_hier"):
        cur = (m.get(head) or {}).get(rest)
    else:
        for part in metric.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
    if isinstance(cur, bool) or cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(ABS_EPS, REL_EPS * abs(b))


def _row(metric: str, unit: Optional[str], better, metrics: list[Optional[dict]]) -> Optional[MetricDelta]:
    values = [_get(m, metric) for m in metrics]
    if all(v is None for v in values):
        return None
    base = values[0]
    deltas, pct, verdict = [], [], []
    for v in values:
        if v is None or base is None:
            deltas.append(None)
            pct.append(None)
            verdict.append("na")
            continue
        d = v - base
        deltas.append(d)
        pct.append(d / abs(base) * 100.0 if base != 0 else None)
        if _same(v, base):
            verdict.append("same")
        elif better == "none":
            verdict.append("na")
        elif (d > 0) == (better == "higher"):
            verdict.append("better")
        else:
            verdict.append("worse")
    return MetricDelta(metric=metric, unit=unit, better=better, values=values, deltas=deltas, pct=pct,
                       verdict=verdict)


def _labels(runs: list[Run]) -> list[str]:
    base = [r.experiment or r.id for r in runs]
    out = []
    for r, b in zip(runs, base):
        out.append(f"{b} ({r.id})" if base.count(b) > 1 and b != r.id else b)
    return out


def _top_level_hier(metrics: Iterable[Optional[dict]]) -> list[str]:
    keys: set[str] = set()
    for m in metrics:
        for k in (m or {}).get("area_by_hier") or {}:
            if k and "/" not in k and "." not in k:
                keys.add(k)
    return sorted(keys)


def compare(runs: list[Run]) -> CompareResult:
    """Compare synthesis Runs (metrics = SynthResult JSON). Runs without metrics show as `na`."""
    if not runs:
        return CompareResult(run_ids=[], labels=[], metrics=[])
    ms = [r.metrics if isinstance(r.metrics, dict) else None for r in runs]
    area_unit = next((m.get("area_unit") for m in ms if m and m.get("area_unit")), None)
    rows: list[MetricDelta] = []
    for metric, unit, better in _CORE:
        if metric == "area_total":
            unit = area_unit
        row = _row(metric, unit, better, ms)
        if row:
            rows.append(row)
    vt_classes = sorted({k for m in ms for k in ((m or {}).get("vt_mix") or {})})
    for c in vt_classes:
        # absent class in a run that has a vt_mix at all means 0 cells of that class
        filled = [({**m, "vt_mix": {c: 0, **(m.get("vt_mix") or {})}} if m and m.get("vt_mix") else m) for m in ms]
        row = _row(f"vt_mix.{c}", "cells", "none", filled)
        if row:
            rows.append(row)
    for h in _top_level_hier(ms):
        row = _row(f"area_by_hier.{h}", area_unit, "lower", ms)
        if row:
            rows.append(row)
    return CompareResult(run_ids=[r.id for r in runs], labels=_labels(runs), metrics=rows)
