"""/api/design/* and /api/xtrace through FastAPI's TestClient on a tmp project."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from svkit import config as C
from svkit.core.fsutil import copy_tree
from svkit.core.rundb import RunDB
from svkit.server.app import create_app

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "fixtures" / "golden" / "xtrace"
D = Path(__file__).parent / "data" / "design"


@pytest.fixture
def client(tmp_path):
    proj = tmp_path / "xbug_mem"
    copy_tree(REPO / "fixtures" / "xbug_mem", proj)
    db = RunDB.for_project(proj)
    sim = db.create_run("sim", "vcs", test="smoke", status="failed")
    shutil.copy(GOLD / "xbug_mem" / "waves.vcd", db.runs_dir / sim.id / "waves.vcd")
    db.update_run(sim.id, artifacts={"waves.vcd": "waves.vcd"},
                  metrics={"tool": "vcs", "test": "smoke", "waves": "waves.vcd"})
    syn = db.create_run("synth", "yosys", status="done")
    shutil.copy(D / "acc4.vg", db.runs_dir / syn.id / "netlist.v")
    shutil.copy(D / "acc4.json", db.runs_dir / syn.id / "netlist.json")
    path = json.loads((D / "acc4_path.json").read_text())
    db.update_run(syn.id, artifacts={"netlist.v": "netlist.v", "netlist.json": "netlist.json"},
                  metrics={"tool": "yosys+opensta", "top": "acc4", "netlist": "netlist.v", "paths": [path]})
    nosyn = db.create_run("synth", "dc", status="error")
    c = TestClient(create_app(C.load(proj)))
    c.ids = {"sim": sim.id, "synth": syn.id, "nonetlist": nosyn.id}
    return c


def test_hierarchy_and_block(client):
    h = client.get("/api/design/hierarchy").json()
    assert h["top"] == "xm_top" and "xm_regfile" in h["modules"]
    assert {"name": "u_rf", "module": "xm_regfile"} in h["modules"]["xm_top"]["instances"]
    b = client.get("/api/design/block", params={"module": "xm_top"}).json()
    assert "u_rf" in {n["id"] for n in b["nodes"]} and b["edges"]
    assert client.get("/api/design/block").json()["module"] == "xm_top"
    assert client.get("/api/design/block", params={"module": "nope"}).status_code == 404



def test_signal_location(client):
    # a waveform path (testbench-rooted) maps onto its declaration in the design
    loc = client.get("/api/design/signal", params={"path": "tb_xm.dut.lut_out"}).json()
    assert loc["module"] == "xm_top" and loc["file"] == "rtl/xm_top.sv" and loc["width"] == 8
    assert isinstance(loc["line"], int) and loc["line"] > 0
    assert client.get("/api/design/signal", params={"path": "tb_xm.no_such_signal"}).status_code == 404


def test_cone(client):
    rid = client.ids["synth"]
    r = client.get("/api/design/cone", params={"run": rid, "path": 0, "depth": 2}).json()
    assert r["unresolved"] == [] and r["netlist"] == "netlist.v"
    assert any(n["on_path"] for n in r["nodes"]) and any(e["on_path"] for e in r["edges"])
    r2 = client.get("/api/design/cone", params={"run": rid, "net": "acc[2]", "depth": 3}).json()
    assert r2["root_net"] == "acc[2]" and len(r2["nodes"]) > 1
    assert client.get("/api/design/cone", params={"run": rid}).status_code == 400
    assert client.get("/api/design/cone", params={"run": rid, "path": 5}).status_code == 404
    assert client.get("/api/design/cone", params={"run": rid, "net": "zzz"}).status_code == 404
    assert client.get("/api/design/cone", params={"run": "r_9999", "net": "a"}).status_code == 404
    assert client.get("/api/design/cone", params={"run": client.ids["nonetlist"], "net": "a"}).status_code == 404


def test_xtrace(client):
    r = client.get("/api/xtrace", params={"signal": "tb_xm.rd_out", "time": 75001}).json()
    assert r["signal"] == "tb_xm.rd_out" and r["value"] == "8'bxxxxxxxx"

    def leaves(n):
        return ([n] if n["cause"] else []) + [x for c in n["children"] for x in leaves(c)]
    assert [(x["signal"], x["cause"]) for x in leaves(r)] == [("tb_xm.dut.u_rf.regs[5]", "uninit_memory")]
    r = client.get("/api/xtrace", params={"signal": "tb_xm.lut_out", "time": 85001,
                                          "run": client.ids["sim"], "depth": 4}).json()
    assert leaves(r)[0]["cause"] == "index_out_of_range"
    assert client.get("/api/xtrace", params={"signal": "x", "time": 1, "run": "r_9999"}).status_code == 404
    assert client.get("/api/xtrace", params={"signal": "x", "time": 1,
                                             "run": client.ids["synth"]}).status_code == 404
