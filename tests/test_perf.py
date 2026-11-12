import pytest
from svkit import config as C
from svkit.analysis import perf as PF
from svkit.analysis.perf import current_period, fix_period, perf_table
from svkit.core.rundb import RunDB


def _cfg(tmp_path, knob=""):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n' + knob)
    return C.load(tmp_path)


TOML_1250 = '[knobs]\nclock_period = { file = "clk.txt", unit = "ns", default = 1250 }\n'


def _sim(db, test, cycles, instrs, group=None, cpi=None, cfg=None):
    """A sim run that reported perf; with `cfg`, it finishes now (its clock period is fixed)."""
    r = db.create_run("sim", "verilator", test=test, status="passed", group_id=group)
    perf = {"test": test, "cycles": cycles, "instrs": instrs, "cpi": cpi}
    db.update_run(r.id, metrics={"tool": "verilator", "test": test, "passed": True, "perf": perf})
    if cfg is not None:
        fix_period(cfg, db, r.id)
    return r


def _synth(db, period):
    s = db.create_run("synth", "yosys", status="done")
    db.update_run(s.id, metrics={"tool": "yosys", "top": "core", "period": period})
    return s


def _sweep(db, t_min, status="done"):
    g = db.create_group("sweep", {"tol": 50})
    db.update_group(g.id, status=status, result={"group_id": g.id, "tol": 50, "t_min": t_min, "status": status})
    return g


def test_period_choice_setting(tmp_path):
    db = RunDB.for_project(tmp_path)
    assert PF.period_choice(db) == {"from": "toml"}                         # the default
    assert PF.set_period_choice(db, "sweep") == {"from": "sweep"}
    assert PF.period_choice(RunDB.for_project(tmp_path)) == {"from": "sweep"}   # kept in the run DB
    assert PF.set_period_choice(db, "manual", 900) == {"from": "manual", "ps": 900.0}
    for bad in (("manual", None), ("manual", 0), ("manual", -5), ("nope", None)):
        with pytest.raises(ValueError):
            PF.set_period_choice(db, *bad)
    assert PF.period_choice(db) == {"from": "manual", "ps": 900.0}            # unchanged by bad input


def test_current_period_per_choice(tmp_path):
    cfg = _cfg(tmp_path, TOML_1250)
    db = RunDB.for_project(tmp_path)
    assert current_period(cfg, db) == (1250.0, "svkit.toml")
    assert current_period(cfg, db, {"from": "sweep"}) == (None, None)        # nothing known yet
    s = _synth(db, 2000.0)
    _synth_err = db.create_run("synth", "yosys", status="error")               # ignored
    assert current_period(cfg, db, {"from": "sweep"}) == (2000.0, f"synth:{s.id}")   # no sweep: synth
    _sweep(db, 700.0, status="running")                                       # unfinished: ignored
    g = _sweep(db, 900.0)
    assert current_period(cfg, db, {"from": "sweep"}) == (900.0, f"sweep:{g.id}")
    assert current_period(cfg, db, {"from": "manual", "ps": 1000}) == (1000.0, "manual")
    assert current_period(_cfg(tmp_path), db) == (2000.0, f"synth:{s.id}")   # no knob in svkit.toml: synth
    assert _synth_err.id


def test_a_finished_run_keeps_its_period(tmp_path):
    cfg = _cfg(tmp_path, TOML_1250)
    db = RunDB.for_project(tmp_path)
    _sim(db, "fib", 1000, 500, cpi=2.0, cfg=cfg)                             # finishes under svkit.toml
    _sim(db, "sort", 300, 200, cfg=cfg)                                       # cpi derived from cycles / instrs
    nop = db.create_run("sim", "verilator", test="noperf", status="passed")
    db.update_run(nop.id, metrics={"tool": "verilator", "test": "noperf"})
    fix_period(cfg, db, nop.id)                                               # no perf: nothing stored
    assert "perf_clock" not in db.get_run(nop.id).metrics

    rows = perf_table(cfg, db)
    assert [p.test for p in rows] == ["fib", "sort"] and rows[1].cpi == 1.5
    assert (rows[0].period, rows[0].period_source, rows[0].tpi) == (1250.0, "svkit.toml", 2500.0)

    g = _sweep(db, 900.0)
    PF.set_period_choice(db, "sweep")                                         # the choice changes ...
    rows = perf_table(cfg, db)
    assert (rows[0].period, rows[0].period_source) == (1250.0, "svkit.toml")   # ... finished runs don't
    _sim(db, "fib", 1000, 500, cpi=2.0, cfg=cfg)                             # a new run gets the sweep's
    rows = perf_table(cfg, db)
    assert (rows[0].period, rows[0].period_source, rows[0].tpi) == (900.0, f"sweep:{g.id}", 1800.0)
    assert rows[1].period == 1250.0

    PF.set_period_choice(db, "manual", 1000)
    _sweep(db, 600.0)                                                         # a newer sweep moves nothing either
    _sim(db, "sort", 300, 200, cfg=cfg)
    rows = perf_table(cfg, db)
    assert [(p.period, p.period_source) for p in rows] == [(900.0, f"sweep:{g.id}"), (1000.0, "manual")]


def test_runs_recorded_before_fixing_get_the_old_rule_once(tmp_path):
    cfg = _cfg(tmp_path, TOML_1250)
    db = RunDB.for_project(tmp_path)
    old = _sim(db, "fib", 1000, 500, cpi=2.0)                                 # no fixed period stored
    g = _sweep(db, 800.0)
    rows = perf_table(cfg, db)                                                # pre-v0.12: the sweep, not svkit.toml
    assert (rows[0].period, rows[0].period_source, rows[0].tpi) == (800.0, f"sweep:{g.id}", 1600.0)
    assert db.get_run(old.id).metrics["perf_clock"] == {"period": 800.0, "source": f"sweep:{g.id}"}
    _sweep(db, 500.0)
    assert perf_table(cfg, db)[0].period == 800.0                             # now fixed


def test_latest_run_per_test_and_group_filter(tmp_path):
    cfg = _cfg(tmp_path)
    db = RunDB.for_project(tmp_path)
    g1 = db.create_group("sim-all")
    _sim(db, "fib", 1000, 500, group=g1.id, cfg=cfg)
    _sim(db, "fib", 900, 500, cfg=cfg)                # newer, outside the group
    assert perf_table(cfg, db)[0].cycles == 900
    assert perf_table(cfg, db, group_id=g1.id)[0].cycles == 1000
    assert perf_table(cfg, db, group_id="g_9999") == []
    assert all(p.period is None and p.tpi is None for p in perf_table(cfg, db))   # no period known


def test_runner_fixes_the_period_as_sim_runs_finish_and_when_reused(tmp_path):
    import time
    from svkit.core.runner import Runner
    cfg = _cfg(tmp_path, TOML_1250)
    runner = Runner(cfg)
    db = runner.db
    perf = {"perf": {"test": "fib", "cycles": 1000, "instrs": 500}}
    run = db.create_run("sim", "verilator", test="fib", cache_key="k")
    runner._finish(run, None, time.time(), "passed", {"passed": True, **perf}, 0, None)
    assert db.get_run(run.id).metrics["perf_clock"] == {"period": 1250.0, "source": "svkit.toml"}

    PF.set_period_choice(db, "manual", 800)
    hit = runner._cached("k", "sim", "verilator", test="fib")              # reuses the result: finishes now
    assert hit.cached_from == run.id and hit.metrics["perf_clock"] == {"period": 800.0, "source": "manual"}
    assert db.get_run(run.id).metrics["perf_clock"]["period"] == 1250.0    # the original keeps its own

    synth = db.create_run("synth", "yosys", test=None)                      # not a sim run: untouched
    runner._finish(synth, None, time.time(), "done", {"period": 2000.0}, 0, None)
    assert "perf_clock" not in db.get_run(synth.id).metrics
