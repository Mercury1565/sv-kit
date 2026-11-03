"""VPD / FSDB -> VCD conversion helpers (Synopsys wave formats svkit cannot read natively).

* VPD: `vpd2vcd` if on PATH, else `vcs -vpd2vcd` (the form VCS installs always have).
  `+splitpacked` splits packed structs/arrays into separate VCD signals (default on).
* FSDB: `fsdb2vcd` (ships with Verdi) if on PATH; otherwise the file is noted, not converted.

Executables honour `[tools]` overrides (`vpd2vcd`, `vcs`, `fsdb2vcd`).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from svkit.config import Config


def vpd_converter(cfg: Config) -> Optional[list[str]]:
    """Command prefix for VPD->VCD, or None when no converter is available."""
    exe = shutil.which(cfg.tool("vpd2vcd"))
    if exe:
        return [exe]
    vcs = shutil.which(cfg.tool("vcs"))
    if vcs:
        return [vcs, "-vpd2vcd"]
    return None


def vpd2vcd_cmd(prefix: list[str], vpd: Path, vcd: Path, *, splitpacked: bool = True) -> list[str]:
    return [*prefix, *(["+splitpacked"] if splitpacked else []), str(vpd), str(vcd)]


def fsdb_converter(cfg: Config) -> Optional[list[str]]:
    exe = shutil.which(cfg.tool("fsdb2vcd"))
    return [exe] if exe else None


def fsdb2vcd_cmd(prefix: list[str], fsdb: Path, vcd: Path) -> list[str]:
    return [*prefix, str(fsdb), "-o", str(vcd)]


def convert_to_vcd(ctx, src: Path, vcd: Path, *, splitpacked: bool = True) -> tuple[bool, str]:
    """Convert a .vpd / .fsdb file to VCD with ctx.sh. Returns (ok, message)."""
    cfg = ctx.cfg
    suf = src.suffix.lower()
    if suf == ".vpd":
        prefix = vpd_converter(cfg)
        if prefix is None:
            return False, f"cannot convert {src.name}: neither vpd2vcd nor vcs is on PATH"
        cmd = vpd2vcd_cmd(prefix, src, vcd, splitpacked=splitpacked)
    elif suf == ".fsdb":
        prefix = fsdb_converter(cfg)
        if prefix is None:
            return False, f"{src.name} is FSDB and fsdb2vcd (Verdi) is not on PATH; waves not archived"
        cmd = fsdb2vcd_cmd(prefix, src, vcd)
    else:
        return False, f"unsupported wave format {src.suffix}"
    vcd.unlink(missing_ok=True)
    ctx.log("[svkit] " + " ".join(cmd))
    rc = ctx.sh(cmd, cwd=src.parent)
    if rc != 0 or not vcd.is_file() or vcd.stat().st_size == 0:
        return False, f"{' '.join(cmd[:2])} failed (exit {rc}) converting {src.name}"
    return True, f"converted {src.name} to VCD"
