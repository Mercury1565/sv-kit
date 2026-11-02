import shutil
from pathlib import Path

from svkit import config as C
from svkit.adapters.waves import wellen as W
from svkit.analysis import divergence as D
from svkit.core.rundb import RunDB

DATA = Path(__file__).parent / "data" / "analysis"

TOML = """
[project]
name = "tiny"
top = "blinker"
tb_top = "tiny_tb"
clock = "clock"
reset = {{ name = "reset", active = "{active}" }}
"""


def _setup(tmp_path, wave="tiny.vcd", active="high"):
    (tmp_path / "svkit.toml").write_text(TOML.format(active=active))
    cfg = C.load(tmp_path)
    db = RunDB.for_project(tmp_path)
    return cfg, db


def _run(db, divergence, wave=DATA / "tiny.vcd", art="waves.vcd", via_metrics=False):
    r = db.create_run("sim", "verilator", test="t", status="failed")
    shutil.copy(wave, db.runs_dir / r.id / art)
    m = {"tool": "verilator", "test": "t", "passed": False, "divergence": divergence}
    if via_metrics:
        m["waves"] = art
    db.update_run(r.id, artifacts={art: art}, metrics=m)
    return db.get_run(r.id)


def test_cycle_mapped_to_rising_edge(tmp_path):
    cfg, db = _setup(tmp_path)
    # reset deasserts at 22000; rising edges at 5000 + 10000k -> cycle 0 = 25000, cycle 3 = 55000
    for cyc, t in ((0, 25000), (3, 55000), (19, 215000)):
        d = D.run_divergence(cfg, db, _run(db, {"line": 4, "cycle": cyc, "expected": "a", "actual": "b"}))
        assert d.time == t and d.cycle == cyc and d.line == 4
    assert D.run_divergence(cfg, db, _run(db, {"line": 1, "cycle": 500})).time is None   # past the end
    d = D.run_divergence(cfg, db, _run(db, {"line": 2, "time": 1234}))                    # already a time
    assert d.time == 1234
    assert D.run_divergence(cfg, db, _run(db, None)) is None


def test_fst_and_metrics_waves_name(tmp_path):
    cfg, db = _setup(tmp_path)
    r = _run(db, {"line": 4, "cycle": 1}, wave=DATA / "tiny.fst", art="dump.fst", via_metrics=True)
    assert D.run_divergence(cfg, db, r).time == 35000


def test_no_waveform_or_unknown_clock(tmp_path):
    cfg, db = _setup(tmp_path)
    r = db.create_run("sim", "verilator", test="t", status="failed")
    db.update_run(r.id, metrics={"tool": "verilator", "test": "t", "divergence": {"line": 3, "cycle": 2}})
    d = D.run_divergence(cfg, db, db.get_run(r.id))
    assert d.line == 3 and d.time is None
    cfg.project.clock = "no_such_clock"
    assert D.run_divergence(cfg, db, _run(db, {"line": 1, "cycle": 1})).time is None


def test_irregular_clock_and_active_low_reset(tmp_path):
    # gated clock (general path), active-low reset `rst_n` released at 30
    vcd = """$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 1 " rst_n $end
$scope module u $end
$var wire 1 # clk $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
0"
0#
#10
1!
1#
#20
0!
0#
#30
1"
#40
1!
1#
#45
0!
0#
#47
1!
1#
#60
0!
0#
#100
1!
1#
#110
"""
    p = tmp_path / "irr.vcd"
    p.write_text(vcd)
    (tmp_path / "svkit.toml").write_text('[project]\nname="x"\ntop="u"\ntb_top="tb"\nclock="clk"\n'
                                         'reset={name="rst_n", active="low"}\n')
    cfg = C.load(tmp_path)
    wf = W.open_wave(p)
    assert W.find_vars(wf, "clk", under="tb")[0] == "tb.clk"          # shallowest first
    assert [D.cycle_to_time(cfg, wf, n) for n in range(4)] == [40, 47, 100, None]
