from svkit.analysis.compare import compare
from svkit.core.rundb import RunDB


def _synth(db, exp, **m):
    r = db.create_run("synth", "yosys", experiment=exp, status="done")
    metrics = {"tool": "yosys", "top": "core", "area_unit": "um^2", **m}
    db.update_run(r.id, metrics=metrics)
    return db.get_run(r.id)


def _row(res, metric):
    return next(m for m in res.metrics if m.metric == metric)


def test_compare_deltas_and_verdicts(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    a = _synth(db, "baseline", wns=-20.0, tns=-100.0, area_total=1000.0, cell_count=50, period=1000.0,
               power={"total": 2.0, "dynamic": 1.5, "leakage": 0.5}, vt_mix={"RVT": 40, "LVT": 10},
               area_by_hier={"": 1000.0, "u_alu": 600.0, "u_alu/u_add": 200.0})
    b = _synth(db, "fast", wns=15.0, tns=0.0, area_total=1100.0, cell_count=50, period=1000.0,
               power={"total": 2.5, "dynamic": 2.0, "leakage": 0.5}, vt_mix={"RVT": 30, "LVT": 15, "HVT": 5},
               area_by_hier={"": 1100.0, "u_alu": 650.0})
    res = compare([a, b])
    assert res.run_ids == [a.id, b.id]
    assert res.labels == ["baseline", "fast"]

    wns = _row(res, "wns")
    assert wns.values == [-20.0, 15.0] and wns.deltas == [0.0, 35.0]
    assert wns.verdict == ["same", "better"] and wns.better == "higher" and wns.unit == "ps"
    assert abs(wns.pct[1] - 175.0) < 1e-9

    area = _row(res, "area_total")
    assert area.verdict[1] == "worse" and area.unit == "um^2" and abs(area.pct[1] - 10.0) < 1e-9
    assert _row(res, "cell_count").verdict == ["same", "same"]
    assert _row(res, "power.total").verdict[1] == "worse"
    assert _row(res, "power.leakage").verdict[1] == "same"
    assert _row(res, "tns").verdict[1] == "better"
    # vt mix: neutral, missing class in a run with a vt_mix counts as 0
    hvt = _row(res, "vt_mix.HVT")
    assert hvt.values == [0.0, 5.0] and hvt.better == "none" and hvt.verdict == ["same", "na"]
    assert _row(res, "area_by_hier.u_alu").deltas == [0.0, 50.0]
    names = [m.metric for m in res.metrics]
    assert "area_by_hier.u_alu/u_add" not in names and "area_by_hier." not in names
    assert "hold_wns" not in names            # no run reports it -> no row


def test_compare_missing_metrics_and_duplicate_labels(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    a = _synth(db, None, wns=0.0, area_total=10.0)
    b = _synth(db, None, wns=5.0, area_total=10.0000001)
    err = db.create_run("synth", "yosys", experiment=None, status="error")
    c1 = _synth(db, "x", wns=1.0)
    c2 = _synth(db, "x", wns=1.0)
    res = compare([a, b, err, c1, c2])
    assert res.labels[:3] == [a.id, b.id, err.id]
    assert res.labels[3:] == [f"x ({c1.id})", f"x ({c2.id})"]
    wns = _row(res, "wns")
    assert wns.values[2] is None and wns.verdict[2] == "na" and wns.deltas[2] is None
    assert wns.pct[1] is None                # baseline 0 -> no percentage
    assert _row(res, "area_total").verdict[1] == "same"      # within epsilon
    # baseline missing -> every column na
    res2 = compare([err, a])
    assert _row(res2, "wns").verdict == ["na", "na"]
    assert compare([]).metrics == []
