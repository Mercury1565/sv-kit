"""Find a Run's waveform artifact on disk."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from svkit.core.rundb import Run, RunDB

WAVE_SUFFIXES = (".fst", ".vcd")


def wave_path(db: RunDB, run: Run) -> Optional[Path]:
    """The run's waveform: `metrics.waves` (SimResult) first, then `waves.fst` / `waves.vcd`,
    then the first artifact with a .fst/.vcd suffix. None when the run kept no waveform."""
    arts = run.artifacts or {}
    names: list[str] = []
    w = (run.metrics or {}).get("waves") if isinstance(run.metrics, dict) else None
    if isinstance(w, str):
        names.append(w)
    names += ["waves.fst", "waves.vcd"]
    names += [n for n in arts if n.lower().endswith(WAVE_SUFFIXES)]
    base = db.runs_dir / run.id
    for n in names:
        rel = arts.get(n)
        if rel is None:
            continue
        p = (base / rel).resolve()
        if p.is_file():
            return p
    return None
