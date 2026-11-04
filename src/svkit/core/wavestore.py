"""How svkit stores the waveform it keeps for each run (`[waves].archive`).

"fst" (default): a VCD kept by a run is converted to FST (GTKWave's `vcd2fst`, also in OSS CAD
Suite), typically 20-60x smaller, and the VCD copy is dropped. The conversion is checked (the FST
must open and end at the VCD's last timestamp); if `vcd2fst` is missing or anything fails, the
VCD is kept and the run says why. "vcd": keep VCDs as they are (the pre-0.2 behaviour).

Switching back and forth only changes new runs; `svkit waves archive --to vcd|fst` converts the
waveforms already recorded (`fst2vcd` / `vcd2fst`), so a change of mind is one command.
The user's own output files (e.g. output/*.vcd) are never touched: only svkit's copies under
.svkit/runs/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from svkit import compat
from svkit.config import Config

CONVERTERS = {"fst": "vcd2fst", "vcd": "fst2vcd"}       # target format -> tool
TIMEOUT_S = 600


def converter(cfg: Config, to: str) -> Optional[str]:
    name = CONVERTERS[to]
    exe = cfg.tools.get(name)
    if exe:
        return shutil.which(exe) or (exe if os.access(exe, os.X_OK) else None)
    return shutil.which(name) or compat.locate(name)


def _end_time(path: Path) -> Optional[int]:
    from svkit.adapters.waves.wellen import open_wave
    try:
        return int(open_wave(path).time_range()[1])
    except Exception:
        return None


def convert(cfg: Config, src: Path, dst: Path) -> tuple[bool, str]:
    """Convert src (vcd/fst) to dst (fst/vcd). Writes a temp file and renames it only after it
    opens and ends at the same time as src. Returns (ok, message)."""
    to = dst.suffix.lstrip(".").lower()
    exe = converter(cfg, to)
    if exe is None:
        return False, f"{CONVERTERS[to]} not found (install GTKWave or OSS CAD Suite, or set [tools].{CONVERTERS[to]})"
    tmp = dst.with_name(dst.name + ".tmp" + dst.suffix)
    tmp.unlink(missing_ok=True)
    try:
        # vcd2fst <in> <out>; fst2vcd writes stdout unless given -o
        argv = [exe, str(src), str(tmp)] if to == "fst" else [exe, "-f", str(src), "-o", str(tmp)]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S,
                           preexec_fn=lambda: os.nice(5))
    except (OSError, subprocess.TimeoutExpired) as e:
        tmp.unlink(missing_ok=True)
        return False, f"{CONVERTERS[to]} failed: {e}"
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        err = (r.stderr or r.stdout).strip().splitlines()
        return False, f"{CONVERTERS[to]} exit {r.returncode}" + (f": {err[-1]}" if err else "")
    want, got = _end_time(src), _end_time(tmp)
    if got is None or (want is not None and want != got):
        tmp.unlink(missing_ok=True)
        return False, f"{CONVERTERS[to]} output did not check out (ends at {got}, expected {want})"
    os.replace(tmp, dst)
    return True, f"{src.name} -> {dst.name}: {src.stat().st_size / 1e6:.1f} MB -> {dst.stat().st_size / 1e6:.1f} MB"


def archive_run(cfg: Config, run_dir: Path, res, artifacts: dict[str, str],
                log: Callable[[str], None] = lambda s: None) -> Optional[str]:
    """After a sim run: store its kept waveform in the configured format. Updates `res.waves`
    and `artifacts` in place. Returns a warning text when it had to keep the original."""
    want = cfg.waves.archive
    cur = res.waves
    if not cur or Path(cur).suffix.lstrip(".") == want or want not in CONVERTERS:
        return None
    src = run_dir / cur
    if not src.is_file() or src.is_symlink():      # linked (over max_size_mb): not ours to rewrite
        return None
    dst = src.with_suffix("." + want)
    ok, msg = convert(cfg, src, dst)
    if not ok:
        log(f"[svkit] kept {src.name} as is: {msg}")
        return f"waveform kept as {src.suffix.lstrip('.').upper()} ({msg})"
    log(f"[svkit] waveform archived as {want.upper()} ({msg})")
    src.unlink()
    artifacts.pop(cur, None)
    artifacts[dst.name] = dst.name
    res.waves = dst.name
    return None


# ---------------------------------------------------------------- existing runs
@dataclass
class Rearchive:
    converted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    before: int = 0
    after: int = 0


def rearchive(cfg: Config, db, to: str, *, dry_run: bool = False,
              progress: Callable[[str], None] = lambda s: None) -> Rearchive:
    """Convert every run's own waveform to `to` ("fst" or "vcd") and update the Run DB, including
    runs that reuse (cache hits point at) a converted file."""
    out = Rearchive()
    src_ext = "vcd" if to == "fst" else "fst"
    runs = db.list_runs(limit=10 ** 9)
    moved: dict[tuple[str, str], str] = {}       # (run id, old artifact name) -> new name
    for r in runs:
        for name, rel in list(r.artifacts.items()):
            if Path(name).suffix.lstrip(".") != src_ext or not name.startswith("waves") or rel.startswith(".."):
                continue
            src = db.runs_dir / r.id / rel
            if not src.is_file() or src.is_symlink():
                continue
            dst = src.with_suffix("." + to)
            size = src.stat().st_size
            if dry_run:
                out.converted.append(r.id)
                out.before += size
                continue
            ok, msg = convert(cfg, src, dst)
            if not ok:
                out.failed.append((r.id, msg))
                continue
            out.before += size
            out.after += dst.stat().st_size
            src.unlink()
            moved[(r.id, name)] = dst.name
            arts = {k: v for k, v in r.artifacts.items() if k != name}
            arts[dst.name] = dst.name
            m = r.metrics if isinstance(r.metrics, dict) else None
            if m and m.get("waves") == name:
                m = {**m, "waves": dst.name}
            db.update_run(r.id, artifacts=arts, **({"metrics": m} if m else {}))
            out.converted.append(r.id)
            progress(f"{r.id}: {msg}")
    if moved and not dry_run:            # cache hits reference the original run's files
        for r in runs:
            changed = False
            arts = dict(r.artifacts)
            m = dict(r.metrics) if isinstance(r.metrics, dict) else None
            for name, rel in r.artifacts.items():
                parts = rel.split("/")
                if len(parts) == 3 and parts[0] == ".." and (parts[1], parts[2]) in moved:
                    new = moved[(parts[1], parts[2])]
                    arts.pop(name)
                    arts[new] = f"../{parts[1]}/{new}"
                    if m and m.get("waves") == name:
                        m["waves"] = new
                    changed = True
            if changed:
                db.update_run(r.id, artifacts=arts, **({"metrics": m} if m else {}))
    return out
