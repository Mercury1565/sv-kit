"""`vcs` stand-in that compiles with Verilator (open-source fallback for wrap flows).

When a project's build calls `vcs ... -o simv` and VCS is not installed, svkit puts a
directory containing a `vcs` script that runs this module at the front of PATH. The user's
Makefile is NOT edited: it still assembles programs, runs `simv +ARGS`, diffs outputs, etc.;
only the compile step is translated:

  vcs args                        -> verilator
  -o <exe>                        -> --binary, executable copied to <exe>
  -f / -F <filelist>              -> expanded inline
  +define+A=1+B                   -> -DA=1 -DB
  +incdir+d1+d2                   -> -Id1 -Id2
  -top X                          -> --top-module X
  -timescale=a/b                  -> --timescale a/b
  -v <file> / -y <dir> / +libext+ -> passed through
  *.sv *.v                        -> sources
  *.svh *.vh                      -> not compiled; their dir is added to the include path
  *.c *.cc *.cpp                  -> DPI C sources (compiled into the model)
  anything else (-sverilog, -full64, -debug_access+all, +lint=..., -kdb, ...) -> ignored

VCS-only system tasks are rewritten in a private copy of the affected sources (never the
user's files): $vcdplusfile -> $dumpfile, $vcdpluson -> $dumpvars, $vcdplusoff -> $dumpoff,
$vcdplusflush -> $dumpflush, $fsdbDumpfile/$fsdbDumpvars likewise; $vcdplusmemon & co. are
dropped. The waveform therefore is a VCD, even when the testbench names it *.vpd; svkit's VCS
adapter detects VCD content and keeps it without conversion.

Verilator is 2-state: X/Z become 0, so X-propagation bugs do not show (use a 4-state
simulator for X-trace).
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SV_EXT = {".sv", ".v", ".svp", ".vp"}
HDR_EXT = {".svh", ".vh", ".svi", ".inc"}
C_EXT = {".c", ".cc", ".cpp", ".cxx"}

_REWRITES = [
    (re.compile(r"\$vcdplusfile\b"), "$dumpfile"),
    (re.compile(r"\$fsdbDumpfile\b"), "$dumpfile"),
    (re.compile(r"\$vcdpluson\b"), "$dumpvars"),
    (re.compile(r"\$fsdbDumpvars\b"), "$dumpvars"),
    (re.compile(r"\$vcdplusoff\b"), "$dumpoff"),
    (re.compile(r"\$vcdplusflush\b"), "$dumpflush"),
    # VCS-only knobs with no VCD equivalent: drop the call, keep the statement terminator
    (re.compile(r"\$(?:vcdplusmemon|vcdplusmemoff|vcdplusdeltacycleon|vcdplusglitchon|vcdplusautoflushon"
                r"|fsdbDumpMDA|fsdbDumpSVA)\b\s*(?:\([^;]*\))?"), ""),
]


class Plan:
    def __init__(self) -> None:
        self.out = "simv"
        self.sources: list[str] = []
        self.csources: list[str] = []
        self.incdirs: list[str] = []
        self.defines: list[str] = []
        self.top: str | None = None
        self.timescale: str | None = None
        self.passthru: list[str] = []
        self.ignored: list[str] = []


def _read_filelist(path: Path) -> list[str]:
    toks: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        line = re.sub(r"//.*$|#.*$", "", line).strip()
        if line:
            toks += shlex.split(line)
    base = path.parent
    out = []
    for t in toks:
        if not t.startswith(("-", "+")) and not os.path.isabs(t) and (base / t).exists():
            t = str(base / t)
        out.append(t)
    return out


def parse_args(argv: list[str], cwd: Path) -> Plan:
    p = Plan()
    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a == "-o" and nxt is not None:
            p.out = nxt
            i += 2
            continue
        if a in ("-f", "-F", "-file") and nxt is not None:
            args[i + 2:i + 2] = _read_filelist(cwd / nxt)
            i += 2
            continue
        if a == "-top" and nxt is not None:
            p.top = nxt
            i += 2
            continue
        if a in ("-v", "-y") and nxt is not None:
            p.passthru += [a, nxt]
            i += 2
            continue
        if a.startswith("-timescale="):
            p.timescale = a.split("=", 1)[1]
        elif a.startswith("+define+"):
            for d in a[len("+define+"):].split("+"):
                if d:
                    p.defines.append(d)
        elif a.startswith("+incdir+"):
            p.incdirs += [d for d in a[len("+incdir+"):].split("+") if d]
        elif a.startswith("+libext+"):
            p.passthru.append(a)
        elif a.startswith(("-", "+")):
            p.ignored.append(a)
        else:
            ext = Path(a).suffix.lower()
            if ext in C_EXT:
                p.csources.append(a)
            elif ext in HDR_EXT:
                d = str(Path(a).parent)
                if d not in p.incdirs:
                    p.incdirs.append(d)
            elif ext in SV_EXT or Path(cwd / a).is_file():
                p.sources.append(a)
            else:
                p.ignored.append(a)
        i += 1
    return p


def rewrite_vcs_tasks(text: str) -> str:
    for rx, rep in _REWRITES:
        text = rx.sub(rep, text)
    return text


def _cache_root() -> Path:
    base = os.environ.get("SVKIT_SHIM_CACHE")
    return Path(base) if base else Path.home() / ".cache" / "svkit" / "vcs-shim"


def build_cmd(p: Plan, cwd: Path, mdir: Path, verilator: str = "verilator") -> list[str]:
    """Verilator argv for a parsed plan (sources needing rewrites are copied into mdir/src)."""
    srcdir = mdir / "src"
    srcs: list[str] = []
    incs: list[str] = []
    for s in p.sources:
        src = (cwd / s).resolve()
        txt = src.read_text(errors="replace")
        new = rewrite_vcs_tasks(txt)
        if new != txt:
            srcdir.mkdir(parents=True, exist_ok=True)
            dst = srcdir / src.name
            dst.write_text(new)
            srcs.append(str(dst))
            if str(src.parent) not in incs:      # keep `include "x"` relative to the original
                incs.append(str(src.parent))
        else:
            srcs.append(str(src))
    for d in p.incdirs:
        incs.append(str((cwd / d).resolve()))
    exe = mdir / "simv"
    cmd = [verilator, "--binary", "--timing", "--trace", "--trace-structs", "-j", "0",
           "-Wno-fatal", "-Wno-lint", "-Wno-style", "--assert", "-O1",
           "--Mdir", str(mdir / "obj"), "-o", str(exe)]
    if p.top:
        cmd += ["--top-module", p.top]
    if p.timescale:
        cmd += ["--timescale", p.timescale]
    cmd += [f"-D{d}" for d in p.defines]
    cmd += [f"-I{d}" for d in dict.fromkeys(incs)]
    cmd += p.passthru
    cmd += srcs
    cmd += [str((cwd / c).resolve()) for c in p.csources if Path(c).suffix.lower() != ".c"]
    return cmd


def compile_c(p: Plan, cwd: Path, mdir: Path, verilator: str) -> tuple[list[str], int]:
    """Compile plain-C DPI files with the C compiler, as VCS does (Verilator would compile them as
    C++, mangling the exported names). Returns object files to link, and an exit code."""
    cs = [c for c in p.csources if Path(c).suffix.lower() == ".c"]
    if not cs:
        return [], 0
    env = {k: v for k, v in os.environ.items() if k != "VERILATOR_ROOT"}
    try:
        root = subprocess.run([verilator, "--getenv", "VERILATOR_ROOT"], env=env, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        root = ""
    cc = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc") or "cc"
    odir = mdir / "cobj"
    odir.mkdir(parents=True, exist_ok=True)
    objs = []
    for c in cs:
        src = (cwd / c).resolve()
        obj = odir / (src.stem + ".o")
        cmd = [cc, "-c", "-O1", "-fPIC", f"-I{src.parent}"]
        if root:
            cmd += [f"-I{root}/include/vltstd", f"-I{root}/include"]
        cmd += [f"-I{(cwd / d).resolve()}" for d in p.incdirs] + [f"-D{d}" for d in p.defines]
        cmd += [str(src), "-o", str(obj)]
        print("[svkit] " + " ".join(shlex.quote(x) for x in cmd), flush=True)
        rc = subprocess.call(cmd, cwd=cwd)
        if rc != 0:
            return objs, rc
        objs.append(str(obj))
    return objs, 0


def vpd2vcd(args: list[str], cwd: Path) -> int:
    """`vcs -vpd2vcd [opts] in.vpd out.vcd`: under the shim the "VPD" is already a VCD -> copy it."""
    files = [a for a in args if not a.startswith(("-", "+"))]
    if len(files) < 2:
        print("svkit vcs-shim: -vpd2vcd needs <in> <out>", file=sys.stderr)
        return 2
    src, dst = cwd / files[0], cwd / files[1]
    try:
        head = src.read_bytes()[:256].lstrip()
    except OSError as e:
        print(f"svkit vcs-shim: {e}", file=sys.stderr)
        return 1
    if not head.startswith((b"$date", b"$version", b"$timescale", b"$comment", b"$scope", b"$var")):
        print(f"svkit vcs-shim: {src} is a real VPD; converting it needs Synopsys vpd2vcd", file=sys.stderr)
        return 1
    shutil.copyfile(src, dst)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cwd = Path.cwd()
    if any(a in ("-ID", "-id", "--version", "-v") for a in argv) and len(argv) == 1:
        print("svkit vcs shim (Verilator backend)")
        return 0
    if argv and argv[0] == "-vpd2vcd":
        return vpd2vcd(argv[1:], cwd)
    p = parse_args(argv, cwd)
    if not p.sources:
        print("svkit vcs-shim: no Verilog/SystemVerilog sources in the vcs command line", file=sys.stderr)
        return 2
    out = (cwd / p.out).resolve()
    mdir = _cache_root() / hashlib.sha256(str(out).encode()).hexdigest()[:16]
    mdir.mkdir(parents=True, exist_ok=True)
    verilator = os.environ.get("SVKIT_SHIM_VERILATOR") or shutil.which("verilator") or "verilator"
    objs, rc = compile_c(p, cwd, mdir, verilator)
    if rc != 0:
        return rc
    cmd = build_cmd(p, cwd, mdir, verilator) + objs
    print("[svkit] vcs not found: compiling with Verilator instead (2-state: X/Z read as 0)", flush=True)
    if p.ignored:
        print(f"[svkit] ignored VCS-only options: {' '.join(p.ignored)}", flush=True)
    print("[svkit] " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    env = dict(os.environ)
    env.pop("VERILATOR_ROOT", None)   # a stale host setting breaks a different verilator install
    rc = subprocess.call(cmd, cwd=cwd, env=env)
    if rc != 0:
        return rc
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() or out.is_symlink():
        out.unlink()
    shutil.copy2(mdir / "simv", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
