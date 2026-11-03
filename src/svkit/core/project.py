"""Project helpers: source resolution, filelists, git info, source hashing, work dirs."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from svkit.config import Config, Sources, expand
from svkit.core.fsutil import copy_file, is_smb_placeholder

ALWAYS_EXCLUDE = {".git", ".svkit", ".venv", "node_modules", "__pycache__"}


def _glob(root: Path, patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        out += sorted(p for p in root.glob(pat) if p.is_file())
    return out


def parse_filelist(path: Path) -> tuple[list[Path], list[Path], dict[str, str]]:
    """Minimal .f parser: files, +incdir+, +define+, -f nesting, comments. Paths relative to the .f's dir."""
    files: list[Path] = []
    incs: list[Path] = []
    defs: dict[str, str] = {}
    base = path.parent
    toks: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("//", 1)[0].split("#", 1)[0].strip()
        if line:
            toks += line.split()
    it = iter(toks)
    for t in it:
        t = os.path.expandvars(t)
        if t.startswith("+incdir+"):
            incs += [base / d for d in t[len("+incdir+"):].split("+") if d]
        elif t.startswith("+define+"):
            for d in t[len("+define+"):].split("+"):
                if d:
                    k, _, v = d.partition("=")
                    defs[k] = v
        elif t in ("-f", "-F"):
            f, i, d = parse_filelist(base / next(it))
            files += f; incs += i; defs.update(d)
        elif t.startswith("-") or t.startswith("+"):
            continue
        else:
            files.append(base / t)
    return files, incs, defs


def resolve_sources(cfg: Config, src: Sources | None = None) -> list[Path]:
    src = src or cfg.project.sources
    root = cfg.root
    files = _glob(root, src.glob)
    if src.filelist:
        files += parse_filelist(root / src.filelist)[0]
    out, seen = [], set()
    for f in files:
        rel = os.path.relpath(f, root)
        if any(fnmatch.fnmatch(rel, pat) for pat in src.exclude):
            continue
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def include_dirs(cfg: Config) -> list[Path]:
    dirs = [cfg.root / d for d in cfg.project.include]
    if cfg.project.sources.filelist:
        dirs += parse_filelist(cfg.root / cfg.project.sources.filelist)[1]
    return [d.resolve() for d in dirs]


def source_hash(cfg: Config, extra_files: list[Path] | None = None) -> str:
    """Content hash of design + testbench sources, include-dir headers, svkit.toml and
    any flow scripts. Part of the cache key."""
    h = hashlib.sha256()
    files = set(resolve_sources(cfg)) | set(resolve_sources(cfg, cfg.project.tb_sources))
    for d in include_dirs(cfg):
        if d.is_dir():
            files |= {p.resolve() for p in d.iterdir() if p.suffix in (".svh", ".vh", ".sv", ".v")}
    for f in extra_files or []:
        if f.is_file():
            files.add(f.resolve())
    for f in sorted(files):
        h.update(str(f.relative_to(cfg.root) if f.is_relative_to(cfg.root) else f).encode())
        h.update(b"\0")
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def flow_files(cfg: Config, *names: Optional[str]) -> list[Path]:
    """Files whose contents affect a flow (Makefile, scripts) — added to the source hash."""
    out = [cfg.root / "svkit.toml"]
    for cand in ("Makefile", "makefile", "GNUmakefile"):
        if (cfg.root / cand).exists():
            out.append(cfg.root / cand)
    for n in names:
        if n:
            out.append(cfg.root / n)
    return out


def cache_key(kind: str, adapter: str, src_hash: str, knobs: dict[str, Any], test: str | None = None) -> str:
    blob = json.dumps({"k": kind, "a": adapter, "s": src_hash, "n": knobs, "t": test}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def git_info(root: Path) -> tuple[Optional[str], Optional[bool]]:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=10)
        if sha.returncode != 0:
            return None, None
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                               capture_output=True, text=True, timeout=30)
        return sha.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def make_workdir(cfg: Config, dest: Path) -> Path:
    """Isolated copy of the project tree (excluding .git/.svkit/[project].workdir_exclude).

    Files are real copies, never hard links: tools often rewrite outputs in place
    (O_TRUNC), which would write through a hard link into the user's repo. Large
    output dirs should be listed in [project].workdir_exclude.
    """
    root = cfg.root
    excl = set(cfg.project.workdir_exclude)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE
                       and not _excluded(os.path.normpath(os.path.join(rel, d)), excl)]
        tgt = dest / rel
        tgt.mkdir(parents=True, exist_ok=True)
        for fn in filenames:
            r = os.path.normpath(os.path.join(rel, fn))
            if _excluded(r, excl) or is_smb_placeholder(fn):
                continue
            s = Path(dirpath) / fn
            if s.is_symlink():
                os.symlink(os.readlink(s), tgt / fn)
                continue
            copy_file(s, tgt / fn)
    return dest


def _excluded(rel: str, excl: set[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) or rel == pat.rstrip("/") for pat in excl)


def flow_vars(cfg: Config, *, test: str | None = None, workdir: Path | None = None,
              run_dir: Path | None = None, **extra: Any) -> dict[str, Any]:
    return {"test": test, "top": cfg.project.top, "root": str(cfg.root),
            "workdir": str(workdir) if workdir else None, "run_dir": str(run_dir) if run_dir else None, **extra}


def tmpl(s: str, cfg: Config, **kw: Any) -> str:
    return expand(s, **flow_vars(cfg, **kw))
