"""Removing runs (DELETE /api/runs/{id}, svkit runs rm, RunDB.delete_run)."""
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from svkit import config as C
from svkit.core.rundb import RunDB
from svkit.server.app import create_app


def _done(db, name=None, **kw):
    r = db.create_run("synth", "dc", status="done", name=name, **kw)
    d = db.run_dir(r.id)
    (d / "area.rpt").write_text(f"area of {r.id}\n")
    (d / "svkit.log").write_text("log\n")
    db.update_run(r.id, metrics={"period": 4000.0}, artifacts={"area.rpt": "area.rpt"})
    return db.get_run(r.id)


def _reuse(db, orig):
    r = db.create_run("synth", "dc", status="done")
    db.update_run(r.id, cached_from=orig.id, metrics=orig.metrics,
                  artifacts={n: f"../{orig.id}/{p}" for n, p in orig.artifacts.items()})
    return db.get_run(r.id)


def test_delete_plain_run(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    a = _done(db)
    db.add_tag(a.id, "keep")
    assert db.delete_run(a.id) == []
    with pytest.raises(KeyError):
        db.get_run(a.id)
    assert not (db.runs_dir / a.id).exists()
    with pytest.raises(KeyError):
        db.delete_run(a.id)
    busy = db.create_run("synth", "dc", status="running")
    with pytest.raises(ValueError, match="still running"):
        db.delete_run(busy.id)


def test_ids_are_never_reused(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    a, b = _done(db), _done(db)
    db.delete_run(b.id)                                  # the newest one
    c = db.create_run("sim", "verilator", test="t", status="passed")
    assert (a.id, b.id, c.id) == ("r_0001", "r_0002", "r_0003")
    g = db.create_group("matrix", {})
    with db._conn() as conn:
        conn.execute("DELETE FROM groups WHERE id = ?", (g.id,))
    assert db.create_group("matrix", {}).id == "g_0002"
    assert RunDB(tmp_path / ".svkit").create_run("sim", "x", status="passed").id == "r_0004"   # survives reopen


def test_removing_an_original_hands_its_files_to_a_reuser(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    orig = _done(db, name="baseline")
    first, second = _reuse(db, orig), _reuse(db, orig)
    assert db.delete_run(orig.id) == [first.id, second.id]
    h = db.get_run(first.id)
    assert h.cached_from is None and h.artifacts == {"area.rpt": "area.rpt"}
    assert (db.runs_dir / first.id / "area.rpt").read_text() == f"area of {orig.id}\n"
    assert (db.runs_dir / first.id / "svkit.log").is_file() and not (db.runs_dir / orig.id).exists()
    s = db.get_run(second.id)
    assert s.cached_from == first.id and s.artifacts == {"area.rpt": f"../{first.id}/area.rpt"}
    assert (db.runs_dir / s.id / s.artifacts["area.rpt"]).resolve().read_text() == f"area of {orig.id}\n"
    # removing a reuser leaves the original alone
    assert db.delete_run(second.id) == []
    assert (db.runs_dir / first.id / "area.rpt").is_file()


def test_cache_survives_a_removed_original(tmp_path):
    """A cache hit whose original was removed outside delete_run (old DB) still resolves."""
    from svkit.core.runner import Runner
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB.for_project(tmp_path)
    orig = _done(db, cache_key="k")
    reuse = _reuse(db, orig)
    db.update_run(reuse.id, cache_key="k")
    with db._conn() as c:
        c.execute("DELETE FROM runs WHERE id = ?", (orig.id,))
    r = Runner(C.load(tmp_path), db)
    hit = r._cached("k", "synth", "dc")
    assert hit is not None and hit.cached_from == reuse.id


def test_delete_route_and_events(tmp_path, monkeypatch):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB.for_project(tmp_path)
    orig = _done(db)
    reuse = _reuse(db, orig)
    busy = db.create_run("synth", "dc", status="queued")
    from svkit.core.events import EventBus
    events = []
    real = EventBus.publish
    monkeypatch.setattr(EventBus, "publish", lambda self, ev: (events.append(ev), real(self, ev))[1])
    c = TestClient(create_app(C.load(tmp_path)))
    res = c.delete(f"/api/runs/{orig.id}")
    assert res.status_code == 200 and res.json() == {"deleted": orig.id, "changed": [reuse.id]}
    events = [e for e in events if e["type"] in ("run.deleted", "run.updated")]
    assert events[0] == {"type": "run.deleted", "run_id": orig.id}
    assert events[1]["type"] == "run.updated" and events[1]["run"]["id"] == reuse.id
    assert c.get(f"/api/runs/{orig.id}").status_code == 404
    assert c.get(f"/api/runs/{reuse.id}/artifacts/area.rpt").text == f"area of {orig.id}\n"
    assert c.delete(f"/api/runs/{orig.id}").status_code == 404
    assert c.delete(f"/api/runs/{busy.id}").status_code == 409
    assert [r["id"] for r in c.get("/api/runs").json()] == [busy.id, reuse.id]


def test_cli_rm(tmp_path, monkeypatch):
    from svkit.cli import app
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    monkeypatch.chdir(tmp_path)
    db = RunDB.for_project(tmp_path)
    a, b = _done(db, name="try 1"), _done(db)
    cli = CliRunner()
    res = cli.invoke(app, ["runs", "rm", a.id], input="n\n")
    assert res.exit_code != 0 and db.get_run(a.id)
    res = cli.invoke(app, ["runs", "rm", a.id, b.id], input="y\n")
    assert res.exit_code == 0, res.output
    assert "try 1" in res.output and f"removed {a.id}" in res.output and f"removed {b.id}" in res.output
    assert db.list_runs() == []
    assert cli.invoke(app, ["runs", "rm", "r_0099", "-y"]).exit_code != 0


def _sim(db, gid, test, status="passed"):
    r = db.create_run("sim", "verilator", test=test, status=status, group_id=gid)
    (db.run_dir(r.id) / "svkit.log").write_text("log\n")
    db.update_run(r.id, metrics={"cycles": 100, "instrs": 50})
    return db.get_run(r.id)


def test_delete_group_removes_the_batch_and_only_its_runs(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    g1, g2 = db.create_group("sim-all", {"tests": ["a", "b"]}), db.create_group("sim-all", {"tests": ["a"]})
    a1, b1, a2 = _sim(db, g1.id, "a"), _sim(db, g1.id, "b"), _sim(db, g2.id, "a")
    outside = _reuse(db, a1)                                # reused a result of the removed batch
    removed, changed = db.delete_group(g1.id)
    assert removed == [a1.id, b1.id] and changed == [outside.id]
    with pytest.raises(KeyError):
        db.get_group(g1.id)
    assert {r.id for r in db.list_runs()} == {a2.id, outside.id}
    assert not (db.runs_dir / a1.id).exists() and not (db.runs_dir / b1.id).exists()
    assert db.get_run(outside.id).cached_from is None and (db.runs_dir / outside.id / "svkit.log").is_file()
    assert db.get_group(g2.id)
    assert db.create_group("sim-all", {}).id == "g_0003"    # batch ids are not reused either
    with pytest.raises(KeyError):
        db.delete_group(g1.id)


def test_delete_group_refuses_while_running_and_removes_nothing(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    g = db.create_group("sim-all", {})
    done, busy = _sim(db, g.id, "a"), _sim(db, g.id, "b", status="running")
    with pytest.raises(ValueError, match="queued or running"):
        db.delete_group(g.id)
    assert db.get_run(done.id) and db.get_run(busy.id) and db.get_group(g.id)


def test_delete_group_route_and_cli(tmp_path, monkeypatch):
    from svkit.cli import app
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    monkeypatch.chdir(tmp_path)
    db = RunDB.for_project(tmp_path)
    g1, g2, g3 = (db.create_group("sim-all", {}) for _ in range(3))
    r1 = _sim(db, g1.id, "a")
    _sim(db, g2.id, "a")
    _sim(db, g3.id, "a", status="queued")
    c = TestClient(create_app(C.load(tmp_path)))
    res = c.delete(f"/api/groups/{g1.id}")
    assert res.status_code == 200 and res.json() == {"deleted": g1.id, "runs": [r1.id], "changed": []}
    assert c.get(f"/api/groups/{g1.id}").status_code == 404
    assert c.delete(f"/api/groups/{g1.id}").status_code == 404
    assert c.delete(f"/api/groups/{g3.id}").status_code == 409
    res = CliRunner().invoke(app, ["runs", "rm", g2.id, "-y"])
    assert res.exit_code == 0 and f"removed batch {g2.id} (1 run)" in res.output, res.output
    assert [g.id for g in db.list_groups()] == [g3.id]
