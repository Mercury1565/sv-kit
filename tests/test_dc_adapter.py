"""DcAdapter tests with a FAKE dc_shell-t on PATH (run everywhere; no Synopsys tools needed).

They drive the real Runner, so they prove the knob plumbing: knob files, the corner tcl_var
prelude, a compile_ultra tcl_replace and an experiment tcl_append all reach the WORK-DIR copy of
the user's script (kept as the `synth_script.tcl` artifact), while the user's script is untouched.
"""
import shutil
import stat
from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters import registry
from svkit.adapters.synth.dc import DcAdapter
from svkit.core.runner import Runner

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "fixtures" / "golden" / "synopsys" / "samples"
TEMPLATE = REPO / "fixtures" / "synopsys_common" / "syn_dc.tcl"

FAKE_DC = r"""#!/bin/sh
# fake dc_shell-t for svkit tests: emits canned DC reports, never synthesizes anything
script=""
while [ $# -gt 0 ]; do
  case "$1" in -f) script="$2"; shift;; esac
  shift
done
echo "$script" >> "$FAKE_DC_CALLS"
if [ -n "$FAKE_DC_FAIL" ]; then echo "Error: fake failure (FAKE-1)"; exit 1; fi
if [ "$script" = "svkit_dc.tcl" ]; then        # builtin mode: reports/ + netlist.v
  mkdir -p reports
  cp "$FAKE_DC_REPORT" reports/timing.rpt
  echo "module x; endmodule" > netlist.v
  echo "Warning: fake builtin warning. (FAKE-2)"
  exit 0
fi
mkdir -p syn_out
{
  echo "Information: reading $script (FAKE-0)"
  echo "clock_period.txt=$(cat clock_period.txt 2>/dev/null)"
  echo "Warning: Design 'minicore' has '1' unresolved references. (UID-341)"
  echo "Information: The register 'dbg_q_reg[0]' is a constant and will be removed. (OPT-1206)"
} 2>&1 | tee syn_out/minicore.log
cp "$FAKE_DC_REPORT" syn_out/minicore.rep
echo "module minicore(input clock); endmodule" > syn_out/minicore.vg
"""

TOML = r'''
[project]
name = "fake-dc"
top = "minicore"
sources = { glob = ["rtl/*.sv"] }

[flows.synth.dc]
cmd = "dc_shell-t -f {script}"
script = "synopsys/syn.tcl"
reports = ["syn_out/{top}.rep"]
netlist = "syn_out/{top}.vg"
log = "syn_out/{top}.log"
license_seats = 2

[knobs]
clock_period = { file = "clock_period.txt", unit = "ps", default = 2000 }
corner = { tcl_var = "corner" }
compile_cmd = { tcl_replace = '^compile\b.*$', with = "{value}" }

[experiments]
baseline = {}
ss = { corner = "SS" }
ultra = { compile_cmd = "compile_ultra" }
fanout8 = { tcl_append = "set_max_fanout 8 [current_design]" }

[library]
vt_suffix = { "_RVT" = "RVT", "_LVT" = "LVT" }
'''


@pytest.fixture
def fake_dc(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    exe = bindir / "dc_shell-t"
    exe.write_text(FAKE_DC)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_DC_REPORT", str(SAMPLES / "combined_ns.rep"))
    monkeypatch.setenv("FAKE_DC_CALLS", str(tmp_path / "dc_calls.txt"))
    monkeypatch.setenv("SVKIT_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("FAKE_DC_FAIL", raising=False)
    for v in ("SVKIT_DC_TIME_UNIT", "SVKIT_DC_TARGET_LIB", "SVKIT_DC_LIBERTY", "SVKIT_DC_LINK_LIB",
              "SVKIT_DC_SEARCH_PATH"):   # a CAEN shell exports these for the real-DC tests
        monkeypatch.delenv(v, raising=False)
    return tmp_path / "dc_calls.txt"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "rtl").mkdir(parents=True)
    (root / "synopsys").mkdir()
    (root / "rtl" / "minicore.sv").write_text("module minicore(input logic clock); endmodule\n")
    shutil.copy(TEMPLATE, root / "synopsys" / "syn.tcl")
    (root / "svkit.toml").write_text(TOML)
    return root


def test_registry_and_available(project, fake_dc):
    cfg = C.load(project)
    ad = registry.get("dc", cfg)
    assert isinstance(ad, DcAdapter) and ad.available()


def test_wrap_baseline(project, fake_dc):
    cfg = C.load(project)
    run = Runner(cfg).synth(experiment="baseline", use_cache=False)
    assert run.status == "done", run.message
    m = run.metrics
    assert m["tool"] == "dc" and m["top"] == "minicore"
    assert m["period"] == 2000 and m["wns"] == -100.0 and m["tns"] == -150.0
    assert m["corner"] == "ss_0p9v_125c"                       # from report_design (no corner knob set)
    assert m["vt_mix"] == {"LVT": 71, "RVT": 453} and m["cell_count"] == 524
    assert m["netlist"] == "netlist.v"
    assert any(w["code"] == "UID-341" for w in m["warnings"])
    assert m["extra"]["removed_registers"][0]["register"] == "dbg_q_reg[0]"
    rd = project / ".svkit" / "runs" / run.id
    for art in ("minicore.rep", "minicore.timing.rpt", "minicore.reference.rpt", "netlist.v", "synth.log",
                "synth.stdout.log", "synth_script.tcl"):
        assert art in run.artifacts and (rd / art).is_file(), art
    # the knob file reached the work dir (the fake echoed it into the user's log)
    assert "clock_period.txt=2000" in (rd / "synth.log").read_text()
    # work dir cleaned up, user's tree untouched
    assert not (project / ".svkit" / "work" / run.id).exists()
    assert not (project / "syn_out").exists() and not (project / "clock_period.txt").exists()


@pytest.mark.parametrize("exp, must_have, must_not", [
    ("ss", ["set corner SS", "if {![info exists corner]} {set corner TT}"], []),
    ("ultra", ["proc svkit_compile {} {\ncompile_ultra\n}"], ["compile -map_effort medium"]),
    ("fanout8", ["set_max_fanout 8 [current_design]"], []),
])
def test_knobs_reach_workdir_script(project, fake_dc, exp, must_have, must_not):
    original = (project / "synopsys" / "syn.tcl").read_text()
    cfg = C.load(project)
    run = Runner(cfg).synth(experiment=exp, use_cache=False)
    assert run.status == "done", run.message
    used = (project / ".svkit" / "runs" / run.id / "synth_script.tcl").read_text()
    for s in must_have:
        assert s in used, (s, used[:400])
    for s in must_not:
        assert s not in used
    if exp == "ss":
        assert used.index("set corner SS") < used.index("if {![info exists corner]}")
        assert run.metrics["corner"] == "SS"                   # knob value wins over the report
    if exp == "fanout8":
        assert used.rstrip().endswith("set_max_fanout 8 [current_design]")
    # the user's own script is never edited
    assert (project / "synopsys" / "syn.tcl").read_text() == original == TEMPLATE.read_text()


def test_set_clock_period(project, fake_dc):
    cfg = C.load(project)
    run = Runner(cfg).synth(sets={"clock_period": 1500}, use_cache=False)
    assert run.status == "done" and run.metrics["period"] == 1500
    assert "clock_period.txt=1500" in (project / ".svkit" / "runs" / run.id / "synth.log").read_text()


def test_tool_failure_is_an_error_run(project, fake_dc, monkeypatch):
    # a stale report from an earlier (user) run is copied into the work dir: it must NOT be parsed
    (project / "syn_out").mkdir()
    shutil.copy(SAMPLES / "combined_ns.rep", project / "syn_out" / "minicore.rep")
    monkeypatch.setenv("FAKE_DC_FAIL", "1")
    cfg = C.load(project)
    run = Runner(cfg).synth(use_cache=False)
    assert run.status == "error"
    assert "produced no DC reports" in run.message and "exited with code 1" in run.message
    assert (project / "syn_out" / "minicore.rep").is_file()     # user's file left alone


def test_cache_hit(project, fake_dc):
    cfg = C.load(project)
    r = Runner(cfg)
    a = r.synth(experiment="baseline")
    b = r.synth(experiment="baseline")
    assert b.cached_from == a.id
    assert fake_dc.read_text().count("\n") == 1


# ---------------------------------------------------------------- builtin mode
BUILTIN_TOML = r'''
[project]
name = "fake-dc-builtin"
top = "minicore"
clock = "clk"
sources = { glob = ["rtl/*.sv"] }
include = ["inc"]
defines = { WIDTH = 8, FAST = true }

[flows.synth.dc]
mode = "builtin"

[knobs]
clock_period = { tcl_var = "svkit_unused_period", default = 2000 }
corner = { tcl_var = "corner" }
compile_cmd = { tcl_var = "compile_cmd" }

[experiments]
ss = { corner = "SS", compile_cmd = "compile_ultra", tcl_append = "set_max_fanout 8 [current_design]" }

[library]
liberty = ["libs/tiny_tt.db", "libs/tiny_tt.lib"]
corners = { SS = ["libs/tiny_ss.db"] }
vt_suffix = { "_RVT" = "RVT" }
'''


@pytest.fixture
def builtin_project(tmp_path):
    root = tmp_path / "bproj"
    (root / "rtl").mkdir(parents=True)
    (root / "inc").mkdir()
    (root / "rtl" / "minicore.sv").write_text("module minicore(input logic clk); endmodule\n")
    (root / "svkit.toml").write_text(BUILTIN_TOML)
    return root


def test_builtin_generates_script(builtin_project, fake_dc, monkeypatch):
    monkeypatch.setenv("FAKE_DC_REPORT", str(SAMPLES / "timing_ns_met.rpt"))
    cfg = C.load(builtin_project)
    run = Runner(cfg).synth(experiment="ss", use_cache=False)
    assert run.status == "done", run.message
    tcl = (builtin_project / ".svkit" / "runs" / run.id / "synth_script.tcl").read_text()
    assert "set corner SS" in tcl and "set compile_cmd compile_ultra" in tcl
    assert "set target_library {tiny_ss.db}" in tcl and "tiny_tt" not in tcl     # corner picks the libs
    assert "create_clock -name clk -period 2 [get_ports clk]" in tcl            # 2000 ps -> 2 ns
    assert "-define {WIDTH=8 FAST}" in tcl and "elaborate minicore" in tcl
    assert tcl.index("set_max_fanout 8") < tcl.index("eval $compile_cmd")       # appended before compile
    assert "redirect -file reports/qor.rpt {report_qor}" in tcl
    assert "write -format verilog -hierarchy -output netlist.v" in tcl
    m = run.metrics
    assert m["wns"] == 230.0 and m["period"] == 2000 and m["corner"] == "SS" and m["netlist"] == "netlist.v"
    assert any(w["code"] == "FAKE-2" for w in m["warnings"])


def test_builtin_ps_library(builtin_project, fake_dc, monkeypatch):
    text = (builtin_project / "svkit.toml").read_text().replace(
        'mode = "builtin"', 'mode = "builtin"\nenv = { SVKIT_DC_TIME_UNIT = "ps" }')
    (builtin_project / "svkit.toml").write_text(text)
    monkeypatch.setenv("FAKE_DC_REPORT", str(SAMPLES / "timing_ps_violated.rpt"))
    run = Runner(C.load(builtin_project)).synth(use_cache=False)
    tcl = (builtin_project / ".svkit" / "runs" / run.id / "synth_script.tcl").read_text()
    assert "create_clock -name clk -period 2000 " in tcl and "set target_library {tiny_tt.db}" in tcl
    assert run.metrics["wns"] == -51.12


def test_builtin_needs_libraries(builtin_project, fake_dc, monkeypatch):
    text = (builtin_project / "svkit.toml").read_text()
    text = text.replace('liberty = ["libs/tiny_tt.db", "libs/tiny_tt.lib"]', 'liberty = ["libs/tiny_tt.lib"]')
    text = text.replace('corners = { SS = ["libs/tiny_ss.db"] }\n', "")
    (builtin_project / "svkit.toml").write_text(text)
    monkeypatch.delenv("SVKIT_DC_TARGET_LIB", raising=False)
    run = Runner(C.load(builtin_project)).synth(use_cache=False)
    assert run.status == "error" and "needs libraries" in run.message
    # ...unless the environment provides them
    monkeypatch.setenv("SVKIT_DC_TARGET_LIB", "some_lib.db")
    run = Runner(C.load(builtin_project)).synth(use_cache=False)
    assert run.status == "done", run.message
    tcl = (builtin_project / ".svkit" / "runs" / run.id / "synth_script.tcl").read_text()
    assert "set target_library $::env(SVKIT_DC_TARGET_LIB)" in tcl
