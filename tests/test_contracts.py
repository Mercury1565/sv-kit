from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters.base import KnobPlan
from svkit.analysis.vt import vt_mix
from svkit.core import golden, knobs as K
from svkit.core.rundb import RunDB
from svkit.schemas import SynthResult, XTraceNode, XCause

TOML = '''
[project]
name = "demo"
top = "core"
sources = { glob = ["rtl/*.sv"] }

[flows.sim.verilator]
mode = "builtin"
tests = { names = ["t1"] }

[flows.synth.dc]
cmd = "dc_shell-t -f syn.tcl"
script = "syn.tcl"
license_seats = 2

[knobs]
clock_period = { file = "clk.txt", unit = "ns", default = 4000 }
corner = { tcl_var = "corner" }
compile_cmd = { tcl_replace = '^compile\\b.*$', with = "{value}" }
period_env = { env = "CLOCK_PERIOD" }

[experiments]
baseline = {}
ss = { corner = "SS" }
ultra = { compile_cmd = "compile_ultra" }
fanout = { tcl_append = "set_max_fanout 8 [current_design]" }

[library]
vt_suffix = { "_R" = "RVT", "_L" = "LVT", "_SL" = "SLVT" }
'''


def cfg(tmp_path, text=TOML):
    (tmp_path / "svkit.toml").write_text(text)
    return C.load(tmp_path)


def test_config_loads(tmp_path):
    c = cfg(tmp_path)
    assert c.project.top == "core"
    assert c.knobs["clock_period"].mechanism == "file"
    assert c.knobs["compile_cmd"].with_ == "{value}"
    assert c.sim_flow()[0] == "verilator"
    assert c.root == tmp_path


def test_config_errors(tmp_path):
    with pytest.raises(C.ConfigError, match="unknown knob"):
        cfg(tmp_path, TOML + '\n[experiments.bad]\nnope = 1\n'.replace("[experiments.bad]", "[experiments.x]"))
    with pytest.raises(C.ConfigError, match="exactly one"):
        cfg(tmp_path, '[project]\nname="a"\ntop="b"\n[knobs]\nk = { file = "a", env = "B" }\n')
    with pytest.raises(C.ConfigError, match="wrap mode needs"):
        cfg(tmp_path, '[project]\nname="a"\ntop="b"\n[flows.sim.vcs]\nmode="wrap"\n')


def test_knob_plan_and_script(tmp_path):
    c = cfg(tmp_path)
    vals = K.resolve(c, experiment="ss", sets={"period_env": 2500})
    assert vals == {"clock_period": 4000, "corner": "SS", "period_env": 2500}
    p = K.plan(c, vals)
    assert p.files == {"clk.txt": "4\n"}            # ps -> ns
    assert p.env == {"CLOCK_PERIOD": "2500"}
    assert p.tcl_prelude == ["set corner SS"]
    (tmp_path / "syn.tcl").write_text("set corner TT\nread_file x\ncompile -map_effort high\n")
    K.prepare_script(p, "syn.tcl", tmp_path)
    body = (tmp_path / "syn.tcl").read_text()
    assert body.index("set corner SS") < body.index("if {![info exists corner]} {set corner TT}")
    # replace + append
    (tmp_path / "syn.tcl").write_text("compile -map_effort high\nreport_timing\n")
    p2 = K.plan(c, K.resolve(c, experiment="ultra") | {"tcl_append": "set_max_fanout 8 [current_design]"})
    K.prepare_script(p2, "syn.tcl", tmp_path)
    body = (tmp_path / "syn.tcl").read_text()
    assert "compile_ultra\nreport_timing" in body and body.rstrip().endswith("set_max_fanout 8 [current_design]")
    with pytest.raises(C.ConfigError):
        K.edit_script("nothing here", KnobPlan(tcl_replace=[("^compile", "x")]))


def test_parse_set():
    assert K.parse_set(["a=1", "b=2.5", "c=SS", "d=true"]) == {"a": 1, "b": 2.5, "c": "SS", "d": True}


def test_rundb_roundtrip(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    g = db.create_group("matrix", {"x": 1})
    r1 = db.create_run("synth", "dc", knobs={"corner": "SS"}, group_id=g.id, cache_key="k1")
    r2 = db.create_run("sim", "verilator", test="t1")
    assert (r1.id, r2.id) == ("r_0001", "r_0002")
    m = SynthResult(tool="dc", top="core", wns=-12.5, area_total=100.0).model_dump(mode="json")
    db.update_run(r1.id, status="done", metrics=m, artifacts={"synth.log": "synth.log"})
    db.add_tag(r1.id, "before-fix")
    got = db.get_run(r1.id)
    assert got.metrics["wns"] == -12.5 and got.tags == ["before-fix"] and got.knobs == {"corner": "SS"}
    assert db.find_cached("k1").id == r1.id
    assert [r.id for r in db.list_runs(kind="synth")] == [r1.id]
    assert [r.id for r in db.list_runs(tag="before-fix")] == [r1.id]
    assert db.get_group(g.id).params == {"x": 1}
    assert (tmp_path / ".svkit" / "runs" / r1.id).is_dir()


def test_golden(tmp_path):
    a, e = tmp_path / "a.txt", tmp_path / "e.txt"
    e.write_text("@@ 10 r1=5\n@@ 20 r2=6\n@@ 30 r3=7\n")
    a.write_text("@@ 10 r1=5\n@@ 20 r2=9\n@@ 30 r3=7\n")
    g = C.Golden(actual="a", expected="e", time_regex=r"@@ (\d+)")
    passed, reason, d = golden.evaluate(exit_code=0, log_text="", pass_regex=None, fail_regex=None,
                                        actual=a, expected=e, golden=g)
    assert passed is False and d.line == 2 and d.time == 20 and d.expected == "@@ 20 r2=6"
    a.write_text(e.read_text())
    assert golden.evaluate(exit_code=0, log_text="", pass_regex=None, fail_regex=None,
                           actual=a, expected=e, golden=g)[0] is True
    assert golden.evaluate(exit_code=3, log_text="", pass_regex=None, fail_regex=None,
                           actual=a, expected=e, golden=g)[0] is False


def test_vt_mix():
    assert vt_mix({"INV_X1_R": 3, "NAND2_SL": 2, "DFF_L": 1, "TIE": 1},
                  {"_R": "RVT", "_L": "LVT", "_SL": "SLVT"}) == {"LVT": 1, "RVT": 3, "SLVT": 2, "other": 1}


def test_schemas():
    n = XTraceNode(signal="top.a", time=10, value="x", cause=XCause.no_reset,
                   children=[XTraceNode(signal="top.b", time=5, value="x")])
    assert XTraceNode.model_validate_json(n.model_dump_json()) == n
    assert SynthResult(tool="t", top="x", wns=1, hold_wns=-1).met is False
