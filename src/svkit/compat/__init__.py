"""Host compatibility: run a project's own build on hosts that lack its tools.

* `tool_shims()`  — a PATH directory with a `vcs` stand-in that compiles with Verilator
                    (see svkit.compat.vcs_shim). Used when a wrap flow needs VCS and VCS is absent.
* `make_env()`    — when a Makefile sets `SHELL := $(SHELL) <bash options>` (e.g. `-o pipefail`)
                    and /bin/sh is not bash (Debian/Ubuntu dash), run make with
                    MAKEFLAGS="SHELL=<wrapper>" whose wrapper runs bash with those options.
                    The Makefile itself is never edited.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path

_SHELL_RE = re.compile(r"^\s*(?:override\s+)?SHELL\s*[:+?]?=\s*(?:\$[({]SHELL[)}]|/bin/sh)\s*(.*?)\s*$", re.M)


def sh_is_bash() -> bool:
    try:
        return Path(os.path.realpath("/bin/sh")).name == "bash"
    except OSError:
        return False


def makefile_shell_flags(cwd: Path) -> list[str] | None:
    """Options a Makefile appends to /bin/sh via `SHELL := $(SHELL) <opts>` (None if it does not)."""
    for name in ("GNUmakefile", "makefile", "Makefile"):
        f = cwd / name
        if f.is_file():
            m = _SHELL_RE.search(f.read_text(errors="replace"))
            if m and m.group(1):
                return shlex.split(m.group(1))
            return None
    return None


def _write_exec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text() != text:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.chmod(0o755)
        os.replace(tmp, path)
    return path


def make_env(cwd: Path, shim_root: Path) -> tuple[dict[str, str], str | None]:
    """Env additions so `make` in `cwd` works on a dash host. Returns (env, note)."""
    if sh_is_bash():
        return {}, None
    flags = makefile_shell_flags(cwd)
    bash = shutil.which("bash")
    if not flags or not bash:
        return {}, None
    wrapper = shim_root / ("sh-" + "_".join(re.sub(r"\W", "", f) for f in flags))
    _write_exec(wrapper, "#!/bin/sh\nexec " + " ".join(shlex.quote(x) for x in [bash, *flags]) + ' "$@"\n')
    prev = os.environ.get("MAKEFLAGS", "")
    env = {"MAKEFLAGS": (prev + " " if prev else "") + f"SHELL={wrapper}"}
    note = (f"/bin/sh is not bash: running make with SHELL=bash {' '.join(flags)} "
            f"(the Makefile's `SHELL := $(SHELL) {' '.join(flags)}` assumes bash)")
    return env, note


def tool_shims(shim_root: Path, tools: list[str]) -> Path:
    """Directory with stand-in executables for `tools` (currently: vcs)."""
    d = shim_root / "bin"
    py = sys.executable
    for t in tools:
        if t == "vcs":
            _write_exec(d / "vcs", f"#!/bin/sh\nexec {shlex.quote(py)} -m svkit.compat.vcs_shim \"$@\"\n")
        else:
            raise ValueError(f"no shim for {t}")
    return d


# ---------------------------------------------------------------- locating open-source tools
import subprocess  # noqa: E402

OSS_TOOLS = ("verilator", "yosys", "sta", "iverilog")
_LOCATE_CACHE: dict[tuple[str, str], str | None] = {}


def search_dirs() -> list[Path]:
    """Where user-local EDA installs usually live (no root needed). $SVKIT_TOOL_PATH comes first."""
    home = Path.home()
    extra = [Path(p) for p in os.environ.get("SVKIT_TOOL_PATH", "").split(os.pathsep) if p]
    return extra + [home / "tools" / "bin", home / "tools" / "oss-cad-suite" / "bin", home / ".local" / "bin",
                    home / "oss-cad-suite" / "bin", Path("/opt/oss-cad-suite/bin"), Path("/usr/local/bin")]


def _candidates(name: str) -> list[str]:
    out: list[str] = []
    for d in os.environ.get("PATH", "").split(os.pathsep) + [str(p) for p in search_dirs()]:
        f = Path(d) / name
        if d and f.is_file() and os.access(f, os.X_OK):
            r = str(f)
            if r not in out:
                out.append(r)
    return out


def _verilator_ok(exe: str) -> bool:
    """Runs, and is Verilator 5+ (needed for --binary --timing). A stale VERILATOR_ROOT is ignored."""
    env = {k: v for k, v in os.environ.items() if k != "VERILATOR_ROOT"}
    try:
        out = subprocess.run([exe, "--version"], env=env, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    m = re.search(r"Verilator\s+(\d+)\.", out)
    return bool(m and int(m.group(1)) >= 5)


def _works(name: str, exe: str) -> bool:
    if name == "verilator":
        return _verilator_ok(exe)
    return True


def locate(name: str) -> str | None:
    """First working executable for an open-source tool: PATH order, then search_dirs()."""
    key = (name, os.environ.get("PATH", ""))
    if key not in _LOCATE_CACHE:
        _LOCATE_CACHE[key] = next((c for c in _candidates(name) if _works(name, c)), None)
    return _LOCATE_CACHE[key]


def pin_tools(cfg) -> list[str]:
    """Point [tools] at working open-source tools when the PATH one is missing or broken (e.g. an
    old system Verilator ahead of OSS CAD Suite). Explicit [tools] entries are kept. Returns notes."""
    notes = []
    for name in OSS_TOOLS:
        if name in cfg.tools:
            continue
        found = locate(name)
        if found and found != shutil.which(name):
            cfg.tools[name] = found
            notes.append(f"using {found} for {name}")
    return notes
