"""Vt-mix classification from cell names via [library].vt_suffix (longest suffix wins)."""
from __future__ import annotations


def vt_class(cell: str, vt_suffix: dict[str, str]) -> str:
    best = None
    for suf, cls in vt_suffix.items():
        if cell.endswith(suf) and (best is None or len(suf) > len(best[0])):
            best = (suf, cls)
    return best[1] if best else "other"


def vt_mix(cells_by_type: dict[str, int], vt_suffix: dict[str, str]) -> dict[str, int]:
    if not vt_suffix:
        return {}
    out: dict[str, int] = {}
    for cell, n in cells_by_type.items():
        c = vt_class(cell, vt_suffix)
        out[c] = out.get(c, 0) + n
    return dict(sorted(out.items()))
