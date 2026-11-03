"""X tracer units: 4-state evaluator, X dependencies, tracer semantics on in-memory waves, trace_run."""
import shutil
from pathlib import Path

import pytest

from svkit import config as C
from svkit.analysis import xtrace as X
from svkit.core.rundb import RunDB
from svkit.design import expr as E
from svkit.design import graph as G
from svkit.design.wavesrc import DictSource, PyWellenSource, fmt_value
from svkit.schemas import XCause

FIX = Path(__file__).resolve().parents[1] / "fixtures"
GOLD = FIX / "golden" / "xtrace"


def ev(vals):
    return E.Evaluator(lambda p: vals.get(p), lambda m, i: vals.get(f"{m}[{i}]"))


def s(n, w):
    return ("s", n, w)


def test_evaluator_four_state():
    v = {"a": "0101", "b": "x011", "c": "1", "z": "0", "x": "x"}
    e = ev(v)
    assert e.eval(("b", "BinaryAnd", s("a", 4), s("b", 4), 4)) == "0001"
    assert e.eval(("b", "BinaryOr", s("a", 4), s("b", 4), 4)) == "x111"
    assert e.eval(("b", "Add", s("a", 4), s("b", 4), 4)) == "xxxx"
    assert e.eval(("b", "Add", s("a", 4), ("c", "0011", 4), 4)) == "1000"
    assert e.eval(("b", "LogicalAnd", s("z", 1), s("x", 1), 1)) == "0"     # short-circuit beats X
    assert e.eval(("b", "Equality", s("a", 4), s("b", 4), 1)) == "0"       # a known bit differs
    assert e.eval(("?", s("c", 1), s("a", 4), s("b", 4), 4)) == "0101"
    assert e.eval(("?", s("x", 1), s("a", 4), ("c", "0111", 4), 4)) == "01x1"
    assert e.eval(("rng", s("b", 4), 0, 3)) == "011"
    assert e.eval(("u", "BitwiseOr", s("b", 4), 1)) == "1"
    assert e.eval(s("missing", 4)) is None
    assert e.eval(("m", "mem", ("c", "110", 3), 8, 0, 5)) == "x" * 8         # out of range
    assert e.eval(("ps", s("a", 4), ("c", "10", 2), 2, 0, 0, False, 1)) == "01"   # a[3:2]


def test_x_deps_follow_taken_branch():
    v = {"sel": "1", "a": "xxxx", "b": "xx00", "i": "011", "j": "x11", "big": "111"}
    e = ev(v)
    assert E.x_deps(("?", s("sel", 1), s("a", 4), s("b", 4), 4), e) == [("sig", "a")]
    assert E.x_deps(("rng", s("b", 4), 0, 2), e) == []                       # selected bits are clean
    assert E.x_deps(("m", "mem", s("i", 3), 8, 0, 5), e) == [("mem", "mem", 3)]
    assert E.x_deps(("m", "mem", s("j", 3), 8, 0, 5), e) == [("sig", "j")]
    assert E.x_deps(("m", "mem", s("big", 3), 8, 0, 5), e) == [("oor", "mem", 7, 0, 5)]


def test_fmt_value():
    assert fmt_value(5, 4) == "0101" and fmt_value("xx", 4) == "xxxx" and fmt_value("01", 4) == "0001"
    assert fmt_value(None, 2) == "xx"


@pytest.fixture(scope="module")
def mem_graph():
    return G.build(C.load(FIX / "xbug_mem"), use_cache=False)


def test_memory_word_dumped_in_waves(mem_graph):
    """When the simulator dumps memory words, the word's own value and write history are used."""
    w = PyWellenSource(GOLD / "xbug_mem" / "waves.vcd")
    data = {n: w.changes(n, 0, 10**9) for n in w.vars()}
    data["tb_xm.dut.u_rf.regs[5]"] = [(0, "xxxxxxxx")]
    n = X.trace(mem_graph, DictSource(data), "tb_xm.rd_out", 75001)
    (root,) = X.leaves(n)
    assert root.cause == XCause.uninit_memory and root.signal == "tb_xm.dut.u_rf.regs[5]"
    assert root.driver.file == "rtl/xm_regfile.sv"


def test_memory_written_with_x(mem_graph):
    """A word written from X write data is traced through the write port, not called uninitialized."""
    w = PyWellenSource(GOLD / "xbug_mem" / "waves.vcd")
    data = {n: w.changes(n, 0, 10**9) for n in w.vars()}
    for n in ("tb_xm.wdata", "tb_xm.dut.wdata", "tb_xm.dut.u_rf.wdata"):
        data[n] = [(0, "00000000"), (44000, "xxxxxxxx"), (46000, "00000000")]   # X at the write of regs[2]
    for n in ("tb_xm.raddr", "tb_xm.dut.raddr", "tb_xm.dut.u_rf.raddr"):
        data[n] = [(0, "000"), (50000, "010")]
    data["tb_xm.dut.u_rf.rdata"] = [(0, "00000000"), (50000, "xxxxxxxx"), (60000, "00000000")]
    node = X.trace(mem_graph, DictSource(data), "tb_xm.dut.u_rf.rdata", 56000)
    roots = X.leaves(node)
    assert [(r.signal, r.cause) for r in roots] == [("tb_xm.dut.wdata", XCause.external_input)]


def test_not_x_and_unknown_signal_and_depth(mem_graph):
    w = PyWellenSource(GOLD / "xbug_mem" / "waves.vcd")
    n = X.trace(mem_graph, w, "tb_xm.rd_out", 45000)
    assert n.cause is None and "no X" in n.note and not n.children
    n = X.trace(mem_graph, w, "tb_xm.nope", 45000)
    assert n.cause == XCause.unknown and "not found" in n.note
    n = X.trace(mem_graph, w, "tb_xm.rd_out", 75001, depth=2)
    assert any(lf.cause == XCause.unknown and "depth" in lf.note for lf in X.leaves(n))


def test_print_tree(mem_graph):
    w = PyWellenSource(GOLD / "xbug_mem" / "waves.vcd")
    lines = []
    X.print_tree(X.trace(mem_graph, w, "tb_xm.lut_out", 85001), lines.append)
    text = "\n".join(lines)
    assert "ROOT CAUSE [index_out_of_range]" in text and "rtl/xm_top.sv:35" in text
    assert text.splitlines()[0].startswith("tb_xm.lut_out @85001 = 8'bxxxxxxxx")


def test_trace_run_uses_latest_sim_run_with_waves(tmp_path):
    proj = tmp_path / "xbug_input"
    shutil.copytree(FIX / "xbug_input", proj)
    cfg = C.load(proj)
    db = RunDB.for_project(proj)
    with pytest.raises(LookupError):
        X.trace_run(cfg, db, signal="tb_xi.result", time=35001)
    r = db.create_run("sim", "vcs", test="smoke", status="failed")
    shutil.copy(GOLD / "xbug_input" / "waves.vcd", db.runs_dir / r.id / "waves.vcd")
    db.update_run(r.id, artifacts={"waves.vcd": "waves.vcd"}, metrics={"tool": "vcs", "test": "smoke",
                                                                        "waves": "waves.vcd"})
    db.create_run("sim", "verilator", test="other", status="error")         # newer, no waves: skipped
    node = X.trace_run(cfg, db, signal="tb_xi.result", time=35001, depth=12)
    assert [(lf.signal, lf.cause) for lf in X.leaves(node)] == [("tb_xi.dut.cfg", XCause.external_input)]
    with pytest.raises(LookupError):
        X.trace_run(cfg, db, run_id="r_0002", signal="tb_xi.result", time=35001)
