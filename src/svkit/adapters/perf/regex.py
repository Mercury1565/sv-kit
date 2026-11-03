"""`[perf].source = { regex_file, cycles, instrs, stalls }` — a generic perf-counter parser.

The testbench writes some text file per test (e.g. `output/{test}.cpi`); each configured
regex has one capture group holding an integer. Used by every sim adapter so CPI is
identical across flows; the Analysis track builds TPI on top of the PerfResult.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from svkit.config import Config, RegexPerfSource, expand
from svkit.schemas import PerfResult


def _int(rx: str, text: str) -> Optional[int]:
    m = re.search(rx, text, re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1), 0)
    except (ValueError, IndexError):
        try:
            return int(float(m.group(1)))
        except (ValueError, IndexError):
            return None


def parse_text(src: RegexPerfSource, test: str, text: str) -> PerfResult:
    cycles, instrs = _int(src.cycles, text), _int(src.instrs, text)
    stalls = {k: v for k, rx in src.stalls.items() if (v := _int(rx, text)) is not None}
    cpi = cycles / instrs if cycles is not None and instrs else None
    return PerfResult(test=test, cycles=cycles, instrs=instrs, cpi=cpi, stalls=stalls)


def parse_perf(cfg: Config, test: str, workdir: Path) -> Optional[PerfResult]:
    """Read the configured perf file for `test` (relative to `workdir`). None if not configured,
    missing, or nothing matched."""
    src = cfg.perf.source
    if src is None:
        return None
    f = Path(expand(src.regex_file, test=test, top=cfg.project.top, root=str(cfg.root), workdir=str(workdir)))
    if not f.is_absolute():
        f = workdir / f
    if not f.is_file():
        return None
    res = parse_text(src, test, f.read_text(errors="replace"))
    if res.cycles is None and res.instrs is None and not res.stalls:
        return None
    return res
