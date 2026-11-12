"""One test's CPI / TPI across every run of it (GET /api/perf/history), and the period setting."""
from fastapi.testclient import TestClient

from svkit import config as C
from svkit.analysis import perf as PF
from svkit.analysis.perf import perf_history
from svkit.core.rundb import RunDB
from svkit.server.app import create_app


def _at(db, table, rid, t):
    with db._conn() as c:                                   # control the clock
        c.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (t, rid))


def _sim(db, test, cycles, instrs, t, group=None, cfg=None, **kw):
    r = db.create_run("sim", "verilator", test=test, status="passed", group_id=group, **kw)
    db.update_run(r.id, metrics={"perf": {"test": test, "cycles": cycles, "instrs": instrs, "stalls": {}}})
    _at(db, "runs", r.id, t)
    if cfg is not None:                                     # it finishes now: its period is fixed
        PF.fix_period(cfg, db, r.id)
    return r


def _synth(db, period, t):
    r = db.create_run("synth", "yosys", status="done")
    db.update_run(r.id, metrics={"period": period})
    _at(db, "runs", r.id, t)
    return r


def _sweep(db, t_min, t):
    g = db.create_group("sweep", {"tol": 50})
    db.update_group(g.id, status="done", result={"group_id": g.id, "tol": 50, "t_min": t_min, "status": "done"})
    _at(db, "groups", g.id, t)
    return g


def _toml(tmp_path, knob=""):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n' + knob)
    return C.load(tmp_path)


def test_each_run_keeps_the_period_it_finished_with(tmp_path):
    cfg = _toml(tmp_path, '[knobs]\nclock_period = { file = "clk.txt", default = 3000 }\n')
    db = RunDB.for_project(tmp_path)
    g = db.create_group("sim-all", {"tests": ["fib"]}, name="after the fix")
    first = _sim(db, "fib", 2000, 1000, t=100.0, cfg=cfg)                     # svkit.toml: 3000
    sw = _sweep(db, 2500.0, t=200.0)
    PF.set_period_choice(db, "sweep")
    second = _sim(db, "fib", 1500, 1000, t=300.0, group=g.id, name="tuned", cfg=cfg)   # sweep: 2500
    PF.set_period_choice(db, "manual", 2000)
    third = _sim(db, "fib", 1200, 1000, t=500.0, cfg=cfg)                     # manual: 2000
    _sim(db, "gcd", 9999, 1, t=600.0, cfg=cfg)                                # another test
    nope = db.create_run("sim", "verilator", test="fib", status="error")      # no perf numbers: skipped

    h = perf_history(cfg, db, "fib")
    assert [p["run_id"] for p in h] == [first.id, second.id, third.id]       # oldest first
    assert [p["cpi"] for p in h] == [2.0, 1.5, 1.2]
    assert [(p["period"], p["period_source"]) for p in h] == \
        [(3000.0, "svkit.toml"), (2500.0, f"sweep:{sw.id}"), (2000.0, "manual")]
    assert [p["tpi"] for p in h] == [6000.0, 3750.0, 2400.0]
    assert h[1]["group_id"] == g.id and h[1]["group_name"] == "after the fix" and h[1]["run_name"] == "tuned"
    assert h[0]["group_id"] is None and nope.id not in {p["run_id"] for p in h}

    PF.set_period_choice(db, "toml")                                          # changing it moves nothing
    assert [p["period"] for p in perf_history(cfg, db, "fib")] == [3000.0, 2500.0, 2000.0]

    c = TestClient(create_app(cfg))
    res = c.get("/api/perf/history", params={"test": "fib"}).json()
    assert [p["run_id"] for p in res] == [first.id, second.id, third.id]
    assert [p["run_id"] for p in c.get("/api/perf/history", params={"test": "fib", "limit": 2}).json()] == \
        [second.id, third.id]                               # the limit keeps the newest
    assert c.get("/api/perf/history", params={"test": "nothing"}).json() == []
    assert c.get("/api/perf/history").status_code == 422


def test_old_runs_follow_the_sweep_of_their_time_then_stay(tmp_path):
    cfg = _toml(tmp_path, '[knobs]\nclock_period = { file = "clk.txt", default = 3000 }\n')
    db = RunDB.for_project(tmp_path)
    s = _synth(db, 5000.0, t=50.0)
    _sim(db, "fib", 2000, 1000, t=100.0)                    # recorded before periods were fixed
    g1 = _sweep(db, 4000.0, t=200.0)
    _sim(db, "fib", 1500, 1000, t=300.0)
    h = perf_history(cfg, db, "fib")
    assert [(p["period"], p["period_source"]) for p in h] == [(5000.0, f"synth:{s.id}"), (4000.0, f"sweep:{g1.id}")]
    _sweep(db, 100.0, t=50.0)                               # even an older sweep added later moves nothing
    assert [p["period"] for p in perf_history(cfg, db, "fib")] == [5000.0, 4000.0]


def test_period_setting_route(tmp_path):
    cfg = _toml(tmp_path, '[knobs]\nclock_period = { file = "clk.txt", default = 3000 }\n')
    c = TestClient(create_app(cfg))
    assert c.get("/api/perf/period").json() == {"from": "toml", "period": 3000.0, "source": "svkit.toml"}
    assert c.put("/api/perf/period", json={"from": "manual", "ps": 800}).json() == \
        {"from": "manual", "ps": 800.0, "period": 800.0, "source": "manual"}
    assert c.put("/api/perf/period", json={"from": "sweep"}).json() == \
        {"from": "sweep", "period": None, "source": None}   # no sweep or synth yet
    for bad in ({"from": "manual"}, {"from": "manual", "ps": 0}, {"from": "nope"}, {}):
        assert c.put("/api/perf/period", json=bad).status_code == 422
    assert c.get("/api/perf/period").json()["from"] == "sweep"


def test_history_without_any_period(tmp_path):
    cfg = _toml(tmp_path)
    db = RunDB(tmp_path / ".svkit")
    _sim(db, "t", 10, 0, t=1.0, cfg=cfg)                    # zero instructions: no CPI, no TPI
    _sim(db, "t", 30, 10, t=2.0, cfg=cfg)
    h = perf_history(cfg, db, "t")
    assert [(p["cpi"], p["period"], p["tpi"]) for p in h] == [(None, None, None), (3.0, None, None)]
