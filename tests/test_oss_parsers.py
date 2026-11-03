"""Offline golden-file tests for the open-source flow parsers (no tools needed)."""
import json
import re
import sys
from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters.perf.regex import parse_perf, parse_text
from svkit.adapters.sim.verilator import order_sources, parse_tool_messages, vcd_end_time
from svkit.adapters.sta.opensta import (OpenStaAdapter, build_tcl, liberty_time_unit_ps, parse_power,
                                        parse_report, parse_timing, time_scale_ps)
from svkit.adapters.synth.yosys import (YosysAdapter, area_by_hier, expand_dont_use, liberty_cells,
                                        liberty_for, parse_stat_json)
from svkit.schemas import SynthResult

from conftest import FIXTURES

G = FIXTURES / "golden" / "oss"
LIB = FIXTURES / "lib" / "svkit_tiny.lib"
TOPS = {"counter": "counter", "pipe5": "p5_core"}


# ------------------------------------------------------------------ yosys stat -json
@pytest.mark.parametrize("fx", ["counter", "pipe5"])
def test_yosys_stat_golden(fx):
    stat = json.loads((G / fx / "stat.json").read_text())
    got = parse_stat_json(stat, top=TOPS[fx])
    want = SynthResult.model_validate_json((G / fx / "stat.parsed.json").read_text())
    assert got == want
    exp = json.loads((G / fx / "expected.json").read_text())
    assert got.area_total == exp["area_total"]
    assert got.cell_count == exp["cell_count"] == sum(got.cells_by_type.values())
    assert not any(k.startswith("p5_") for k in got.cells_by_type)     # submodule instances excluded


def test_yosys_area_by_hier_with_netlist_and_local_areas():
    stat = json.loads((G / "pipe5" / "stat.json").read_text())
    netlist = {"modules": {"p5_core": {"cells": {"u_rf": {"type": "p5_regfile"}, "u_alu": {"type": "p5_alu"},
                                                 "u_hz": {"type": "p5_hazard"}, "_1_": {"type": "NAND2_L"}}}}}
    res = parse_stat_json(stat, top="p5_core", netlist=netlist)
    assert res.area_by_hier == {"": 2993.0, "u_alu": 361.0, "u_hz": 49.5, "u_rf": 1110.5}
    # older yosys: module areas are LOCAL -> inclusive area must be summed up the tree
    mods = {"top": {"area": 10.0, "num_cells_by_type": {"mid": 2}},
            "mid": {"area": 3.0, "num_cells_by_type": {"leaf": 1}}, "leaf": {"area": 1.0}}
    assert area_by_hier(mods, top="top", design_area=18.0) == {"": 18.0, "mid#0": 4.0, "mid#0/leaf": 1.0,
                                                             "mid#1": 4.0, "mid#1/leaf": 1.0}


def test_yosys_parse_files_and_vt_mix(tmp_path):
    cfg = C.parse({"project": {"name": "x", "top": "p5_core"},
                   "library": {"vt_suffix": {"_R": "RVT", "_L": "LVT"}}}, tmp_path)
    res = YosysAdapter(cfg).parse([G / "pipe5" / "stat.json"], top="p5_core")
    exp = json.loads((G / "pipe5" / "expected.json").read_text())
    assert res.vt_mix == exp["vt_mix"]


def test_liberty_helpers(tmp_path):
    cells = liberty_cells([LIB])
    assert {"INV_R", "INV_L", "DFFR_R", "DLATCH_L", "TIEHI_R", "MUX2_L"} <= set(cells)
    assert all(c.endswith(("_R", "_L")) for c in cells)
    assert expand_dont_use("*_L", [LIB]) == [c for c in cells if c.endswith("_L")]
    assert expand_dont_use("", [LIB]) == []
    assert liberty_time_unit_ps([LIB]) == 1000.0
    root = tmp_path / "proj"
    root.mkdir()
    cfg = C.parse({"project": {"name": "x", "top": "t"},
                   "library": {"liberty": ["../a.lib"], "corners": {"tt": ["../a.lib"], "ss": ["../b.lib"]}}}, root)
    assert liberty_for(cfg, None) == [(tmp_path / "a.lib").resolve()]
    assert liberty_for(cfg, "ss") == [(tmp_path / "b.lib").resolve()]
    with pytest.raises(C.ConfigError):
        liberty_for(cfg, "ff")


# ------------------------------------------------------------------ OpenSTA
@pytest.mark.parametrize("fx,period", [("counter", 2000), ("counter", 200), ("pipe5", 2000), ("pipe5", 500)])
def test_opensta_report_golden(fx, period):
    text = (G / fx / f"sta_{period}.log").read_text()
    got = parse_report(text, top=TOPS[fx], period_ps=float(period))
    want = SynthResult.model_validate_json((G / fx / f"sta_{period}.parsed.json").read_text())
    assert got == want
    # invariants independent of the snapshot
    assert got.extra["sta_time_unit_ps"] == 1000.0
    maxp = [p for p in got.paths if p.path_type == "max"]
    minp = [p for p in got.paths if p.path_type == "min"]
    assert maxp and minp
    assert got.wns == min(p.slack for p in maxp)
    assert got.hold_wns == min(p.slack for p in minp)
    assert [p.slack for p in maxp] == sorted(p.slack for p in maxp)
    worst = maxp[0]
    assert worst.points and worst.points[-1].arrival == pytest.approx(worst.arrival)
    assert worst.required - worst.arrival == pytest.approx(worst.slack, abs=0.2)
    assert any(pt.net for pt in worst.points) and any(pt.cell for pt in worst.points)
    assert got.power.total and got.power.leakage and got.power.dynamic
    assert got.power.total == pytest.approx(got.power.dynamic + got.power.leakage, rel=1e-3)
    if period >= 2000:
        assert got.wns > 0 and got.tns == 0
    else:
        assert got.wns < 0 and got.tns < 0


def test_opensta_t_min_consistent_with_golden():
    """WNS at 2000 ps predicts T_min within the abc re-mapping noise."""
    for fx in ("counter", "pipe5"):
        exp = json.loads((G / fx / "expected.json").read_text())
        assert exp["t_min_ps"] < 2000
        assert exp["wns_at_default_ps"] > 0


def test_opensta_parse_timing_units_and_ports():
    text = """ time 1ps
Startpoint: in_a (input port clocked by c)
Endpoint: out_y (output port clocked by c)
Path Group: c
Path Type: max

  Delay    Time   Description
---------------------------------------------------------
   0.00    0.00   clock c (rise edge)
   0.00    0.00 ^ in_a (in)
                  in_a (net)
  12.50   12.50 v u1/sub/g7/Y (NAND2_X)
                  n7 (net)
   3.00   15.50 v out_y (out)
          15.50   data arrival time

 100.00  100.00   clock c (rise edge)
          100.00   data required time
---------------------------------------------------------
          100.00   data required time
          -15.50   data arrival time
---------------------------------------------------------
           84.50   slack (MET)
"""
    paths = OpenStaAdapter.__new__(OpenStaAdapter).parse_timing(text)
    assert len(paths) == 1
    p = paths[0]
    assert (p.startpoint, p.endpoint, p.slack, p.required, p.arrival) == ("in_a", "out_y", 84.5, 100.0, 15.5)
    assert [(x.inst, x.pin, x.cell, x.rise_fall) for x in p.points] == [
        ("", "in_a", None, "r"), ("u1/sub/g7", "Y", "NAND2_X", "f"), ("", "out_y", None, "f")]
    assert p.points[0].net == "in_a" and p.points[1].net == "n7"
    assert time_scale_ps(" time 1ns\n") == 1000.0 and time_scale_ps("nothing") is None


def test_opensta_power_units():
    t = """Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Total                  1.00e-03   2.00e-03   5.00e-06   3.005e-03 100.0%
"""
    p = parse_power(t)
    assert (p.dynamic, p.leakage, p.total) == (3.0, 0.005, 3.005)


def test_opensta_tcl_generation(tmp_path):
    tcl = build_tcl(liberty=[LIB], netlist=tmp_path / "n.v", top="t", clock="clk", period_lib=0.5, sdc=None,
                    max_paths=3)
    assert "create_clock -name svkit_clk -period 0.5" in tcl and "get_ports -quiet {clk}" in tcl
    assert "report_power" in tcl and "-group_count 3" in tcl
    tcl2 = build_tcl(liberty=[LIB], netlist=tmp_path / "n.v", top="t", clock="clk", period_lib=0.4,
                     sdc=tmp_path / "u.sdc", max_paths=3)
    assert "read_sdc" in tcl2 and "-period 0.4" in tcl2 and "svkit_clk" not in tcl2


# ------------------------------------------------------------------ verilator / perf helpers
def test_verilator_messages():
    log = """%Warning-WIDTHTRUNC: rtl/a.sv:12:9: Operator ASSIGN expects 4 bits
%Warning-WIDTHTRUNC: rtl/a.sv:12:9: Operator ASSIGN expects 4 bits
%Error: rtl/b.sv:3:1: syntax error
other line
"""
    msgs = parse_tool_messages(log)
    assert len(msgs) == 2
    assert (msgs[0].code, msgs[0].file, msgs[0].line, msgs[0].severity.value) == ("WIDTHTRUNC", "rtl/a.sv", 12,
                                                                                 "warning")
    assert msgs[1].severity.value == "error" and msgs[1].file == "rtl/b.sv"


def test_order_sources_puts_packages_first(tmp_path):
    a, b = tmp_path / "a.sv", tmp_path / "z_pkg.sv"
    a.write_text("module a; endmodule\n")
    b.write_text("package z_pkg; endpackage\n")
    assert order_sources([a, b]) == [b, a]


def test_vcd_end_time(tmp_path):
    f = tmp_path / "w.vcd"
    f.write_text("$timescale 1ps $end\n#0\n1!\n#150000\n0!\n")
    assert vcd_end_time(f) == 150000


def test_perf_regex(tmp_path):
    cfg = C.load(FIXTURES / "pipe5")
    res = parse_text(cfg.perf.source, "sum", "cycles = 57\ninstrs = 35\nload_use_stalls = 0\nbranch_flushes = 9\n")
    assert (res.cycles, res.instrs, res.stalls) == (57, 35, {"load_use": 0, "branch": 9})
    assert res.cpi == pytest.approx(57 / 35)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "t.cpi").write_text("cycles = 10\ninstrs = 5\n")
    assert parse_perf(cfg, "t", tmp_path).cpi == 2.0
    assert parse_perf(cfg, "missing", tmp_path) is None


# ------------------------------------------------------------------ fixture corpus sanity
def test_pipe5_goldens_match_reference_iss():
    """The pipe5 goldens equal the T8 instruction-set simulator's trace (cycle stamps removed)."""
    sys.path.insert(0, str(FIXTURES / "pipe5" / "tools"))
    sys.dont_write_bytecode = True
    try:
        import t8
    finally:
        sys.path.pop(0)
    progs = sorted((FIXTURES / "pipe5" / "programs").glob("*.s"))
    assert len(progs) >= 4
    for s in progs:
        words = t8.assemble(s.read_text())
        assert t8.read_mem(s.with_suffix(".mem")) == words, f"{s.name}: .mem out of date (make programs)"
        golden = (FIXTURES / "pipe5" / "expected" / f"{s.stem}.wb").read_text().splitlines()
        assert [re.sub(r"^@\d+ ", "", l) for l in golden] == t8.iss(words), s.stem


@pytest.mark.parametrize("fx", ["counter", "fifo", "pipe5", "latch"])
def test_fixture_configs_load(fx):
    cfg = C.load(FIXTURES / fx)
    assert {"verilator", "vcs"} <= set(cfg.flows.sim) and {"yosys", "dc"} <= set(cfg.flows.synth)
    assert cfg.flows.synth["yosys"].sta == "opensta"
    assert {"clock_period", "corner"} <= set(cfg.knobs) and len(cfg.experiments) >= 2
    for lib in cfg.library.liberty + [l for ls in cfg.library.corners.values() for l in ls]:
        assert (cfg.root / lib).resolve().is_file()
    assert json.loads((G / fx / "expected.json").read_text())["t_min_ps"] > 0


def test_toy_library_is_regenerable(tmp_path):
    """The committed .lib files are exactly what the generator writes."""
    sys.path.insert(0, str(FIXTURES / "lib"))
    sys.dont_write_bytecode = True
    try:
        import gen_svkit_tiny as gen
    finally:
        sys.path.pop(0)
    assert gen.library("tt") == (FIXTURES / "lib" / "svkit_tiny.lib").read_text()
    assert gen.library("ss") == (FIXTURES / "lib" / "svkit_tiny_ss.lib").read_text()
