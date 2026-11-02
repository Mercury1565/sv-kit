from svkit import config as C
from svkit.analysis.perf import current_period, perf_table
from svkit.core.rundb import RunDB


def _cfg(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    return C.load(tmp_path)


def _sim(db, test, cycles, instrs, group=None, cpi=None):
    r = db.create_run("sim", "verilator", test=test, status="passed", group_id=group)
    perf = {"test": test, "cycles": cycles, "instrs": instrs, "cpi": cpi}
    db.update_run(r.id, metrics={"tool": "verilator", "test": test, "passed": True, "perf": perf})
    return r


def test_tpi_follows_latest_synth_then_sweep(tmp_path):
    cfg = _cfg(tmp_path)
    db = RunDB.for_project(tmp_path)
    _sim(db, "fib", 1000, 500, cpi=2.0)
    _sim(db, "sort", 300, 200)                       # cpi derived from cycles / instrs
    nop = db.create_run("sim", "verilator", test="noperf", status="passed")
    db.update_run(nop.id, metrics={"tool": "verilator", "test": "noperf"})

    rows = perf_table(cfg, db)
    assert [p.test for p in rows] == ["fib", "sort"]
    assert rows[1].cpi == 1.5
    assert all(p.tpi is None and p.period is None for p in rows)       # no synth / sweep yet

    s1 = db.create_run("synth", "yosys", status="done")
    db.update_run(s1.id, metrics={"tool": "yosys", "top": "core", "period": 2000.0})
    err = db.create_run("synth", "yosys", status="error")                # ignored
    rows = perf_table(cfg, db)
    assert rows[0].period == 2000.0 and rows[0].period_source == f"synth:{s1.id}" and rows[0].tpi == 4000.0

    s2 = db.create_run("synth", "yosys", status="done")                  # newer synth run lands
    db.update_run(s2.id, metrics={"tool": "yosys", "top": "core", "period": 1600.0})
    assert perf_table(cfg, db)[0].tpi == 3200.0
    assert err.id

    running = db.create_group("sweep", {"tol": 50})                       # unfinished sweep: ignored
    db.update_group(running.id, result={"group_id": running.id, "tol": 50, "t_min": 900.0, "status": "running"})
    assert perf_table(cfg, db)[0].period == 1600.0
    g = db.create_group("sweep", {"tol": 50})
    db.update_group(g.id, status="done", result={"group_id": g.id, "tol": 50, "t_min": 1250.0, "status": "done"})
    rows = perf_table(cfg, db)
    assert rows[0].period == 1250.0 and rows[0].period_source == f"sweep:{g.id}"
    assert rows[0].tpi == 2500.0 and rows[1].tpi == 1875.0
    assert current_period(db) == (1250.0, f"sweep:{g.id}")


def test_latest_run_per_test_and_group_filter(tmp_path):
    cfg = _cfg(tmp_path)
    db = RunDB.for_project(tmp_path)
    g1 = db.create_group("sim-all")
    _sim(db, "fib", 1000, 500, group=g1.id)
    _sim(db, "fib", 900, 500)                         # newer, outside the group
    assert perf_table(cfg, db)[0].cycles == 900
    assert perf_table(cfg, db, group_id=g1.id)[0].cycles == 1000
    assert perf_table(cfg, db, group_id="g_9999") == []
