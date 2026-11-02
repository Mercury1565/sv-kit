from fastapi.testclient import TestClient

from svkit import config as C
from svkit.core.rundb import RunDB
from svkit.server.app import create_app


def test_api_runs(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    cfg = C.load(tmp_path)
    db = RunDB.for_project(tmp_path)
    r = db.create_run("sim", "verilator", test="t1", status="passed")
    (db.runs_dir / r.id / "sim.log").write_text("hello\n")
    db.update_run(r.id, artifacts={"sim.log": "sim.log"}, metrics={"tool": "verilator", "test": "t1"})
    c = TestClient(create_app(cfg))
    assert c.get("/api/project").json()["name"] == "demo"
    runs = c.get("/api/runs").json()
    assert runs[0]["id"] == r.id and "metrics" not in runs[0]
    assert c.get(f"/api/runs/{r.id}").json()["metrics"]["test"] == "t1"
    assert c.get(f"/api/runs/{r.id}/artifacts/sim.log").text == "hello\n"
    assert c.get(f"/api/runs/{r.id}/artifacts/../../runs.sqlite").status_code == 404
    assert c.post(f"/api/runs/{r.id}/tags", json={"tag": "x"}).json()["tags"] == ["x"]
    assert c.get("/api/runs/r_9999").status_code == 404
    assert c.get("/api/source", params={"file": "/etc/passwd"}).status_code == 404
