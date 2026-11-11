"""Run and batch names (DB v4) and running a chosen subset of tests as one batch."""
import shutil
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from svkit import config as C
from svkit.config import ConfigError
from svkit.core.rundb import NAME_MAX, RunDB, clean_name
from svkit.core.runner import Runner
from svkit.server.app import create_app


def test_clean_name():
    assert clean_name(None) is None
    assert clean_name("   ") is None
    assert clean_name("  fast\n  branch\tpredictor ") == "fast branch predictor"
    assert len(clean_name("x" * 500)) == NAME_MAX


def test_v3_database_gains_name_columns(tmp_path):
    state = tmp_path / ".svkit"
    state.mkdir()
    c = sqlite3.connect(state / "runs.sqlite")
    c.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('version', '3');
        CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, adapter TEXT NOT NULL, test TEXT,
            status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL, duration_s REAL,
            git_sha TEXT, git_dirty INTEGER, source_hash TEXT, knobs TEXT NOT NULL DEFAULT '{}', experiment TEXT,
            cache_key TEXT, cached_from TEXT, exit_code INTEGER, metrics TEXT, artifacts TEXT NOT NULL DEFAULT '{}',
            group_id TEXT, message TEXT, flow TEXT, owner TEXT);
        CREATE TABLE groups (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at REAL NOT NULL,
            status TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}', result TEXT);
    """)
    c.execute("INSERT INTO runs (id, kind, adapter, status, created_at) VALUES ('r_0001', 'synth', 'dc', 'done', ?)",
              (time.time(),))
    c.execute("INSERT INTO groups (id, kind, created_at, status) VALUES ('g_0001', 'sim-all', ?, 'done')",
              (time.time(),))
    c.commit()
    c.close()
    db = RunDB(state)
    assert db.get_run("r_0001").name is None and db.get_group("g_0001").name is None
    db.set_name("r_0001", "baseline")
    db.set_name("g_0001", "before the fix")
    assert db.get_run("r_0001").name == "baseline"
    assert db.get_group("g_0001").name == "before the fix"
    db.set_name("r_0001", "")
    assert db.get_run("r_0001").name is None
    with pytest.raises(KeyError):
        db.set_name("r_0099", "x")
    with sqlite3.connect(state / "runs.sqlite") as c2:
        assert c2.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0] == "4"


def test_rename_routes(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB.for_project(tmp_path)
    r = db.create_run("synth", "yosys", status="done", name="first try")
    g = db.create_group("sim-all", {"tests": ["a"]}, name="nightly")
    c = TestClient(create_app(C.load(tmp_path)))
    assert c.get(f"/api/runs/{r.id}").json()["name"] == "first try"
    assert c.get("/api/runs").json()[0]["name"] == "first try"
    assert c.patch(f"/api/runs/{r.id}", json={"name": "  smaller  area "}).json()["name"] == "smaller area"
    assert c.patch(f"/api/runs/{r.id}", json={"name": None}).json()["name"] is None
    assert c.patch(f"/api/runs/{r.id}", json={}).status_code == 400
    assert c.patch(f"/api/runs/{r.id}", json={"name": 3}).status_code == 400
    assert c.patch("/api/runs/r_9999", json={"name": "x"}).status_code == 404
    assert c.get("/api/groups", params={"kind": "sim-all"}).json()[0]["name"] == "nightly"
    assert c.patch(f"/api/groups/{g.id}", json={"name": "weekly"}).json()["name"] == "weekly"
    assert c.patch("/api/groups/g_9999", json={"name": "x"}).status_code == 404


def test_compare_labels_use_names(tmp_path):
    from svkit.analysis.compare import compare
    db = RunDB(tmp_path / ".svkit")
    a = db.create_run("synth", "dc", experiment="baseline", status="done", name="before retiming")
    b = db.create_run("synth", "dc", experiment="baseline", status="done")
    res = compare([db.get_run(a.id), db.get_run(b.id)])
    assert res.labels == ["before retiming", "baseline"]


TOML = """
[project]
name = "subset"
top = "top"
sources = { glob = ["*.sv"] }

[flows.sim.custom]
cmd = "make out/{test}.out"
golden = { actual = "out/{test}.out", expected = "exp/{test}.out" }
tests = { names = ["a", "b", "c", "d"] }
"""

MAKEFILE = """\
out/%.out:
\tmkdir -p out; echo ok > $@
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    from svkit.adapters import registry
    from svkit.adapters.sim.verilator import VerilatorAdapter

    class Custom(VerilatorAdapter):       # the generic wrap path, without a tool requirement
        name = "custom"
        tools = ()
    monkeypatch.setitem(registry.BUILTIN, "custom", f"{__name__}:Custom")
    globals()["Custom"] = Custom
    (tmp_path / "top.sv").write_text("module top; endmodule\n")
    (tmp_path / "Makefile").write_text(MAKEFILE)
    (tmp_path / "exp").mkdir()
    for t in "abcd":
        (tmp_path / "exp" / f"{t}.out").write_text("ok\n")
    (tmp_path / "svkit.toml").write_text(TOML)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.skipif(not shutil.which("make"), reason="needs make")
def test_batch_of_chosen_tests(project):
    r = Runner(C.load(project))
    gid, runs = r.sim_all(tests=["c", "a", "c"], jobs=1, use_cache=False, name="just two")
    assert [x.test for x in runs] == ["c", "a"] and all(x.status == "passed" for x in runs)
    g = r.db.get_group(gid)
    assert g.name == "just two" and g.params["tests"] == ["c", "a"] and g.params["subset"] is True
    assert all(x.group_id == gid and x.name is None for x in runs)     # the name belongs to the batch
    gid2, runs2 = r.sim_all(jobs=1)
    assert len(runs2) == 4 and r.db.get_group(gid2).params["subset"] is False
    with pytest.raises(ConfigError, match="unknown test.*zz"):
        r.sim_all(tests=["a", "zz"], jobs=1)
    one = r.sim("b", name="  solo ")
    assert one.name == "solo"


@pytest.mark.skipif(not shutil.which("make"), reason="needs make")
def test_cli_tests_and_names(project):
    from svkit.cli import app
    cli = CliRunner()
    res = cli.invoke(app, ["sim", "a", "b", "--name", "pair", "--no-cache"])
    assert res.exit_code == 0, res.output
    assert "batch g_0001" in res.output and '"pair"' in res.output
    db = RunDB.for_project(project)
    assert db.get_group("g_0001").params["tests"] == ["a", "b"]
    res = cli.invoke(app, ["sim", "a", "--all"])
    assert res.exit_code != 0 and "not both" in res.output
    res = cli.invoke(app, ["sim", "d", "--name", "single"])
    assert res.exit_code == 0, res.output
    rid = db.list_runs(limit=1)[0].id
    assert db.get_run(rid).name == "single"
    res = cli.invoke(app, ["rename", "g_0001", "renamed pair"])
    assert res.exit_code == 0 and db.get_group("g_0001").name == "renamed pair"
    res = cli.invoke(app, ["rename", rid])
    assert res.exit_code == 0 and "name removed" in res.output and db.get_run(rid).name is None
    assert cli.invoke(app, ["rename", "r_9999", "x"]).exit_code != 0


@pytest.mark.skipif(not shutil.which("make"), reason="needs make")
def test_job_runs_chosen_tests_with_a_name(project):
    c = TestClient(create_app(C.load(project)))
    assert c.post("/api/jobs", json={"kind": "sim-all", "args": {"tests": "a"}}).status_code == 400
    jid = c.post("/api/jobs", json={"kind": "sim-all", "args": {"tests": ["b", "d"], "name": "from the ui",
                                                                 "jobs": 1}}).json()["job_id"]
    for _ in range(200):
        job = next(j for j in c.get("/api/jobs").json() if j["job_id"] == jid)
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    g = c.get(f"/api/groups/{job['group_id']}").json()
    assert g["name"] == "from the ui" and sorted(x["test"] for x in g["runs"]) == ["b", "d"]
