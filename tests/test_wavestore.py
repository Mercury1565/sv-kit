"""[waves].archive: runs keep FST (default) or VCD, and `svkit waves archive` converts old runs."""
import shutil
from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters.waves.wellen import open_wave
from svkit.core import wavestore as W
from svkit.core.rundb import RunDB
from svkit.schemas import SimResult

TINY = Path(__file__).parent / "data" / "analysis" / "tiny.vcd"
needs_conv = pytest.mark.skipif(not (shutil.which("vcd2fst") or __import__("svkit.compat", fromlist=["x"]).locate("vcd2fst")),
                                reason="needs vcd2fst/fst2vcd (GTKWave / OSS CAD Suite)")


def _cfg(tmp_path, extra=""):
    (tmp_path / "svkit.toml").write_text('[project]\nname = "w"\ntop = "t"\n' + extra)
    return C.load(tmp_path)


def test_default_is_fst(tmp_path):
    assert _cfg(tmp_path).waves.archive == "fst"
    assert _cfg(tmp_path, '[waves]\narchive = "vcd"\n').waves.archive == "vcd"


@needs_conv
def test_archive_run_converts_losslessly(tmp_path):
    cfg = _cfg(tmp_path)
    rd = tmp_path / "run"
    rd.mkdir()
    shutil.copy(TINY, rd / "waves.vcd")
    want = open_wave(rd / "waves.vcd").time_range()
    res, arts = SimResult(tool="x", test="t", waves="waves.vcd"), {"waves.vcd": "waves.vcd", "sim.log": "sim.log"}
    assert W.archive_run(cfg, rd, res, arts) is None
    assert res.waves == "waves.fst" and arts == {"waves.fst": "waves.fst", "sim.log": "sim.log"}
    assert not (rd / "waves.vcd").exists() and open_wave(rd / "waves.fst").time_range() == want


def test_archive_vcd_setting_keeps_vcd(tmp_path):
    cfg = _cfg(tmp_path, '[waves]\narchive = "vcd"\n')
    rd = tmp_path / "run"
    rd.mkdir()
    shutil.copy(TINY, rd / "waves.vcd")
    res, arts = SimResult(tool="x", test="t", waves="waves.vcd"), {"waves.vcd": "waves.vcd"}
    assert W.archive_run(cfg, rd, res, arts) is None and res.waves == "waves.vcd" and (rd / "waves.vcd").is_file()


def test_missing_converter_keeps_the_vcd(tmp_path):
    cfg = _cfg(tmp_path, '[tools]\nvcd2fst = "svkit-no-such-vcd2fst"\n')
    rd = tmp_path / "run"
    rd.mkdir()
    shutil.copy(TINY, rd / "waves.vcd")
    res, arts = SimResult(tool="x", test="t", waves="waves.vcd"), {"waves.vcd": "waves.vcd"}
    warn = W.archive_run(cfg, rd, res, arts)
    assert warn and "vcd2fst not found" in warn
    assert res.waves == "waves.vcd" and arts == {"waves.vcd": "waves.vcd"} and (rd / "waves.vcd").is_file()


def test_bad_converter_output_is_rejected(tmp_path):
    fake = tmp_path / "vcd2fst"
    fake.write_text("#!/bin/sh\necho garbage > \"$2\"\n")
    fake.chmod(0o755)
    cfg = _cfg(tmp_path, f'[tools]\nvcd2fst = "{fake}"\n')
    ok, msg = W.convert(cfg, TINY, tmp_path / "out.fst")
    assert not ok and "did not check out" in msg and not (tmp_path / "out.fst").exists()


@needs_conv
def test_rearchive_both_ways_including_cache_hits(tmp_path):
    cfg = _cfg(tmp_path)
    db = RunDB(tmp_path / ".svkit")
    orig = db.create_run("sim", "verilator", test="t")
    shutil.copy(TINY, db.run_dir(orig.id) / "waves.vcd")
    db.update_run(orig.id, artifacts={"waves.vcd": "waves.vcd", "sim.log": "sim.log"},
                  metrics={"waves": "waves.vcd", "test": "t"})
    hit = db.create_run("sim", "verilator", test="t")
    db.update_run(hit.id, cached_from=orig.id, artifacts={"waves.vcd": f"../{orig.id}/waves.vcd"},
                  metrics={"waves": "waves.vcd", "test": "t"})
    want = open_wave(db.run_dir(orig.id) / "waves.vcd").time_range()

    assert W.rearchive(cfg, db, "fst", dry_run=True).converted == [orig.id]
    assert (db.run_dir(orig.id) / "waves.vcd").is_file()            # dry run changes nothing

    res = W.rearchive(cfg, db, "fst")
    assert res.converted == [orig.id] and not res.failed and res.after > 0
    o, h = db.get_run(orig.id), db.get_run(hit.id)
    assert o.metrics["waves"] == "waves.fst" and o.artifacts == {"waves.fst": "waves.fst", "sim.log": "sim.log"}
    assert h.metrics["waves"] == "waves.fst" and h.artifacts == {"waves.fst": f"../{orig.id}/waves.fst"}

    W.rearchive(cfg, db, "vcd")                                      # and back again
    o, h = db.get_run(orig.id), db.get_run(hit.id)
    assert o.metrics["waves"] == "waves.vcd" and h.artifacts == {"waves.vcd": f"../{orig.id}/waves.vcd"}
    assert open_wave(db.run_dir(orig.id) / "waves.vcd").time_range() == want
    assert not (db.run_dir(orig.id) / "waves.fst").exists()
