"""Downloading a synthesis run's reports as one text file."""
from fastapi.testclient import TestClient

from svkit import config as C
from svkit.analysis.report_export import report_names
from svkit.core.rundb import RunDB
from svkit.server.app import create_app

METRICS = {"tool": "dc", "top": "core", "period": 4600.0, "wns": 0.1, "tns": 0.0, "hold_wns": 2.0,
           "area_total": 2700.25, "area_unit": "um^2", "cell_count": 13466,
           "power": {"total": 0.948, "dynamic": 0.9, "leakage": 0.048},
           "paths": [{"startpoint": "a_reg", "endpoint": "b_reg", "slack": 0.1, "path_type": "max"}]}


def test_report_names_skip_split_copies():
    arts = {n: n for n in ("reports.rep", "reports.timing.rpt", "reports.area.rpt", "power.rpt", "stat.txt",
                           "sta.log", "synth.log", "netlist.v", "stat.json", "synth_script.tcl")}
    assert report_names(arts) == ["reports.rep", "power.rpt", "stat.txt", "sta.log"]
    # split sections are kept when their combined file is not there
    assert report_names({"x.timing.rpt": "x.timing.rpt"}) == ["x.timing.rpt"]


def test_reports_download(tmp_path):
    (tmp_path / "svkit.toml").write_text('[project]\nname="demo"\ntop="core"\n')
    db = RunDB.for_project(tmp_path)
    r = db.create_run("synth", "dc", status="done", name="before / retiming", knobs={"clock_period": 4600})
    d = db.run_dir(r.id)
    (d / "area.rpt").write_text("AREA REPORT BODY\n")
    (d / "timing.rpt").write_text("TIMING REPORT BODY")
    (d / "netlist.v").write_text("module core; endmodule\n")
    db.update_run(r.id, metrics=METRICS, artifacts={"area.rpt": "area.rpt", "timing.rpt": "timing.rpt",
                                                   "netlist.v": "netlist.v", "gone.rpt": "gone.rpt"})
    hit = db.create_run("synth", "dc", status="done")      # a reused result points at the original's files
    db.update_run(hit.id, metrics=METRICS, cached_from=r.id, artifacts={"area.rpt": f"../{r.id}/area.rpt",
                                                                        "evil.rpt": "../../../svkit.toml"})
    sim = db.create_run("sim", "verilator", test="t", status="passed")
    c = TestClient(create_app(C.load(tmp_path)))

    res = c.get(f"/api/runs/{r.id}/reports.txt")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    assert f'filename="{r.id}-before-retiming-synth-reports.txt"' in res.headers["content-disposition"]
    t = res.text
    assert '"before / retiming"' in t and "4,600 ps" in t and "13,466" in t and "0.948 mW" in t
    assert "a_reg -> b_reg" in t and "clock_period=4600" in t
    assert t.index("AREA REPORT BODY") < t.index("TIMING REPORT BODY") and "module core" not in t
    assert "gone.rpt" in t and "missing" in t

    t2 = c.get(f"/api/runs/{hit.id}/reports.txt").text
    assert "AREA REPORT BODY" in t2 and f"reused from {r.id}" in t2
    assert 'name="demo"' not in t2                          # never reads outside the run directories
    assert c.get(f"/api/runs/{sim.id}/reports.txt").status_code == 400
    assert c.get("/api/runs/r_9999/reports.txt").status_code == 404

    empty = db.create_run("synth", "yosys", status="error")
    db.update_run(empty.id, message="yosys not found")
    t3 = c.get(f"/api/runs/{empty.id}/reports.txt").text
    assert "yosys not found" in t3 and "kept no text reports" in t3
