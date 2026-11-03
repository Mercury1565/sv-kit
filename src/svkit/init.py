"""`svkit init`: scan a project and write a best-guess, commented `svkit.toml`.

Generic heuristics only — nothing here may know about a particular project:
  * HDL files      -> modules, instantiations -> design top (never instantiated, not a testbench)
                      and testbench top; source globs per directory; include dirs (*.svh/*.vh dirs)
  * Makefile       -> rule targets and %-pattern rules (sim-like, synth-like, check-like), the
                      tools it mentions (vcs, verilator, dc_shell, yosys), clock-period files /
                      env vars, liberty files, a TEST-style variable
  * *.f            -> filelist for sources
  * TCL scripts    -> DC-like scripts (compile + reports); `set <var> <value>` lines -> tcl_var knobs;
                      a target-library glob over the corner variable -> corner experiments;
                      `-lib_cell` names -> Vt suffix map
  * golden dirs    -> expected / golden / *correct* / reference dirs -> golden + test names; a
                      `%.check: out/%.x ref/%.x` rule names actual + expected even before make has
                      generated the reference dir (then the sim command builds the golden too)
  * test inputs    -> programs/tests dirs -> tests glob
  * outputs        -> existing .vcd/.cpi/.rep/.vg files -> waves, perf, report paths
Anything uncertain is written with a `# TODO` comment. Never overwrites without `force`.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from svkit.config import CONFIG_NAME

SKIP_DIRS = {".git", ".svkit", ".venv", "node_modules", "__pycache__", "obj_dir", "csrc", "simv.daidir",
             "build", "DVEfiles", "verdiLog", "work", "INCA_libs", "xcelium.d"}
HDL_EXT = {".sv", ".v", ".svh", ".vh"}
MAX_FILES = 20000
KEYWORDS = {"module", "endmodule", "begin", "end", "if", "else", "for", "case", "assign", "always", "always_ff",
            "always_comb", "always_latch", "initial", "function", "task", "input", "output", "inout", "wire",
            "reg", "logic", "bit", "int", "integer", "parameter", "localparam", "typedef", "struct", "enum",
            "return", "import", "export", "package", "endpackage", "generate", "genvar", "endgenerate",
            "interface", "modport", "class", "virtual", "string", "while", "repeat", "forever", "default",
            "unique", "priority", "posedge", "negedge", "or", "and", "not", "fork", "join", "automatic",
            "static", "const", "var", "signed", "unsigned", "void", "real", "time", "event", "property",
            "assert", "assume", "cover", "sequence", "covergroup", "foreach", "do", "wait", "disable",
            "defparam", "specify", "supply0", "supply1", "tri", "casez", "casex", "endcase", "endfunction",
            "endtask", "endinterface", "endclass", "case", "elsif", "ifdef", "ifndef", "endif", "define"}
TB_NAME = re.compile(r"(^|[_\W])(tb|test|tests|testbench|bench|tst)([_\W]|$)", re.I)
SIM_WORDS = re.compile(r"(sim|run|test|\bout\b|\.out$)", re.I)
SYN_WORDS = re.compile(r"(syn|synth|netlist|\.vg$|\.ddc$)", re.I)
CHECK_WORDS = re.compile(r"(check|diff|compare|verify)", re.I)


@dataclass
class Module:
    name: str
    file: Path
    ports: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    insts: set[str] = field(default_factory=set)
    has_initial: bool = False


@dataclass
class Scan:
    root: Path
    files: list[Path] = field(default_factory=list)
    modules: dict[str, Module] = field(default_factory=dict)
    make_text: str = ""
    make_targets: list[str] = field(default_factory=list)
    make_vars: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- scanning
def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        if Path(dp).relative_to(root).parts.__len__() > 6:
            dns[:] = []
        for fn in sorted(fns):
            out.append(Path(dp) / fn)
            if len(out) >= MAX_FILES:
                return out
    return out


def _strip_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    return re.sub(r"//[^\n]*", "", t)


def parse_hdl(path: Path) -> list[Module]:
    try:
        text = _strip_comments(path.read_text(errors="replace"))
    except OSError:
        return []
    mods = []
    for m in re.finditer(r"\bmodule\s+(?:automatic\s+|static\s+)?(\w+)(.*?)\bendmodule\b", text, re.S):
        name, body = m.group(1), m.group(2)
        mod = Module(name=name, file=path)
        hdr_end = body.find(";")
        header = body[:hdr_end] if hdr_end >= 0 else ""
        mod.inputs = re.findall(r"\binput\s+(?:wire\s+|logic\s+|reg\s+|var\s+)?(?:signed\s+)?(?:\[[^\]]*\]\s*)*(\w+)",
                                body)
        mod.ports = re.findall(r"\b(?:input|output|inout)\b[^,;)]*?(\w+)\s*(?=[,;)]|$)", header) or mod.inputs
        mod.has_initial = bool(re.search(r"\binitial\b", body))
        for im in re.finditer(r"^\s*(\w+)\s*(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?(\w+)\s*(?:\[[^\]]*\]\s*)?\(",
                              body, re.M):
            t, inst = im.group(1), im.group(2)
            if t not in KEYWORDS and inst not in KEYWORDS:
                mod.insts.add(t)
        mods.append(mod)
    return mods


def parse_makefile(text: str) -> tuple[list[str], dict[str, str]]:
    targets, vars_ = [], {}
    joined = re.sub(r"\\\n", " ", text)
    for line in joined.splitlines():
        if line.startswith("\t") or line.lstrip().startswith("#"):
            continue
        mv = re.match(r"^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*(?:\?|:|::|\+)?=\s*(.*)$", line)
        if mv:
            vars_[mv.group(1)] = mv.group(2).strip()
            continue
        mt = re.match(r"^([^\s:=#][^:=]*?)\s*::?(?!=)", line)
        if mt:
            for t in mt.group(1).split():
                if t.startswith(".") and t.isupper() or t in (".PHONY", ".SUFFIXES", ".PRECIOUS", ".SECONDARY"):
                    continue
                targets.append(t)
    return list(dict.fromkeys(targets)), vars_


def _strip_make_comments(text: str) -> str:
    """Drop whole-line comments: usage notes like `# make cpu.vg` must not look like real paths."""
    return re.sub(r"(?m)^[ \t]*#.*$", "", text)


def scan(root: Path) -> Scan:
    s = Scan(root=root)
    s.files = _walk(root)
    for f in s.files:
        if f.suffix in (".sv", ".v"):
            for m in parse_hdl(f):
                s.modules.setdefault(m.name, m)
    for cand in ("Makefile", "makefile", "GNUmakefile"):
        if (root / cand).is_file():
            s.make_text = _strip_make_comments((root / cand).read_text(errors="replace"))
            # follow plain `include file.mk` lines (one level, project-local files only)
            for inc in re.findall(r"^-?include\s+([^\s$]+)\s*$", s.make_text, re.M):
                f = root / inc
                if f.is_file() and f.resolve().is_relative_to(root.resolve()):
                    s.make_text += "\n" + _strip_make_comments(f.read_text(errors="replace"))
            s.make_targets, s.make_vars = parse_makefile(s.make_text)
            break
    return s


# ---------------------------------------------------------------------------- inference
def _rel(p: Path, root: Path) -> str:
    return os.path.relpath(p, root)


def _is_tb(m: Module, root: Path) -> bool:
    rel = _rel(m.file, root)
    if TB_NAME.search(m.name) or TB_NAME.search(Path(rel).stem):
        return True
    if any(TB_NAME.search(part) for part in Path(rel).parts[:-1]):
        return True
    return not m.ports and m.has_initial


def pick_tops(s: Scan) -> tuple[Optional[str], Optional[str], list[str]]:
    mods = s.modules
    instantiated = {t for m in mods.values() for t in m.insts if t in mods}
    roots = [m for n, m in mods.items() if n not in instantiated]
    tbs = [m for m in mods.values() if _is_tb(m, s.root)]
    design_roots = [m for m in roots if m not in tbs]

    def size(name: str, seen=None) -> int:
        seen = seen or set()
        if name in seen or name not in mods:
            return 0
        seen.add(name)
        return 1 + sum(size(c, seen) for c in mods[name].insts)

    top = None
    # prefer the module the testbench instantiates
    tb_roots = [m for m in roots if m in tbs]
    for tb in sorted(tb_roots, key=lambda m: -size(m.name)):
        dut = [c for c in tb.insts if c in mods and mods[c] not in tbs]
        if dut:
            top = max(dut, key=size)
            break
    if top is None and design_roots:
        top = max(design_roots, key=lambda m: size(m.name)).name
    tb_top = None
    if tb_roots:
        cands = [m for m in tb_roots if top in m.insts] or tb_roots
        tb_top = max(cands, key=lambda m: size(m.name)).name
    return top, tb_top, [m.name for m in tbs]


def source_globs(s: Scan, names: set[str], exclude_files: set[Path]) -> list[str]:
    """Directory/extension globs covering the files that define `names`."""
    files = {s.modules[n].file for n in names if n in s.modules} - exclude_files
    # packages / headers in the same dirs count too
    dirs = Counter()
    for f in files:
        dirs[(_rel(f.parent, s.root), f.suffix)] += 1
    globs = []
    for (d, ext), _ in sorted(dirs.items()):
        g = f"{d}/*{ext}" if d != "." else f"*{ext}"
        # don't let a glob swallow testbench files
        matched = {p for p in s.root.glob(g)}
        if matched & exclude_files:
            globs += sorted(_rel(f, s.root) for f in files if f.parent == s.root / d and f.suffix == ext)
        else:
            globs.append(g)
    return globs


def _hier(s: Scan, top: str) -> set[str]:
    out, stack = set(), [top]
    while stack:
        n = stack.pop()
        if n in out or n not in s.modules:
            continue
        out.add(n)
        stack += list(s.modules[n].insts)
    return out


def _guess_signal(names: list[str], rx: str) -> Optional[str]:
    c = [n for n in names if re.search(rx, n, re.I)]
    return sorted(c, key=len)[0] if c else None


@dataclass
class Guess:
    lines: list[str] = field(default_factory=list)

    def add(self, *ls: str) -> None:
        self.lines += ls


def _q(v) -> str:
    return json.dumps(v)


def _lit(rx: str) -> str:
    return "'" + rx + "'" if "'" not in rx else json.dumps(rx)


def _dir_named(s: Scan, rx: str) -> list[Path]:
    seen = []
    for f in s.files:
        rel = Path(_rel(f, s.root))
        for i, part in enumerate(rel.parts[:-1]):
            if re.search(rx, part, re.I):
                d = s.root.joinpath(*rel.parts[:i + 1])
                if d not in seen:
                    seen.append(d)
    return seen


def _files_in(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []


def _make_test_var(s: Scan) -> Optional[str]:
    for v in s.make_vars:
        if re.fullmatch(r"(TEST|TESTNAME|TEST_NAME|PROG|PROGRAM|TESTCASE)", v, re.I):
            return v
    return None


def _pattern_targets(s: Scan) -> list[str]:
    return [t for t in s.make_targets if "%" in t]


def _time_regex(d: Path, ext: str) -> Optional[tuple[str, str]]:
    """If (nearly) every golden line carries a leading cycle/time stamp, return (regex, unit)."""
    lines = []
    for f in _files_in(d)[:5]:
        if f.suffix == ext:
            lines += [l for l in f.read_text(errors="replace").splitlines()[:200] if l.strip()]
    if not lines:
        return None
    for rx, unit in ((r"^@(\d+)", "cycle"), (r"^\s*(?:cycle|cyc)\s*[=:]?\s*(\d+)", "cycle"),
                     (r"^\s*(?:time|t)\s*[=:]?\s*(\d+)", "ticks"), (r"^\s*\[\s*(\d+)\s*\]", "ticks")):
        hits = sum(1 for l in lines if re.search(rx, l, re.I))
        if hits >= 0.8 * len(lines):
            return rx, unit
    return None


def _joined_lines(s: Scan) -> list[str]:
    return re.sub(r"\\\n", " ", s.make_text).splitlines()


def _check_rule(s: Scan) -> Optional[tuple[str, str]]:
    """(actual, expected) from a check-like pattern rule whose prerequisites are two files with the
    same extension in different dirs, e.g. `%.check: output/%.wb output_correct/%.wb`."""
    for line in _joined_lines(s):
        if line.startswith("\t"):
            continue
        m = re.match(r"^(\S*%\S*)\s*::?(?!=)\s*([^;#]*)", line)
        if not m or not CHECK_WORDS.search(m.group(1)):
            continue
        pre = [t for t in m.group(2).split() if "%" in t and "/" in t and "$" not in t]
        by_ext: dict[str, list[str]] = {}
        for t in pre:
            by_ext.setdefault(Path(t).suffix, []).append(t)
        for ext, ts in by_ext.items():
            if ext and len(ts) == 2:
                ref = [t for t in ts if re.search(r"correct|expected|golden|ref", t, re.I)]
                exp = ref[0] if len(ref) == 1 else ts[1]
                act = ts[0] if exp == ts[1] else ts[1]
                return act.replace("%", "{test}"), exp.replace("%", "{test}")
    return None


def _check_recipe(s: Scan) -> Optional[tuple[str, str, str]]:
    """(check target, actual, expected) from a check rule whose recipe makes the reference itself
    and diffs it, e.g. `%.check: output/%.out` + `ref > $<.correct` + `diff $< $<.correct`.
    Running that target is then the per-test command: it simulates, builds the reference, diffs."""
    lines = _joined_lines(s)
    for i, line in enumerate(lines):
        m = re.match(r"^(\S*%\S*)\s*::?(?!=)\s*([^;#]*)", line)
        if not m or line.startswith("\t") or not CHECK_WORDS.search(m.group(1)):
            continue
        pre = m.group(2).split()
        recipe = []
        for ln in lines[i + 1:]:
            if not ln.startswith("\t"):
                break
            recipe.append(ln.strip().lstrip("@-+"))
        sub = lambda t: (t.replace("$<", pre[0] if pre else "$<").replace("$*", "%").replace("$(*)", "%"))
        made = {sub(r) for ln in recipe for r in re.findall(r">\s*([^\s;&|]+)", ln)}
        for ln in recipe:
            dm = re.match(r"diff\b(?:\s+-\S+)*\s+(\S+)\s+(\S+)", ln)
            if not dm:
                continue
            a, e = sub(dm.group(1)), sub(dm.group(2))
            if a in made and e not in made:
                a, e = e, a
            if e in made and "%" in a and "%" in e and "$" not in a + e:
                return (m.group(1).removeprefix("./").replace("%", "{test}"),
                        a.replace("%", "{test}"), e.replace("%", "{test}"))
    return None


def _tee_log(s: Scan, top: Optional[str]) -> Optional[str]:
    """Where a dc_shell recipe tees its log, relative to the project root (`cd synth && ... | tee x`)."""
    for line in _joined_lines(s):
        if "dc_shell" not in line or "tee" not in line:
            continue
        pre, _, _ = line.partition("dc_shell")
        tm = re.search(r"\|\s*tee\s+(?:-a\s+)?([\w./$*%{}()-]+)", line)
        if not tm:
            continue
        log = re.sub(r"\$\*|\$\(\*\)|%", "{top}", tm.group(1))
        if top:
            log = re.sub(rf"(?<![\w]){re.escape(top)}(?![\w])", "{top}", log)
        if "$" in log:
            continue
        cds = re.findall(r"\bcd\s+([\w./-]+)\s*(?:&&|;)", pre)
        if cds and not log.startswith("/"):
            log = os.path.normpath(os.path.join(cds[-1], log))
        return log
    return None


def _vt_suffix(tcl_text: str) -> Optional[dict[str, str]]:
    """Vt suffix map from library cell names the DC script mentions (`-lib_cell INVx1_ASAP7_75t_R`)."""
    for cell in re.findall(r"-lib_cell\s+(?:\S+/)?(\w+)", tcl_text):
        if re.search(r"_(RVT|LVT|HVT|SLVT)$", cell):
            return {"_RVT": "RVT", "_LVT": "LVT", "_HVT": "HVT", "_SLVT": "SLVT"}
        if re.search(r"_(R|L|SL)$", cell):
            return {"_R": "RVT", "_L": "LVT", "_SL": "SLVT"}
    return None


def _other_corners(script: Path, root: Path, var: str, current: str) -> list[str]:
    """Corners for which the script's target-library glob over `$var` also finds files."""
    out = []
    text = script.read_text(errors="replace")
    for m in re.finditer(r"\bglob\s+(?:-nocomplain\s+)?([^\]\s]+)", text):
        pat = m.group(1).strip('"{}')
        if not re.search(rf"\$\{{{var}\}}|\${var}\b", pat):
            continue
        for alt in ("SS", "FF", "ss", "ff", "SSG", "FFG", "ssg", "ffg"):
            if alt.upper() == current.upper() or alt.upper() in {o.upper() for o in out}:
                continue
            p = re.sub(rf"\$\{{{var}\}}|\${var}\b", alt, pat)
            bases = [Path(p)] if os.path.isabs(p) else [root / p, script.parent / p]
            if any(glob.glob(str(b)) for b in bases):
                out.append(alt)
    return out


def _perf_regex(sample: str, word: str) -> Optional[str]:
    """Regex capturing the integer next to a `word` label in a perf line, whichever side it is on:
    `Total cycles: 120` -> 'Total\\ cycles\\D*(\\d+)', `133 cycles / 147 instrs` -> '(\\d+)\\s*cycles'.
    A testbench format string works too (`%0d cycles`)."""
    line = re.sub(r"%-?\d*l?[dui]", "0", sample)
    m = re.search(rf"(\d+)[ \t]*({word}\w*)", line, re.I)
    if m:
        return r"(\d+)\s*" + re.escape(m.group(2))
    m = re.search(rf"([A-Za-z_ ]*{word}\w*)[^\d\n]*\d+", line, re.I)
    if m:
        return re.escape(m.group(1).strip()) + r"\D*(\d+)"
    return None


def infer(root: Path) -> tuple[str, list[str]]:
    s = scan(root)
    notes: list[str] = []
    g = Guess()
    mt = s.make_text.lower()
    tcl_files = [f for f in s.files if f.suffix == ".tcl"]
    dc_scripts = []
    for f in tcl_files:
        t = f.read_text(errors="replace")
        if re.search(r"^\s*compile(_ultra)?\b", t, re.M) and re.search(
                r"report_|write\s+-f|write_file|\blink\b|create_clock|target_library", t):
            dc_scripts.append(f)
    has_vcs = bool(re.search(r"\bvcs\b", mt))
    has_verilator = "verilator" in mt
    has_dc = "dc_shell" in mt or bool(dc_scripts)
    has_yosys = "yosys" in mt

    # ---------------------------------------------------------------- project
    top, tb_top, tbs = pick_tops(s)
    if top is None:
        notes.append("no HDL modules found: set [project].top by hand")
    filelists = [f for f in s.files if f.suffix == ".f"]
    tb_files = {s.modules[n].file for n in tbs if n in s.modules}
    design = _hier(s, top) if top else set()
    srcs = source_globs(s, design, tb_files) if design else []
    # packages used by the design live in files without modules: add their dirs' globs
    for f in s.files:
        if f.suffix in (".sv", ".v") and f not in tb_files:
            txt = f.read_text(errors="replace")
            if re.search(r"^\s*package\s+\w+", txt, re.M) and not any(Path(f).match(x) for x in srcs):
                srcs.append(_rel(f, root))
    tb_srcs = sorted({_rel(f, root) for f in tb_files})
    incdirs = sorted({_rel(f.parent, root) for f in s.files if f.suffix in (".svh", ".vh")})
    top_mod = s.modules.get(top) if top else None
    inputs = top_mod.inputs if top_mod else []
    clock = _guess_signal(inputs, r"^(clk|clock)$|clk|clock") or "clock"
    reset = _guess_signal(inputs, r"^(rst|reset)(_n|n|_b)?$|rst|reset")
    name = re.sub(r"[^\w.-]", "_", root.name) or "project"

    g.add("# svkit.toml -- generated by `svkit init`. Review every `# TODO`.",
          "# Reference: README.md (svkit.toml reference). Re-run with `svkit init --force` to regenerate.", "",
          "[project]", f"name = {_q(name)}")
    g.add(f"top = {_q(top or 'TODO_top')}" + ("" if top else "   # TODO: synthesis top module"))
    if tb_top:
        g.add(f"tb_top = {_q(tb_top)}")
    if filelists and not srcs:
        g.add(f"sources = {{ filelist = {_q(_rel(filelists[0], root))} }}")
    else:
        g.add(f"sources = {{ glob = {json.dumps(srcs)} }}" + ("" if srcs else "   # TODO"))
        if filelists:
            g.add(f"# filelist found: {_rel(filelists[0], root)} -- use `sources = {{ filelist = \"...\" }}` instead?")
    if tb_srcs:
        g.add(f"tb_sources = {{ glob = {json.dumps(tb_srcs)} }}")
    if incdirs:
        g.add(f"include = {json.dumps(incdirs)}")
    g.add(f"clock = {_q(clock)}" + ("" if clock in inputs else "   # TODO: clock port name"))
    if reset:
        active = "low" if re.search(r"(_n|n|_b)$", reset) and not reset.endswith("en") else "high"
        g.add(f"reset = {{ name = {_q(reset)}, active = {_q(active)} }}")
    out_dirs = [d for d in ("output", "out", "build", "synth", "syn_out", "obj_dir") if (root / d).is_dir()
                or re.search(rf"(^|[\s/]){d}/", s.make_text)]
    if out_dirs:
        g.add(f"workdir_exclude = {json.dumps(out_dirs)}   # large output dirs not copied into work dirs")

    # ---------------------------------------------------------------- tests + goldens
    golden_dirs = [d for d in _dir_named(s, r"^(expected|golden|goldens|reference|ref)$|correct") if d.is_dir()]
    golden_ext, golden_dir = None, None
    for d in golden_dirs:
        exts = Counter(p.suffix for p in _files_in(d) if p.suffix)
        if exts:
            pref = [e for e in (".wb", ".out", ".log", ".txt") if e in exts]
            golden_ext = pref[0] if pref else exts.most_common(1)[0][0]
            golden_dir = d
            break
    rule = _check_rule(s)
    golden_build = None          # make target that generates the expected file (make skips it when fresh)
    check_cmd = None             # a check target that simulates, builds the reference and diffs
    if rule is None and golden_dir is None and (cr := _check_recipe(s)):
        check_cmd, rule = "make " + cr[0], (cr[1], cr[2])
        golden_ext = "".join(Path(cr[2]).suffixes)
        golden_dir = root / Path(cr[2]).parent
    elif rule and (golden_dir is None or Path(rule[1]).parent == Path(_rel(golden_dir, root))):
        golden_ext, golden_dir = Path(rule[1]).suffix, root / Path(rule[1]).parent
        if any(t.startswith(f"{Path(rule[1]).parent}/%") for t in s.make_targets):
            golden_build = rule[1]
    test_dirs = [d for d in _dir_named(s, r"^(programs?|tests?|test_?progs?|testcases?|stimulus|vectors)$")
                 if d.is_dir() and d not in golden_dirs]
    tests_glob, test_names = None, []
    if test_dirs:
        d = test_dirs[0]
        exts = Counter(p.suffix for p in _files_in(d) if p.suffix)
        tb_text = "".join(s.modules[n].file.read_text(errors="replace") for n in tbs if n in s.modules)
        if exts:
            ment = lambda txt: [e for e in exts if re.search(re.escape(e) + r"(?![\w])", txt)]
            mentioned = ment(tb_text) or ment(s.make_text)
            ext = max(mentioned or exts, key=lambda e: exts[e])
            tests_glob = f"{_rel(d, root)}/*{ext}"
    if golden_dir is not None:
        test_names = sorted(p.stem for p in _files_in(golden_dir) if p.suffix == golden_ext)
    # where does the testbench write its outputs?
    actual = rule[0] if rule and golden_dir == root / Path(rule[1]).parent else None
    if golden_ext and actual is None:
        for d in ("output", "out", "results", "build"):
            if (root / d).is_dir() or re.search(rf"\b{d}/", s.make_text + " ".join(
                    s.modules[n].file.read_text(errors="replace") for n in tbs if n in s.modules)):
                actual = f"{d}/{{test}}{golden_ext}"
                break
        actual = actual or f"{{test}}{golden_ext}"
    waves = None
    for f in s.files:
        if f.suffix in (".vcd", ".fst") and golden_ext is not None:
            waves = f"{_rel(f.parent, root)}/{{test}}{f.suffix}" if f.parent != root else f"{{test}}{f.suffix}"
            break
    if waves is None and actual:
        waves = str(Path(actual).with_suffix(".vcd"))

    # ---------------------------------------------------------------- simulation flows
    test_var = _make_test_var(s)
    pats = _pattern_targets(s)

    def sim_cmd(tool_word: Optional[str]) -> Optional[str]:
        # 1) pattern rule like %.out (one that runs a test, not one that builds a simulator or opens
        #    a GUI)  2) plain target mentioning sim + the tool  3) None
        runs = [p for p in pats if SIM_WORDS.search(p.split("/")[-1]) and not CHECK_WORDS.search(p)
                and not SYN_WORDS.search(p) and not re.search(r"simv|verdi|dve|gui|wave", p, re.I)]
        runs.sort(key=lambda p: (not p.endswith(".out"), "/" in p.removeprefix("./")))
        if runs:
            return "make " + runs[0].removeprefix("./").replace("%", "{test}")
        plain = [t for t in s.make_targets if "%" not in t and "/" not in t
                 and re.search(r"(^|[-_.])(sim|run|simulate)([-_.]|$)", t, re.I)]
        if tool_word:
            pref = [t for t in plain if tool_word in t.lower()]
            plain = pref or [t for t in plain if not re.search(r"vcs|verilator|xrun|questa", t, re.I)] or plain
        if plain:
            return f"make {plain[0]}" + (f" {test_var}={{test}}" if test_var else "")
        return None

    sim_flows = []
    if has_vcs:
        sim_flows.append(("vcs", sim_cmd("vcs")))
    if has_verilator or not has_vcs:
        sim_flows.append(("verilator", sim_cmd("verilator") if has_verilator else None))
    for fname, cmd in sim_flows:
        g.add("", f"[flows.sim.{fname}]")
        if cmd and check_cmd:
            cmd = check_cmd
            notes.append(f"the reference output is made by `{check_cmd}`, so that is the per-test command; "
                         "a failing diff is reported as a golden mismatch")
        if cmd and golden_build:
            cmd += " " + golden_build
            notes.append(f"make generates {_rel(golden_dir, root)}/: the sim command also builds {golden_build}")
        if cmd:
            g.add('mode = "wrap"', f"cmd = {_q(cmd)}")
            simv = [t for t in s.make_targets if "%" not in t and re.search(r"(^|/)simv$", t)]
            if fname == "vcs" and simv:
                g.add(f"build_cmd = {_q('make ' + simv[0])}   # build the simulator once before tests fan out")
        elif fname == "verilator":
            g.add('mode = "builtin"                 # svkit compiles sources + tb_sources with Verilator 5')
        else:
            g.add('mode = "wrap"', 'cmd = "TODO: command that runs one test, e.g. make {test}.out"')
            notes.append(f"no simulation target found for {fname}: fill in [flows.sim.{fname}].cmd")
        if waves:
            g.add(f"waves = {_q(waves)}" + ("" if any(f.suffix in ('.vcd', '.fst') for f in s.files)
                                           else "   # TODO: check the waveform path"))
        if golden_ext and golden_dir is not None:
            exp = _q(_rel(golden_dir, root) + '/{test}' + golden_ext)
            trx = _time_regex(golden_dir, golden_ext)
            if trx:
                g.add(f"golden = {{ actual = {_q(actual)}, expected = {exp}, time_regex = {_lit(trx[0])}, "
                      f"time_unit = {_q(trx[1])} }}   # TODO: check time_unit (cycle or ticks)")
            else:
                g.add(f"golden = {{ actual = {_q(actual)}, expected = {exp} }}"
                      "   # TODO: add time_regex = '...' if lines carry a cycle/time")
        if tests_glob:
            g.add(f"tests = {{ glob = {_q(tests_glob)} }}")
        elif test_names:
            g.add(f"tests = {{ names = {json.dumps(test_names)} }}")
        else:
            g.add('tests = { names = ["default"] }   # TODO: list tests or give a glob')
        if any(f.suffix == ".vpd" for f in s.files) or ".vpd" in s.make_text:
            g.add("# NOTE: the build writes VPD; svkit reads VCD/FST -- point `waves` at a converted VCD")

    # ---------------------------------------------------------------- synthesis flows
    libs = []
    for tok in re.findall(r"[\w./${}()-]+\.lib\b", s.make_text):
        if "$" not in tok and (root / tok).is_file():
            libs.append(tok)
    libs += [_rel(f, root) for f in s.files if f.suffix == ".lib"]
    libs = list(dict.fromkeys(libs))
    synth_targets = [t for t in s.make_targets if SYN_WORDS.search(t)]

    def synth_cmd(tool_word: str) -> Optional[str]:
        for t in synth_targets:
            if "%" in t:
                return "make " + t.replace("%", "{top}")
        pref = [t for t in synth_targets if tool_word in t.lower()]
        rest = [t for t in synth_targets if not re.search(r"dc|yosys|genus", t, re.I)]
        c = pref or rest
        return f"make {c[0]}" if c else None

    text_all = s.make_text + "".join(f.read_text(errors="replace") for f in dc_scripts)

    def report_path(ext: str) -> Optional[str]:
        hits = []
        for tok in re.findall(rf"[\w./$(){{}}%-]*\{ext}\b", text_all):
            t = re.sub(r"\$\((\w+)\)|\$\{(\w+)\}|\$(\w+)|%", "{top}", tok)
            if "{top}" in t or (top and top in t):
                hits.append(t.replace(top, "{top}") if top else t)
        if hits:                          # `synth/cpu.vg` over a bare `%.vg` alias rule
            return next((h for h in hits if "/" in h), hits[0])
        found = [f for f in s.files if f.suffix == ext]
        if found:
            f = found[0]
            return _rel(f.parent, root) + "/{top}" + ext if top and f.stem == top else _rel(f, root)
        return None

    synth_flows = []
    if has_dc:
        synth_flows.append("dc")
    if has_yosys or libs or not has_dc:
        synth_flows.append("yosys")
    for fl in synth_flows:
        g.add("", f"[flows.synth.{fl}]")
        if fl == "dc":
            cmd = synth_cmd("dc")
            script = _rel(dc_scripts[0], root) if dc_scripts else None
            if script is None:
                mk = re.search(r"(?:dc_shell\S*|\$\(\w*DC\w*\))[^\n]*?-f\s+([\w./-]+\.tcl)", s.make_text)
                script = mk.group(1) if mk else None
            if cmd:
                g.add('mode = "wrap"', f"cmd = {_q(cmd)}")
            elif script:
                g.add('mode = "wrap"', f"cmd = {_q('dc_shell-t -f ' + script)}")
            else:
                g.add('mode = "wrap"', 'cmd = "TODO: synthesis command"')
            if script:
                g.add(f"script = {_q(script)}")
            rep = report_path(".rep")
            g.add(f"reports = [{_q(rep)}]" if rep else 'reports = []   # TODO: report files to parse')
            vg = report_path(".vg") or report_path(".v.gz")
            g.add(f"netlist = {_q(vg)}" if vg else '# netlist = "TODO"')
            log = _tee_log(s, top)
            if log:
                g.add(f"log = {_q(log)}")
            g.add("license_seats = 1                 # concurrent dc_shell runs (shared licenses)")
        else:
            g.add('mode = "builtin"                 # generated yosys script on [library].liberty', 'sta = "opensta"')
    if "yosys" in synth_flows:
        g.add("", "[flows.sta.opensta]", "max_paths = 5")

    # ---------------------------------------------------------------- knobs
    knob_lines: list[str] = []
    period_file = None
    for m in re.finditer(r"\$\(shell\s+cat\s+([^\s)]+)\)|\bcat\s+([\w./-]+)", s.make_text):
        f = m.group(1) or m.group(2)
        if (root / f).is_file() and re.fullmatch(r"\s*[\d.]+\s*", (root / f).read_text(errors="replace")):
            period_file = f
            break
    if period_file is None:
        for f in s.files:
            if re.search(r"(clock|clk|period)", f.name, re.I) and f.suffix not in HDL_EXT | {".tcl", ".sdc"} \
                    and f.stat().st_size < 64 and re.fullmatch(r"\s*[\d.]+\s*", f.read_text(errors="replace")):
                period_file = _rel(f, root)
                break
    period_env = None
    m = re.search(r"\b([A-Z_]*(?:CLOCK|CLK)[A-Z_]*PERIOD[A-Z_]*)\b", s.make_text + text_all)
    if m:
        period_env = m.group(1)
    if period_file:
        val = float((root / period_file).read_text().strip())
        unit = "ns" if val < 100 else "ps"
        dflt = val * (1000 if unit == "ns" else 1)
        knob_lines.append(f"clock_period = {{ file = {_q(period_file)}, unit = {_q(unit)}, default = {dflt:g} }}"
                          f"   # TODO: confirm unit ({val:g} read from {period_file})")
        if period_env:
            knob_lines.append(f"# the Makefile also passes ${period_env}; the file knob drives it")
    elif period_env:
        knob_lines.append(f"clock_period = {{ env = {_q(period_env)}, unit = \"ps\" }}   # TODO: unit / default")
    tcl_vars: dict[str, str] = {}
    for f in dc_scripts:
        for mm in re.finditer(r"^[^#\n]*?\bset\s+(\w+)\s+([^\s{}\[\]$;]+)", f.read_text(errors="replace"), re.M):
            tcl_vars.setdefault(mm.group(1), mm.group(2))
    corner_var = next((v for v in tcl_vars if re.search(r"corner|process|pvt", v, re.I)), None)
    if corner_var:
        knob_lines.append(f"corner = {{ tcl_var = {_q(corner_var)}, default = {_q(tcl_vars[corner_var].strip(chr(34)))} }}")
    if dc_scripts:
        t = dc_scripts[0].read_text(errors="replace")
        mc = re.search(r"^\s*(compile(?:_ultra)?\b.*)$", t, re.M)
        if mc:
            knob_lines.append(f"# compile_cmd = {{ tcl_replace = '^\\s*compile(_ultra)?\\b.*$', with = \"{{value}}\" }}"
                              f"   # current: {mc.group(1).strip()}")
    if knob_lines:
        g.add("", "[knobs]", *knob_lines)
    g.add("", "[experiments]", "baseline = {}")
    corners = [c for f in dc_scripts for c in _other_corners(f, root, corner_var, tcl_vars[corner_var].strip('"'))] \
        if corner_var else []
    for c in dict.fromkeys(corners):
        g.add(f"{c.lower()}_corner = {{ corner = {_q(c)} }}")
    if corner_var and not corners:
        g.add(f"# slow_corner = {{ corner = \"TODO\" }}")
    if dc_scripts and re.search(r"^\s*#\s*set_max_fanout", dc_scripts[0].read_text(errors="replace"), re.M):
        g.add('# max_fanout = { tcl_append = "set_max_fanout 8 [current_design]" }')

    # ---------------------------------------------------------------- perf
    cpi_files = [f for f in s.files if f.suffix == ".cpi"]
    cpi_mentioned = ".cpi" in s.make_text or any(
        ".cpi" in s.modules[n].file.read_text(errors="replace") for n in tbs if n in s.modules)
    if cpi_files or cpi_mentioned:
        if cpi_files:
            sample = cpi_files[0].read_text(errors="replace")
        else:   # clean checkout: learn the layout from the testbench's format string
            tb_lines = [ln for f in sorted(tb_files) for ln in f.read_text(errors="replace").splitlines()]
            sample = next((ln for ln in tb_lines if re.search(r"cycle", ln, re.I) and re.search(r"instr", ln, re.I)
                           and "%" in ln), "")
        cyc_rx = _perf_regex(sample, "cycle") or r"cycles\D*(\d+)"
        ins_rx = _perf_regex(sample, "instr") or r"instr\w*\D*(\d+)"
        base = f"{Path(actual).parent}/" if actual and Path(actual).parent != Path(".") else ""
        g.add("", "[perf.source]", f"regex_file = {_q(base + '{test}.cpi')}   # TODO: check path",
              f"cycles = {_lit(cyc_rx)}", f"instrs = {_lit(ins_rx)}")

    # ---------------------------------------------------------------- library
    g.add("", "[library]")
    vt = _vt_suffix("".join(f.read_text(errors="replace") for f in dc_scripts))
    if vt:
        g.add("vt_suffix = { " + ", ".join(f"{_q(k)} = {_q(v)}" for k, v in vt.items()) + " }"
              "   # from the library cell names in the DC script")
    else:
        g.add('# vt_suffix = { "_R" = "RVT", "_L" = "LVT" }   # TODO: cell-name suffix -> Vt class')
    if libs:
        g.add(f"liberty = {json.dumps(libs)}")
    elif "yosys" in synth_flows:
        g.add('liberty = []   # TODO: Liberty (.lib) files for the open-source flow')
        notes.append("no .lib found: set [library].liberty for the yosys/opensta flow")

    if re.search(r"-xprop=tmerge", s.make_text):
        notes.append("VCS runs with -xprop=tmerge; the X tracer models standard Verilog X semantics only, "
                     "so traces through if/case may differ from what VCS shows")
    notes.insert(0, f"top = {top}, tb_top = {tb_top}, {len(s.modules)} modules scanned")
    notes.append(f"sim flows: {', '.join(f for f, _ in sim_flows)}; synth flows: {', '.join(synth_flows)}")
    if tests_glob or test_names:
        notes.append(f"tests: {tests_glob or ', '.join(test_names)}")
    return "\n".join(g.lines) + "\n", notes


def init(root: Path, force: bool = False) -> tuple[Path, list[str]]:
    root = Path(root).resolve()
    out = root / CONFIG_NAME
    if out.exists() and not force:
        raise FileExistsError(f"{out} exists (use --force to overwrite)")
    text, notes = infer(root)
    out.write_text(text)
    # validate what we wrote so the user hears about problems now
    from svkit.config import ConfigError, load
    try:
        load(out)
    except ConfigError as e:
        notes.append(f"WARNING: generated file does not validate yet: {e}")
    return out, notes
