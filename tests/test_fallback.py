"""Open-source fallback when Synopsys tools are missing (svkit.compat + runner flow resolution)."""
import os
import shutil
import textwrap
from pathlib import Path

import pytest

from svkit import compat
from svkit import config as C
from svkit.compat import vcs_shim as S
from svkit.core.runner import Runner, backends, resolve_sim, resolve_synth


# ---------------------------------------------------------------- vcs argument translation
def test_parse_vcs_args(tmp_path):
    (tmp_path / "files.f").write_text("rtl/a.sv\n// comment\n+incdir+inc2\n")
    p = S.parse_args(["-sverilog", "-full64", "+lint=TFIPC-L", "-timescale=1ns/100ps", "+incdir+inc+inc3",
                      "-f", "files.f", "tb/tb.sv", "dpi/print.c", "hdr/defs.svh", "+define+A=1+B",
                      "-top", "tb", "-debug_access+all", "-o", "build/simv"], tmp_path)
    assert p.out == "build/simv" and p.top == "tb" and p.timescale == "1ns/100ps"
    assert p.sources == ["rtl/a.sv", "tb/tb.sv"]
    assert p.csources == ["dpi/print.c"]
    assert p.defines == ["A=1", "B"]
    assert p.incdirs == ["inc", "inc3", "inc2", "hdr"]
    assert "-sverilog" in p.ignored and "+lint=TFIPC-L" in p.ignored


def test_rewrite_vcs_system_tasks():
    src = textwrap.dedent("""\
        initial begin
          $vcdplusfile({name, ".vpd"});
          $vcdpluson(0, tb);
          $vcdplusmemon;
          $fsdbDumpvars(0, tb);
          $vcdplusoff;
        end
        """)
    out = S.rewrite_vcs_tasks(src)
    assert '$dumpfile({name, ".vpd"});' in out and "$dumpvars(0, tb);" in out and "$dumpoff;" in out
    assert "vcdplus" not in out and "fsdb" not in out


def test_vpd2vcd_copies_vcd_text(tmp_path):
    (tmp_path / "w.vpd").write_text("$version x $end\n$timescale 1ns $end\n")
    assert S.vpd2vcd(["+splitpacked", "w.vpd", "w.vcd"], tmp_path) == 0
    assert (tmp_path / "w.vcd").read_text().startswith("$version")
    (tmp_path / "real.vpd").write_bytes(b"\x00VPD binary")
    assert S.vpd2vcd(["real.vpd", "r.vcd"], tmp_path) == 1


# ---------------------------------------------------------------- make SHELL on dash hosts
def test_makefile_shell_flags(tmp_path):
    (tmp_path / "Makefile").write_text("SHELL := $(SHELL) -o pipefail\nall:\n\ttrue\n")
    assert compat.makefile_shell_flags(tmp_path) == ["-o", "pipefail"]
    (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
    assert compat.makefile_shell_flags(tmp_path) is None


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("make"), reason="needs bash and make")
def test_make_env_runs_bash_options(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(compat, "sh_is_bash", lambda: False)
    (tmp_path / "Makefile").write_text("SHELL := $(SHELL) -o pipefail\nall:\n\t@true | false | true; echo rc=$$?\n")
    env, note = compat.make_env(tmp_path, tmp_path / "shims")
    assert note and "pipefail" in note
    out = subprocess.run(["make", "-s"], cwd=tmp_path, env={**os.environ, **env}, capture_output=True, text=True)
    assert "rc=1" in out.stdout, out.stdout + out.stderr      # pipefail honoured under bash


# ---------------------------------------------------------------- flow resolution
TOML = """
[project]
name = "fb"
top = "top"
tb_top = "tb"
sources = { glob = ["rtl/*.sv"] }
tb_sources = { glob = ["tb/*.sv"] }

[tools]
vcs = "svkit-no-such-vcs"
dc_shell-t = "svkit-no-such-dc"
%s

[flows.sim.vcs]
cmd = "make run TEST={test}"
tests = { names = ["t1"] }
%s

[flows.synth.dc]
cmd = "make syn"
"""


def _cfg(tmp_path, tools="", extra=""):
    (tmp_path / "rtl").mkdir(exist_ok=True)
    (tmp_path / "rtl" / "top.sv").write_text("module top(input logic clock, input logic a, output logic y);\n"
                                            "  always_ff @(posedge clock) y <= a;\nendmodule\n")
    (tmp_path / "svkit.toml").write_text(TOML % (tools, extra))
    return C.load(tmp_path)


def test_resolve_sim_prefers_another_configured_flow(tmp_path):
    cfg = _cfg(tmp_path, tools='verilator = "true"', extra='[flows.sim.verilator]\nmode = "builtin"\n')
    rs = resolve_sim(cfg, None)
    assert rs.name == "verilator" and rs.label == "verilator" and "instead of 'vcs'" in rs.note
    rs = resolve_sim(cfg, "vcs")                 # asked for by name: keep the vcs build, shim the compiler
    assert rs.name == "vcs" and rs.shims == ["vcs"]


def test_resolve_sim_shims_vcs_in_wrap_flow(tmp_path):
    cfg = _cfg(tmp_path, tools='verilator = "true"')
    rs = resolve_sim(cfg, None)
    assert rs.name == "vcs" and rs.label == "verilator" and rs.shims == ["vcs"] and "Verilator" in rs.note


def test_fallback_off(tmp_path):
    cfg = _cfg(tmp_path, tools='verilator = "true"')
    cfg.flows.fallback = False
    rs = resolve_sim(cfg, None)
    assert rs.label == "vcs" and rs.note is None


def test_resolve_synth_uses_yosys_with_generic_library(tmp_path):
    cfg = _cfg(tmp_path, tools='yosys = "true"\nsta = "true"')
    rs = resolve_synth(cfg, None)
    assert rs.name == "yosys" and rs.flow.mode == "builtin" and rs.flow.sta == "opensta"
    assert rs.cfg.library.liberty and rs.cfg.library.liberty[0].endswith("svkit_tiny.lib")
    assert Path(rs.cfg.library.liberty[0]).is_file()          # shipped in the package
    assert "demo library" in rs.note and not cfg.library.liberty   # the user's config is not modified
    b = backends(cfg)
    assert b["synth"]["runs_on"] == "yosys" and b["synth"]["note"]


def test_resolve_synth_yosys_without_opensta(tmp_path):
    cfg = _cfg(tmp_path, tools='yosys = "true"\nsta = "svkit-no-such-sta"')
    rs = resolve_synth(cfg, None)
    assert rs.name == "yosys" and rs.flow.sta is None
    assert "no timing" in rs.note and "opensta" not in rs.cfg.flows.sta


def test_locate_finds_pip_yosys(tmp_path, monkeypatch):
    exe = tmp_path / "yowasp-yosys"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(compat, "search_dirs", lambda: [])
    assert compat.locate("yosys") == str(exe)


# ---------------------------------------------------------------- end to end (real Verilator / Yosys)
VCS_MAKEFILE = """\
SHELL := $(SHELL) -o pipefail
build/simv: tb/tb.sv rtl/top.sv dpi/hello.c
\tmkdir -p build
\tvcs -sverilog -full64 +lint=TFIPC-L -timescale=1ns/1ps tb/tb.sv rtl/top.sv dpi/hello.c +define+W=4 -o $@
out/%.out: build/simv
\tmkdir -p out
\tbuild/simv +OUT=out/$* > out/$*.log
\tvcs -vpd2vcd +splitpacked out/$*.vpd out/$*.vcd && rm out/$*.vpd
run: out/$(TEST).out
"""

TB = """\
`timescale 1ns/1ps
import "DPI-C" function int hello(input int x);
module tb;
  logic clock = 0, a = 0, y;
  string name;
  integer f;
  top dut(.clock(clock), .a(a), .y(y));
  always #5 clock = ~clock;
  initial begin
    if (!$value$plusargs("OUT=%s", name)) $finish;
    $vcdplusfile({name, ".vpd"});
    $vcdpluson(0, tb);
    f = $fopen({name, ".out"}, "w");
    a = 1; @(posedge clock); @(posedge clock); #1;
    $fdisplay(f, "y=%0d dpi=%0d w=%0d", y, hello(20), `W);
    $fclose(f);
    $finish;
  end
endmodule
"""


@pytest.mark.oss
@pytest.mark.slow
@pytest.mark.skipif(bool(shutil.which("vcs")), reason="VCS is installed: no fallback to test")
def test_vcs_wrap_flow_runs_on_verilator(tmp_path):
    root = tmp_path / "proj"
    (root / "tb").mkdir(parents=True)
    (root / "dpi").mkdir()
    (root / "tb" / "tb.sv").write_text(TB)
    (root / "dpi" / "hello.c").write_text('#include "svdpi.h"\nint hello(int x) { return x + 22; }\n')
    (root / "Makefile").write_text(VCS_MAKEFILE)
    (root / "expected").mkdir()
    (root / "expected" / "t1.out").write_text("y=1 dpi=42 w=4\n")
    cfg = _cfg(root, extra='build_cmd = "make build/simv"\nwaves = "out/{test}.vcd"\n'
                           'golden = { actual = "out/{test}.out", expected = "expected/{test}.out" }\n')
    cfg.tools.pop("verilator", None)
    r = Runner(cfg)
    assert r.cfg.tool("verilator")      # a working Verilator 5 was located
    gid, runs = r.sim_all(jobs=1, use_cache=False)
    run = runs[0]
    assert run.status == "passed", (run.message, (r.db.run_dir(run.id) / "svkit.log").read_text()[-3000:])
    assert run.adapter == "verilator" and run.metrics["tool"] == "verilator"
    assert run.metrics["waves"] == "waves.fst"          # archived as FST ([waves].archive default)
    assert any(w["code"] == "FALLBACK" for w in run.metrics["warnings"])
