"""Open-source flow, run for real: Verilator sim, Yosys + OpenSTA synthesis on the fixture corpus."""
import json
import re
import shutil
import subprocess

import pytest

from svkit import config as C
from svkit.core.runner import Runner

from conftest import FIXTURES

pytestmark = pytest.mark.oss
G = FIXTURES / "golden" / "oss"


def _runner(path):
    return Runner(C.load(path))


def _expected(fx):
    return json.loads((G / fx / "expected.json").read_text())


@pytest.mark.parametrize("fx", ["counter", "fifo", "pipe5", "latch"])
def test_sim_all_builtin(fixture_copy, fx):
    root = fixture_copy(fx)
    r = _runner(root)
    gid, runs = r.sim_all(jobs=4)
    want = _expected(fx)["tests"]
    got = {run.test: run.status for run in runs}
    assert got == want, {run.test: run.message for run in runs}
    builds = list((root / ".svkit" / "build").glob("verilator-*/Vsim"))
    assert len(builds) == 1, "one Verilator build shared by all tests"
    for run in runs:
        m = run.metrics
        assert m["tool"] == "verilator" and m["waves"] == "waves.vcd" and m["sim_time"] > 0
        assert {"waves.vcd", "sim.log", "build.log"} <= set(run.artifacts)
        assert (r.db.run_dir(run.id) / "waves.vcd").stat().st_size > 0
    grp = r.db.get_group(gid)
    assert grp.status == ("failed" if "failed" in want.values() else "done")


def test_pipe5_failing_test_divergence_and_perf(fixture_copy):
    root = fixture_copy("pipe5")
    r = _runner(root)
    exp = _expected("pipe5")
    bad = r.sim("ldst_fwd")
    assert bad.status == "failed"
    d = bad.metrics["divergence"]
    ft = exp["failing_test"]
    assert (d["line"], d["cycle"], d["expected"], d["actual"]) == (ft["divergence_line"], ft["divergence_cycle"],
                                                                 ft["expected"], ft["actual"])
    assert "golden mismatch" in bad.message
    good = r.sim("hazards")
    assert good.status == "passed"
    perf = good.metrics["perf"]
    assert (perf["cycles"], perf["instrs"]) == (exp["perf"]["hazards"]["cycles"], exp["perf"]["hazards"]["instrs"])
    assert perf["stalls"]["load_use"] >= 1 and perf["cpi"] > 1
    # cache: same sources + test -> new run copied from the first
    again = r.sim("hazards")
    assert again.cached_from == good.id and again.status == "passed"


def test_sim_detects_stale_output_and_compile_errors(fixture_copy):
    root = fixture_copy("counter")
    (root / "output").mkdir(exist_ok=True)
    (root / "output" / "basic.out").write_text("stale\n")
    (root / "rtl" / "counter.sv").write_text((root / "rtl" / "counter.sv").read_text().replace("endmodule", "endmodul"))
    r = _runner(root)
    run = r.sim("basic")
    assert run.status == "failed" and "compile failed" in run.message
    assert "build.log" in run.artifacts
    assert any(w["severity"] == "error" for w in run.metrics["warnings"])


def test_sim_wrap_mode_makefile(fixture_copy):
    root = fixture_copy("counter")
    t = (root / "svkit.toml").read_text()
    t = t.replace('[flows.sim.verilator]\nmode = "builtin"',
                  '[flows.sim.verilator]\nmode = "wrap"\ncmd = "make -s sim-verilator TEST={test}"')
    (root / "svkit.toml").write_text(t)
    r = _runner(root)
    gid, runs = r.sim_all(jobs=1)
    assert [x.status for x in runs] == ["passed"] * 3, [x.message for x in runs]
    assert all(x.metrics["waves"] == "waves.vcd" for x in runs)


def test_synth_counter_and_pipe5(fixture_copy):
    for fx in ("counter", "pipe5"):
        root = fixture_copy(fx)
        r = _runner(root)
        run = r.synth()
        assert run.status == "done", run.message
        m = run.metrics
        exp = _expected(fx)
        assert m["tool"] == "yosys+opensta" and m["corner"] == "TT" and m["period"] == 2000
        assert m["area_total"] == exp["area_total"] and m["cell_count"] == exp["cell_count"]
        assert m["wns"] == pytest.approx(exp["wns_at_default_ps"]) and m["tns"] == 0 and m["hold_wns"] >= 0
        assert m["power"]["total"] == pytest.approx(exp["power_total_mw"], rel=1e-3)
        assert m["vt_mix"] == exp["vt_mix"]
        assert m["paths"] and all(p["points"] for p in m["paths"])
        assert {"netlist.v", "netlist.json", "stat.json", "sta.log", "synth.log"} <= set(run.artifacts)
        if fx == "pipe5":
            assert m["area_by_hier"] == exp["area_by_hier"]
        # repeat -> cache hit
        again = r.synth()
        assert again.cached_from == run.id and again.metrics == run.metrics


def test_synth_knobs_change_results(fixture_copy):
    root = fixture_copy("counter")
    r = _runner(root)
    base = r.synth().metrics
    ss = r.synth(experiment="ss_corner").metrics
    assert ss["corner"] == "SS" and ss["wns"] < base["wns"]
    tight = r.synth(sets={"clock_period": 200}).metrics
    assert tight["period"] == 200 and tight["wns"] < 0 and tight["tns"] < 0
    rvt = r.synth(experiment="rvt_only").metrics
    assert set(rvt["vt_mix"]) == {"RVT"} and "LVT" in base["vt_mix"]
    t_min = _expected("counter")["t_min_ps"]
    at = r.synth(sets={"clock_period": t_min}).metrics
    below = r.synth(sets={"clock_period": t_min - 1}).metrics
    assert at["wns"] >= 0 and below["wns"] < 0


def test_synth_matrix(fixture_copy):
    root = fixture_copy("fifo")
    r = _runner(root)
    gid, runs = r.matrix(jobs=2)
    assert [x.experiment for x in runs] == ["baseline", "ss_corner", "rvt_only"]
    assert all(x.status == "done" for x in runs)


def test_synth_wrap_mode_makefile(fixture_copy):
    root = fixture_copy("counter")
    t = (root / "svkit.toml").read_text()
    t = t.replace('[flows.synth.yosys]\nmode = "builtin"',
                  '[flows.synth.yosys]\nmode = "wrap"\ncmd = "make -s syn-yosys"\n'
                  'reports = ["syn_out/{top}.stat.json"]\nnetlist = "syn_out/{top}.yosys.v"')
    (root / "svkit.toml").write_text(t)
    r = _runner(root)
    run = r.synth(sets={"clock_period": 1000})
    assert run.status == "done", run.message
    m = run.metrics
    assert m["period"] == 1000 and m["wns"] > 0 and m["cell_count"] > 0 and m["paths"]
    # the corner knob reaches the wrapped Makefile through SVKIT_LIBERTY
    ss = r.synth(experiment="ss_corner").metrics
    assert ss["wns"] < r.synth().metrics["wns"]


def test_cli_sim_and_synth(fixture_copy, monkeypatch):
    from typer.testing import CliRunner
    from svkit.cli import app
    root = fixture_copy("latch")
    monkeypatch.chdir(root)
    cli = CliRunner()
    res = cli.invoke(app, ["sim", "--all"])
    assert res.exit_code == 0, res.output
    assert "1/1 passed" in res.output
    res = cli.invoke(app, ["synth", "--set", "clock_period=500"])
    assert res.exit_code == 0, res.output
    assert re.search(r"WNS=\d", res.output)
