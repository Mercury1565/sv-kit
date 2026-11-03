"""Golden + unit tests for the Design Compiler report parsers (no Synopsys tools needed).

Regenerate goldens after an intentional parser change:  SVKIT_REGEN_GOLDEN=1 pytest tests/test_dc_reports.py
"""
import json
import os
from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters.synth import dc_reports as R
from svkit.adapters.synth.dc import DcAdapter

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "synopsys" / "samples"
REAL = SAMPLES.parent / "real"      # real dc_shell-t output from CAEN runs (paths stripped)
REGEN = os.environ.get("SVKIT_REGEN_GOLDEN") == "1"


def _cfg(tmp_path, vt=True):
    lib = '[library]\nvt_suffix = { "_RVT" = "RVT", "_LVT" = "LVT", "_HVT" = "HVT", "_R" = "RVT", "_L" = "LVT", "_SL" = "SLVT" }\n' if vt else ""
    (tmp_path / "svkit.toml").write_text(f'[project]\nname = "t"\ntop = "x"\n{lib}')
    return C.load(tmp_path)


def _sample_inputs(p: Path) -> list[Path]:
    if p.suffix == ".files":
        return [p.parent / ln.strip() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    return [p]


SAMPLE_FILES = sorted(p for d in (SAMPLES, REAL) if d.is_dir() for p in d.iterdir()
                      if p.suffix in (".rpt", ".rep", ".log", ".files"))


def test_every_sample_has_a_golden():
    assert SAMPLE_FILES, "no samples found"
    missing = [p.name for p in SAMPLE_FILES if not p.with_suffix(".expected.json").exists()]
    assert REGEN or not missing, f"samples without .expected.json: {missing} (run with SVKIT_REGEN_GOLDEN=1)"


@pytest.mark.parametrize("sample", SAMPLE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_golden(sample, tmp_path):
    ad = DcAdapter(_cfg(tmp_path))
    got = ad.parse(_sample_inputs(sample), top="").model_dump(mode="json")
    exp_file = sample.with_suffix(".expected.json")
    if REGEN:
        exp_file.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert exp_file.exists(), f"missing {exp_file.name}; run with SVKIT_REGEN_GOLDEN=1"
    assert got == json.loads(exp_file.read_text())


# ---------------------------------------------------------------- splitting
def test_split_combined_rep():
    secs = R.split_reports((SAMPLES / "combined_ns.rep").read_text())
    assert [s.kind for s in secs] == ["design", "timing", "constraint", "power", "resources", "area", "reference"]
    assert all(s.design == "minicore" for s in secs)
    assert "-max_paths 2" in secs[1].options and "-all_violators" in secs[2].options
    # the per-design sub-banners of report_reference -hierarchy are NOT new reports
    assert "Design: ex_stage" in secs[-1].text and "Design: mem_stage" in secs[-1].text


def test_combined_equals_parts(tmp_path):
    """Parsing the combined .rep gives the same result as parsing its sections as separate files."""
    text = (SAMPLES / "combined_ns.rep").read_text()
    files = []
    for i, s in enumerate(R.split_reports(text)):
        f = tmp_path / f"{i}_{s.kind}.rpt"
        f.write_text(s.text)
        files.append(f)
    a = R.parse_files([SAMPLES / "combined_ns.rep"])
    b = R.parse_files(files)
    assert a.model_dump() == b.model_dump()


def test_no_banner_text_is_guessed():
    body = (SAMPLES / "timing_hold.rpt").read_text().split("****************************************\n")[-1]
    r = R.parse_texts([body], top="alu_pipe")
    assert r.hold_wns == -30.0 and len(r.paths) == 2


# ---------------------------------------------------------------- units
def test_time_and_power_units():
    assert R.time_unit_to_ps("ns") == 1000 and R.time_unit_to_ps("1ps") == 1 and R.time_unit_to_ps("10ps") == 10
    assert R.time_unit_to_ps("1e-09 Second") == pytest.approx(1000)
    assert R.power_to_mw(1.5, "uW") == pytest.approx(1.5e-3)
    assert R.power_to_mw(2, "nW") == pytest.approx(2e-6)
    assert R.power_to_mw(3, "pW") == pytest.approx(3e-9)
    assert R.power_to_mw(4, "W") == pytest.approx(4000)
    assert R.power_to_mw(5, "mW") == 5
    assert R.power_to_mw(7, "1uW") == pytest.approx(7e-3)   # "Dynamic Power Units = 1uW"
    with pytest.raises(ValueError):
        R.power_to_mw(1, "furlongs")


def test_detect_time_unit():
    assert R.detect_time_unit("Time_unit                      : 1e-12 Second") == 1.0
    assert R.detect_time_unit("    Time Units = 1ns\n") == 1000.0
    assert R.detect_time_unit("Time Unit : 1ps") == 1.0
    assert R.detect_time_unit("nothing here") is None


def test_infer_time_unit():
    assert R.infer_time_unit(2.0, 2000.0) == 1000.0   # ns library
    assert R.infer_time_unit(400.0, 400.0) == 1.0     # ps library
    assert R.infer_time_unit(400.0, 2000.0) is None   # inconsistent -> don't guess
    assert R.infer_time_unit(None, 2000.0) is None


def test_unit_precedence():
    t = (SAMPLES / "timing_ps_violated.rpt").read_text()
    # magnitude heuristic (period 400 units) -> ps
    assert R.parse_texts([t]).wns == -51.12
    # known period in ps agrees with ns -> report is in ns (e.g. a 400 ns clock)
    r = R.parse_texts([t], period_ps=400_000)
    assert r.wns == -51120.0 and r.extra["time_unit_source"] == "inferred from clock period"
    assert r.period == 400_000
    # a configured default applies when nothing else decides
    c = (SAMPLES / "constraints_violators.rpt").read_text()
    assert R.parse_texts([c], time_unit="ps").wns == -51.12
    assert R.parse_texts([c]).wns == -51120.0          # default ns
    # an explicit unit in the text beats everything
    u = "Report : units\nTime_unit : 1e-12 Second\n"
    assert R.parse_texts([t, u], time_unit="ns", period_ps=400_000).extra["time_unit"] == "ps"


# ---------------------------------------------------------------- individual parsers
def test_timing_columns_nets_and_ports():
    paths = R.parse_timing((SAMPLES / "timing_ns_met.rpt").read_text())
    assert [p.slack for p in paths] == [230.0, 550.0]
    p = paths[0]
    assert p.startpoint == "u_stage1/opa_q_reg_3_" and p.endpoint == "u_stage2/res_q_reg_7_"
    assert p.path_group == "clk" and p.path_type == "max" and p.required == 940.0 and p.arrival == 710.0
    q = p.points[1]
    assert (q.inst, q.pin, q.cell, q.incr, q.arrival, q.rise_fall, q.net) == \
        ("u_stage1/opa_q_reg_3_", "Q", "DFFX1_RVT", 180.0, 180.0, "f", "u_stage1/opa_q[3]")
    assert p.points[-1].pin == "D" and p.points[-1].arrival == 710.0
    # annotated delay markers (*, &) are ignored, values kept
    assert any(pt.inst == "u_stage1/U40" and pt.pin == "Y" and pt.incr == 30.0 for pt in p.points)
    port = paths[1].points[0]
    assert (port.inst, port.pin, port.cell, port.arrival) == ("", "op_sel[1]", None, 210.0)


def test_timing_wrapped_names():
    p = R.parse_timing((SAMPLES / "timing_ps_violated.rpt").read_text(), scale=1.0)[0]
    names = [(pt.inst, pt.cell) for pt in p.points]
    assert ("u_datapath_with_a_very_long_hierarchical_name/u_alu_stage/U1022", "NAND2xp5_T75_L") in names
    assert ("u_datapath_with_a_very_long_hierarchical_name/u_alu_stage/U1023", "NOR2xp33_T75_SL") in names
    assert p.points[-1].pin == "result[15]" and p.points[-1].cell is None and p.slack == -51.12


def test_hold_paths_and_order():
    r = R.parse_files([SAMPLES / "timing_ns_met.rpt", SAMPLES / "timing_hold.rpt"])
    assert [p.path_type for p in r.paths] == ["max", "max", "min", "min"]
    assert r.paths[2].slack == -30.0 and r.hold_wns == -30.0 and r.hold_tns == -30.0
    assert not r.met


def test_qor_groups():
    q = R.parse_qor((SAMPLES / "qor_ns.rpt").read_text())
    assert set(q["groups"]) == {"INPUTS", "clk"}
    assert q["groups"]["clk"]["Critical Path Slack"] == 0.23
    assert q["sections"]["Cell Count"]["Leaf Cell Count"] == 214


def test_area_hierarchy():
    a = R.parse_area((SAMPLES / "area_hier.rpt").read_text())
    assert a["hier"][""] == 6461.5193
    assert a["hier"]["u_core/u_execute/u_multiplier_with_long_name_0"] == 2767.452
    assert len(a["hier"]) == 6
    assert a["summary"]["total_cell"] == 6461.519254 and a["summary"]["cells"] == 2511
    assert a["libraries"] == ["tiny_std_tt", "tiny_std_tt_lvt"]   # names only, never file paths


def test_constraints():
    c = R.parse_constraints((SAMPLES / "constraints_violators.rpt").read_text())
    assert [e["slack"] for e in c["setup"]] == [-51.12, -38.4, -4.71]
    assert c["setup"][2]["name"] == "u_datapath_with_a_very_long_hierarchical_name/acc_q_reg_30_/D"
    assert c["hold"][0]["slack"] == -3.4 and c["setup"][0]["group"] == "core_clk"
    assert set(c["drv"]) == {"max_transition", "max_fanout"}
    assert R.parse_constraints((SAMPLES / "constraints_clean.rpt").read_text())["clean"] is True


def test_power():
    p = R.parse_power((SAMPLES / "power_uw.rpt").read_text())
    assert p["internal"] == pytest.approx(0.182441) and p["switching"] == pytest.approx(0.074512)
    assert p["dynamic"] == pytest.approx(0.256953) and p["leakage"] == pytest.approx(8.2014e-6)
    q = R.parse_power((SAMPLES / "combined_ps.rep").read_text())
    assert q["leakage"] == pytest.approx(3.15e-5) and q["total"] == pytest.approx(0.0245315)
    # only the power-group table (no summary lines)
    t = "Total            1.0000 mW        2.0000 uW     3.0000e+03 pW       1.0020 mW\n"
    assert R.parse_power(t) == pytest.approx({"internal": 1.0, "switching": 0.002, "dynamic": 1.002,
                                              "leakage": 3e-6, "total": 1.002})


def test_reference_flattening():
    ref = R.parse_reference((SAMPLES / "reference_hier.rpt").read_text())
    # stage_W8 is instantiated twice at the top, add_W8 once per stage
    assert ref["cells"]["DFFHQNx1_T75_R"] == 4 + 2 * 8
    assert ref["cells"]["FAx1_T75_R"] == 2 * 4
    assert ref["cells"]["NOR2xp33_T75_SL"] == 2 * 2
    assert "stage_W8" not in ref["cells"] and ref["unexpanded"] == []


def test_vt_mix_from_references(tmp_path):
    r = DcAdapter(_cfg(tmp_path)).parse([SAMPLES / "reference_hier.rpt"], top="pipe_top")
    assert r.vt_mix == {"LVT": 20, "RVT": 58, "SLVT": 4}
    assert sum(r.vt_mix.values()) == r.cell_count == 82
    assert DcAdapter(_cfg(tmp_path, vt=False)).parse([SAMPLES / "reference_hier.rpt"], top="x").vt_mix == {}


def test_resources_and_design():
    tabs = R.parse_resources((SAMPLES / "resources.rpt").read_text())
    assert tabs[0]["design"] == "alu_stage2_W8" and tabs[0]["file"] == "alu_stage2.sv"
    assert tabs[0]["rows"][2]["contained operations"] == "eq_57 lt_58 gt_59"
    assert tabs[1]["rows"][0]["current implementation"] == "pparch (area,speed)"
    d = R.parse_design((SAMPLES / "design.rpt").read_text())
    assert d["operating_conditions"]["name"] == "tt_1p0v_25c" and d["wire_load_model"] == "wl_small"


def test_log_dedupe_and_removed_registers():
    lr = R.parse_log((SAMPLES / "dc_warnings.log").read_text())
    codes = [m.code for m in lr.messages]
    assert codes[0] == "LINK-3" and codes.count("LINT-28") == 1
    lint = next(m for m in lr.messages if m.code == "LINT-28")
    assert lint.text.endswith("(x3)")
    elab = next(m for m in lr.messages if m.code == "ELAB-366")
    assert (elab.file, elab.line) == ("rtl/fifo_ctl.sv", 88)
    tim = next(m for m in lr.messages if m.code == "TIM-164")
    assert "tiny_std_tt_lvt" in tim.text            # continuation line joined
    assert [(r["register"], r["reason"]) for r in lr.removed_registers] == [
        ("u_ctl0/mode_q_reg[1]", "constant"), ("u_ctl0/mode_q_reg[0]", "constant"),
        ("u_ctl1/spare_q_reg", "unloaded"), ("wptr_q_reg[3]", "merged")]
    assert lr.counts["error"] == 1


def test_knob_values_win(tmp_path):
    r = DcAdapter(_cfg(tmp_path)).parse([SAMPLES / "combined_ns.rep"], top="minicore", period_ps=2000, corner="SS")
    assert r.period == 2000 and r.corner == "SS"


def test_robust_to_garbage():
    r = R.parse_texts(["****************************************\nReport : timing\n"
                       "Design : x\n****************************************\nStartpoint: a\nNo paths.\n",
                       "random text\n"], ["not a log"], top="x")
    assert r.paths == [] and r.wns is None and r.top == "x"


def test_fatal_problems_from_real_dc_failure():
    # dc_shell-t V-2023.12 exits 0 after these, so the adapter must catch them.
    log = ("Error: Could not read the following target libraries:\n"
           "syn_out/.db\n"
           "Warning: Unable to resolve reference 'sky_and2' in 'top'. (LINK-5)\n"
           "Warning: Module counter contains unmapped components.  The output netlist might not be "
           "read back into the system. (VO-12)\n")
    probs = R.fatal_problems(log)
    assert len(probs) == 3
    assert probs[1].startswith("unresolved cell references") and "sky_and2" in probs[1]
    assert probs[2].startswith("the netlist contains unmapped logic") and "VO-12" in probs[2]
    assert R.fatal_problems((SAMPLES / "dc_warnings.log").read_text()) == []
