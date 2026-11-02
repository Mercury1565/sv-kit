"""Waveform backend (pywellen) on tiny committed VCD/FST files (tests/data/analysis/tiny_tb.v)."""
import os
import shutil
from pathlib import Path

import pytest

from svkit.adapters.waves import wellen as W

DATA = Path(__file__).parent / "data" / "analysis"
FILES = [DATA / "tiny.vcd", DATA / "tiny.fst"]


@pytest.fixture(params=FILES, ids=["vcd", "fst"])
def wf(request):
    return W.open_wave(request.param)


def test_meta(wf):
    m = wf.meta()
    assert m["format"] in ("vcd", "fst") and m["file"].startswith("tiny.")
    assert m["timescale"] == {"factor": 1, "unit": "ps"}
    assert (m["t_start"], m["t_end"]) == (0, 222000)


def test_scope_and_search(wf):
    top = wf.scope("")
    assert [s["path"] for s in top["scopes"]] == ["tiny_tb"]
    tb = wf.scope("tiny_tb")
    assert [s["name"] for s in tb["scopes"]] == ["dut"]
    v = {x["name"]: x for x in wf.scope("tiny_tb.dut")["vars"]}
    assert v["count"]["width"] == 4 and v["count"]["path"] == "tiny_tb.dut.count"
    assert v["late"]["kind"] == "reg"
    with pytest.raises(W.UnknownSignal):
        wf.scope("nope")
    hits = [h["path"] for h in wf.search("COUNT")]
    assert hits == ["tiny_tb.count", "tiny_tb.dut.count"]
    assert [h["path"] for h in wf.search("tiny_tb.dut.?ate")] == ["tiny_tb.dut.late"]
    assert [h["path"] for h in wf.search("*dut*", limit=2)] == ["tiny_tb.dut.clock", "tiny_tb.dut.reset"]
    assert wf.search("") == []


def test_data_values_and_init(wf):
    d = wf.data(["tiny_tb.dut.late", "tiny_tb.dut.count", "tiny_tb.reset"], 0, 60000)["signals"]
    late = d["tiny_tb.dut.late"]
    assert late == {"width": 8, "init": "xxxxxxxx", "changes": [], "truncated": False}
    cnt = d["tiny_tb.dut.count"]
    assert cnt["init"] == "xxxx"
    assert cnt["changes"][:3] == [[5000, "0000"], [25000, "0001"], [35000, "0010"]]
    assert d["tiny_tb.reset"]["changes"] == [[22000, "0"]]
    # window starting mid-run: init is the value at t0, changes are strictly after t0
    d2 = wf.data(["tiny_tb.dut.late"], 100000, 222000)["signals"]["tiny_tb.dut.late"]
    assert d2["init"] == "xxxxxxxx" and d2["changes"] == [[115000, "01011010"]]
    d3 = wf.data(["tiny_tb.dut.late"], 115000, 120000)["signals"]["tiny_tb.dut.late"]
    assert d3["init"] == "01011010" and d3["changes"] == []
    assert wf.value_at("tiny_tb.dut.mode", 60000) == "01"
    with pytest.raises(W.UnknownSignal):
        wf.data(["tiny_tb.nope"], 0, 10)


def test_real_values():
    wf = W.open_wave(DATA / "tiny.fst")
    r = wf.data(["tiny_tb.ratio"], 0, 30000)["signals"]["tiny_tb.ratio"]
    assert r["width"] is None and r["init"] == "0.0"
    assert r["changes"] == [[5000, "0.25"], [15000, "0.5"], [25000, "0.75"]]


def test_downsampling(wf):
    full = wf.data(["tiny_tb.clock"], 0, 222000, 100000)["signals"]["tiny_tb.clock"]
    assert not full["truncated"] and len(full["changes"]) == 44
    d = wf.data(["tiny_tb.clock", "tiny_tb.reset"], 0, 222000, 10)["signals"]
    clk = d["tiny_tb.clock"]
    assert clk["truncated"] and clk["changes"][0] == [5000, W.DENSE]
    assert not d["tiny_tb.reset"]["truncated"]     # sparse signals are untouched


def _check_spacing(changes, t0, t1, mc):
    import math
    w = math.ceil((t1 - t0) / mc)
    ts = [c[0] for c in changes]
    assert all(b - a >= w for a, b in zip(ts, ts[1:])), ts
    assert len(changes) <= 2 * mc
    assert all(t0 < t <= t1 for t in ts)


def test_thin_properties(tmp_path):
    # a signal with bursts: dense regions, isolated changes, and a settle value after each burst
    lines = ["$timescale 1ns $end", "$scope module m $end", "$var wire 4 ! s [3:0] $end",
             "$upscope $end", "$enddefinitions $end", "#0", "b0 !"]
    v, t = 0, 0
    for burst in range(5):
        t += 1000
        lines += [f"#{t}", f"b{(v := (v + 1) % 16):b} !"]          # isolated change
        t += 1000
        for k in range(200):                                       # burst: a change every tick
            t += 1
            lines += [f"#{t}", f"b{(v := (v + 1) % 16):b} !"]
    lines.append(f"#{t + 1000}")
    p = tmp_path / "burst.vcd"
    p.write_text("\n".join(lines) + "\n")
    wf = W.open_wave(p)
    end = wf.meta()["t_end"]
    for mc in (5, 20, 50, 100):
        d = wf.data(["m.s"], 0, end, mc)["signals"]["m.s"]
        assert d["truncated"]
        _check_spacing(d["changes"], 0, end, mc)
        vals = [c[1] for c in d["changes"]]
        assert W.DENSE in vals
        # the entry after a dense block is the settled value at the block end
        for i, (tt, val) in enumerate(d["changes"]):
            if val == W.DENSE and i + 1 < len(d["changes"]):
                nt, nv = d["changes"][i + 1]
                if nv != W.DENSE:
                    assert nv == wf.value_at("m.s", nt)
        # isolated changes survive when they are far from bursts
        if mc >= 50:
            assert [1000, "0001"] in d["changes"]
    # a narrow window around one burst with a big budget is exact
    d = wf.data(["m.s"], 1500, 2500, 1000)["signals"]["m.s"]
    assert not d["truncated"] and len(d["changes"]) == 200


def test_next(wf):
    sig = "tiny_tb.dut.count"
    assert wf.next(sig, 5000) == 25000
    assert wf.next(sig, 35000, direction=-1) == 25000
    assert wf.next(sig, 5000, direction=-1) == 0
    assert wf.next(sig, 0, direction=-1) is None
    assert wf.next("tiny_tb.dut.late", 0, "x") is None                 # no change INTO x after 0
    assert wf.next("tiny_tb.dut.late", 200000, "x", -1) == 0
    assert wf.next("tiny_tb.dut.late", 0, "change") == 115000
    assert wf.next("tiny_tb.clock", 222000) is None


def test_lru_cache_reuses_and_invalidates(tmp_path):
    p = tmp_path / "w.vcd"
    shutil.copy(FILES[0], p)
    a = W.open_wave(p)
    assert W.open_wave(p) is a
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    b = W.open_wave(p)
    assert b is not a
    with pytest.raises(W.WaveError):
        W.open_wave(tmp_path / "missing.vcd")
    bad = tmp_path / "bad.vcd"
    bad.write_text("this is not a waveform")
    with pytest.raises(W.WaveError):
        W.open_wave(bad).meta()


def test_format_values():
    assert W._fmt(5, 4, False) == "0101"
    assert W._fmt(0, 1, False) == "0"
    assert W._fmt("1x", 4, False) == "001x"
    assert W._fmt("x1", 4, False) == "xxx1"
    assert W._fmt("Z", 1, False) == "z"
    assert W._fmt(1.5, None, True) == "1.5"
    assert W._fmt(None, 4, False) is None
    assert W._fmt(2**70, 72, False) == format(2**70, "072b")
