"""HTTP routes of the analysis track: compare, sweeps, perf, divergence, waves."""
import shutil
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from svkit import config as C
from svkit.analysis import perf as PF
from svkit.core.rundb import RunDB
from svkit.server.app import create_app

DATA = Path(__file__).parent / "data" / "analysis"


@pytest.fixture
def env(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="blinker"\ntb_top="tiny_tb"\n'
                                         'clock="clock"\nreset={name="reset"}\n')
    cfg = C.load(tmp_path)
    db = RunDB.for_project(tmp_path)
    return TestClient(create_app(cfg)), db


def _synth(db, exp, **m):
    r = db.create_run("synth", "yosys", experiment=exp, status="done")
    db.update_run(r.id, metrics={"tool": "yosys", "top": "blinker", **m})
    return r.id


def _sim_with_waves(db, wave="tiny.vcd", divergence=None):
    r = db.create_run("sim", "verilator", test="t1", status="failed")
    shutil.copy(DATA / wave, db.runs_dir / r.id / f"waves{Path(wave).suffix}")
    m = {"tool": "verilator", "test": "t1", "passed": False, "waves": f"waves{Path(wave).suffix}",
         "perf": {"test": "t1", "cycles": 30, "instrs": 20}}
    if divergence:
        m["divergence"] = divergence
    db.update_run(r.id, artifacts={f"waves{Path(wave).suffix}": f"waves{Path(wave).suffix}"}, metrics=m)
    return r.id


def test_compare_route(env):
    c, db = env
    a = _synth(db, "base", wns=-10.0, area_total=100.0)
    b = _synth(db, "fast", wns=5.0, area_total=120.0)
    res = c.get("/api/compare", params={"ids": f"{a},{b}"}).json()
    assert res["run_ids"] == [a, b] and res["labels"] == ["base", "fast"]
    wns = next(m for m in res["metrics"] if m["metric"] == "wns")
    assert wns["deltas"] == [0.0, 15.0] and wns["verdict"] == ["same", "better"]
    assert c.get("/api/compare", params={"ids": f"{a},r_9999"}).status_code == 404
    assert c.get("/api/compare", params={"ids": ","}).status_code == 400


def test_sweep_route(env):
    c, db = env
    g = db.create_group("sweep", {"tol": 25})
    r = c.get(f"/api/sweeps/{g.id}").json()
    assert r["status"] == "running" and r["points"] == [] and r["tol"] == 25
    res = {"group_id": g.id, "tol": 25, "status": "done", "t_min": 900.0, "t_min_run_id": "r_0001",
           "points": [{"period": 900.0, "run_id": "r_0001", "wns": 3.0, "passed": True}]}
    db.update_group(g.id, status="done", result=res)
    r = c.get(f"/api/sweeps/{g.id}").json()
    assert r["t_min"] == 900.0 and r["points"][0]["passed"] and r["knob"] == "clock_period"
    assert c.get("/api/sweeps/g_9999").status_code == 404
    m = db.create_group("matrix")
    assert c.get(f"/api/sweeps/{m.id}").status_code == 404


def test_perf_route_keeps_the_period_a_run_finished_with(env):
    c, db = env
    sid = _synth(db, None, period=1000.0)
    rid = _sim_with_waves(db)
    PF.fix_period(c.app.state.svkit.cfg, db, rid)          # what the runner does as the run finishes
    rows = c.get("/api/perf").json()
    assert rows[0]["test"] == "t1" and rows[0]["cpi"] == 1.5
    assert rows[0]["tpi"] == 1500.0 and rows[0]["period_source"] == f"synth:{sid}"
    _synth(db, None, period=500.0)                          # a newer synth run does not move it
    assert c.get("/api/perf").json()[0]["tpi"] == 1500.0


def test_divergence_route(env):
    c, db = env
    rid = _sim_with_waves(db, divergence={"line": 7, "cycle": 2, "expected": "1", "actual": "0"})
    d = c.get(f"/api/runs/{rid}/divergence").json()
    assert d == {"line": 7, "cycle": 2, "time": 45000, "expected": "1", "actual": "0", "source": None}
    other = _sim_with_waves(db)
    assert c.get(f"/api/runs/{other}/divergence").status_code == 404
    assert c.get("/api/runs/r_9999/divergence").status_code == 404


@pytest.mark.parametrize("wave", ["tiny.vcd", "tiny.fst"])
def test_wave_routes(env, wave):
    c, db = env
    rid = _sim_with_waves(db, wave)
    base = f"/api/waves/{rid}"
    m = c.get(f"{base}/meta").json()
    assert m["t_end"] == 222000 and m["timescale"]["unit"] == "ps" and m["format"] == wave.split(".")[1]
    top = c.get(f"{base}/scope", params={"path": ""}).json()
    assert top["path"] == "" and top["scopes"][0]["name"] == "tiny_tb"
    dut = c.get(f"{base}/scope", params={"path": "tiny_tb.dut"}).json()
    assert {v["name"] for v in dut["vars"]} == {"clock", "reset", "count", "late", "mode"}
    assert c.get(f"{base}/scope", params={"path": "zzz"}).status_code == 404
    hits = c.get(f"{base}/search", params={"q": "*.LATE", "limit": 5}).json()
    assert [h["path"] for h in hits] == ["tiny_tb.late", "tiny_tb.dut.late"] and hits[0]["width"] == 8
    d = c.post(f"{base}/data", json={"signals": ["tiny_tb.dut.mode", "tiny_tb.clock"], "t0": 0, "t1": 222000,
                                     "max_changes": 10}).json()["signals"]
    assert d["tiny_tb.dut.mode"]["changes"] == [[5000, "00"], [55000, "01"], [95000, "10"], [215000, "01"]]
    assert d["tiny_tb.clock"]["truncated"] and d["tiny_tb.clock"]["changes"][0][1] == "*"
    assert c.post(f"{base}/data", json={"signals": ["nope"], "t0": 0, "t1": 1}).status_code == 404
    assert c.post(f"{base}/data", json={"signals": ["tiny_tb.clock"]}).status_code == 400
    assert c.get(f"{base}/next", params={"signal": "tiny_tb.dut.late", "t": 0}).json() == {"t": 115000}
    assert c.get(f"{base}/next", params={"signal": "tiny_tb.dut.late", "t": 300000, "what": "x",
                                         "dir": -1}).json() == {"t": 0}
    assert c.get(f"{base}/next", params={"signal": "tiny_tb.clock", "t": 222000}).json() == {"t": None}
    assert c.get(f"{base}/next", params={"signal": "nope", "t": 0}).status_code == 404
    assert c.get(f"{base}/decode", params={"signal": "tiny_tb.dut.mode"}).json() == {"kind": "plain"}
    assert c.get(f"{base}/decode", params={"signal": "nope"}).status_code == 404


def test_wave_routes_errors(env):
    c, db = env
    r = db.create_run("sim", "verilator", test="t", status="passed")
    assert c.get(f"/api/waves/{r.id}/meta").status_code == 404
    assert c.get("/api/waves/r_9999/meta").status_code == 404


def test_decode_uses_design_graph_when_present(env, monkeypatch):
    c, db = env
    rid = _sim_with_waves(db)
    calls = []

    def lookup_type(cfg, path):
        calls.append(path)
        if path.endswith("mode"):
            return {"kind": "enum", "enum": {"00": "IDLE", "01": "RUN", "10": "DONE"}}
        return None

    mod = types.ModuleType("svkit.design.graph")
    mod.lookup_type = lookup_type
    monkeypatch.setitem(sys.modules, "svkit.design.graph", mod)
    r = c.get(f"/api/waves/{rid}/decode", params={"signal": "tiny_tb.dut.mode"}).json()
    assert r["kind"] == "enum" and r["enum"]["01"] == "RUN"
    assert c.get(f"/api/waves/{rid}/decode", params={"signal": "tiny_tb.dut.count"}).json() == {"kind": "plain"}
    assert calls == ["tiny_tb.dut.mode", "tiny_tb.dut.count"]
