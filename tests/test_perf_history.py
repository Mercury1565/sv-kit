"""One test's CPI / TPI across every run of it (GET /api/perf/history)."""
from fastapi.testclient import TestClient

from svkit import config as C
from svkit.analysis.perf import perf_history
from svkit.core.rundb import RunDB
from svkit.server.app import create_app


def _sim(db, test, cycles, instrs, t, group=None, **kw):
    r = db.create_run("sim", "verilator", test=test, status="passed", group_id=group, **kw)
    db.update_run(r.id, metrics={"perf": {"test": test, "cycles": cycles, "instrs": instrs, "stalls": {}}})
    with db._conn() as c:                                   # control the clock: created_at decides the period
        c.execute("UPDATE runs SET created_at = ? WHERE id = ?", (t, r.id))
    return r


def _synth(db, period, t):
    r = db.create_run("synth", "yosys", status="done")
    db.update_run(r.id, metrics={"period": period})
    with db._conn() as c:
        c.execute("UPDATE runs SET created_at = ? WHERE id = ?", (t, r.id))
    return r


def _sweep(db, t_min, t):
    g = db.create_group("sweep", {"tol": 50})
    db.update_group(g.id, status="done", result={"group_id": g.id, "tol": 50, "t_min": t_min, "status": "done"})
    with db._conn() as c:
        c.execute("UPDATE groups SET created_at = ? WHERE id = ?", (t, g.id))
    return g


def test_history_follows_the_period_of_its_time(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB.for_project(tmp_path)
    g = db.create_group("sim-all", {"tests": ["fib"]}, name="after the fix")
    first = _sim(db, "fib", 2000, 1000, t=100.0)            # before any synthesis: earliest period known
    s1 = _synth(db, 5000.0, t=200.0)
    second = _sim(db, "fib", 1500, 1000, t=300.0, group=g.id, name="tuned")
    s2 = _synth(db, 4000.0, t=400.0)
    third = _sim(db, "fib", 1200, 1000, t=500.0)
    _sim(db, "gcd", 9999, 1, t=600.0)                       # another test: not in fib's history
    nope = db.create_run("sim", "verilator", test="fib", status="error")   # no perf numbers: skipped

    h = perf_history(C.load(tmp_path), db, "fib")
    assert [p["run_id"] for p in h] == [first.id, second.id, third.id]       # oldest first
    assert [p["cpi"] for p in h] == [2.0, 1.5, 1.2]
    assert [p["period"] for p in h] == [5000.0, 5000.0, 4000.0]
    assert [p["period_source"] for p in h] == [f"synth:{s1.id}", f"synth:{s1.id}", f"synth:{s2.id}"]
    assert [p["tpi"] for p in h] == [10000.0, 7500.0, 4800.0]
    assert h[1]["group_id"] == g.id and h[1]["group_name"] == "after the fix" and h[1]["run_name"] == "tuned"
    assert h[0]["group_id"] is None and nope.id not in {p["run_id"] for p in h}

    c = TestClient(create_app(C.load(tmp_path)))
    res = c.get("/api/perf/history", params={"test": "fib"}).json()
    assert [p["run_id"] for p in res] == [first.id, second.id, third.id]
    assert [p["run_id"] for p in c.get("/api/perf/history", params={"test": "fib", "limit": 2}).json()] == \
        [second.id, third.id]                               # the limit keeps the newest
    assert c.get("/api/perf/history", params={"test": "nothing"}).json() == []
    assert c.get("/api/perf/history").status_code == 422


def test_history_uses_clock_period_from_svkit_toml(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n'
                                         '[knobs]\nclock_period = { file = "clk.txt", default = 3000 }\n')
    db = RunDB.for_project(tmp_path)
    _sim(db, "fib", 2000, 1000, t=100.0)
    _synth(db, 5000.0, t=200.0)                             # ignored: svkit.toml sets the period
    _sim(db, "fib", 1500, 1000, t=300.0)
    h = perf_history(C.load(tmp_path), db, "fib")
    assert [(p["period"], p["period_source"], p["tpi"]) for p in h] == \
        [(3000.0, "svkit.toml", 6000.0), (3000.0, "svkit.toml", 4500.0)]


def test_history_period_from_sweep_follows_its_time_and_manual_is_fixed(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n'
                                         '[knobs]\nclock_period = { file = "clk.txt", default = 3000 }\n')
    cfg = C.load(tmp_path)
    db = RunDB.for_project(tmp_path)
    _sim(db, "fib", 2000, 1000, t=100.0)                    # before any sweep: the synth period then
    _synth(db, 5000.0, t=50.0)
    g1 = _sweep(db, 4000.0, t=200.0)
    _sim(db, "fib", 1500, 1000, t=300.0)
    g2 = _sweep(db, 2500.0, t=400.0)
    _sim(db, "fib", 1200, 1000, t=500.0)
    h = perf_history(cfg, db, "fib", period_from="sweep")
    assert [p["period"] for p in h] == [5000.0, 4000.0, 2500.0]   # sweep before synth, as before v0.12
    assert [p["period_source"] for p in h][1:] == [f"sweep:{g1.id}", f"sweep:{g2.id}"]

    h = perf_history(cfg, db, "fib", period_from="manual", period=1000.0)
    assert [(p["period"], p["period_source"], p["tpi"]) for p in h] == \
        [(1000.0, "manual", 2000.0), (1000.0, "manual", 1500.0), (1000.0, "manual", 1200.0)]

    c = TestClient(create_app(cfg))
    res = c.get("/api/perf/history", params={"test": "fib", "period_from": "sweep"}).json()
    assert [p["period"] for p in res] == [5000.0, 4000.0, 2500.0]
    res = c.get("/api/perf", params={"period_from": "manual", "period": 800}).json()
    assert res[0]["period"] == 800.0 and res[0]["period_source"] == "manual" and res[0]["tpi"] == 960.0
    assert c.get("/api/perf").json()[0]["period_source"] == "svkit.toml"
    assert c.get("/api/perf", params={"period_from": "manual"}).status_code == 422
    assert c.get("/api/perf", params={"period_from": "manual", "period": 0}).status_code == 422
    assert c.get("/api/perf", params={"period_from": "nope"}).status_code == 422


def test_history_without_any_period(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB(tmp_path / ".svkit")
    _sim(db, "t", 10, 0, t=1.0)                             # zero instructions: no CPI, no TPI
    _sim(db, "t", 30, 10, t=2.0)
    h = perf_history(C.load(tmp_path), db, "t")
    assert [(p["cpi"], p["period"], p["tpi"]) for p in h] == [(None, None, None), (3.0, None, None)]
