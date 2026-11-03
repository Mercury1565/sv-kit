"""DesignGraph: hierarchy, types (lookup_type), drivers, block diagram, caching, path alignment."""
import shutil
from pathlib import Path

import pytest

from svkit import config as C
from svkit.design import graph as G

D = Path(__file__).parent / "data" / "design"


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "tb").mkdir()
    shutil.copy(D / "demo.sv", tmp_path / "rtl" / "demo.sv")
    shutil.copy(D / "demo_tb.sv", tmp_path / "tb" / "demo_tb.sv")
    (tmp_path / "svkit.toml").write_text(
        '[project]\nname="demo"\ntop="demo_top"\ntb_top="demo_tb"\n'
        'sources={glob=["rtl/*.sv"]}\ntb_sources={glob=["tb/*.sv"]}\nreset={name="rst_n", active="low"}\n')
    return C.load(tmp_path)


@pytest.fixture
def g(proj):
    return G.build(proj, use_cache=False)


def test_hierarchy(g):
    assert g.roots == ["demo_tb"] and g.design_tops == ["demo_tb.dut"]
    h = g.hierarchy()
    assert h["top"] == "demo_top"
    top = h["modules"]["demo_top"]
    assert top["file"] == "rtl/demo.sv" and top["line"] > 0
    assert {p["name"]: p["dir"] for p in top["ports"]}["dout"] == "out"
    assert {p["name"]: p["width"] for p in top["ports"]}["din"] == 8
    assert {"name": "u_leaf", "module": "demo_leaf"} in top["instances"]
    assert {"u_arr[0]", "u_arr[1]"} <= {i["name"] for i in top["instances"]}
    tree = h["tree"][0]
    assert tree["path"] == "demo_tb.dut" and "demo_tb.dut.u_leaf" in [c["path"] for c in tree["children"]]


def test_types_and_lookup(proj, g):
    t = G.lookup_type(proj, "demo_tb.dut.state")
    assert t == {"kind": "enum", "enum": {"0": "S_IDLE", "1": "S_RUN", "2": "S_DONE"}}
    s = G.lookup_type(proj, "TOP.demo_tb.dut.info")          # wave root differs: aligned by suffix
    assert s["kind"] == "struct"
    f = {x["name"]: x for x in s["fields"]}
    assert (f["tag"]["msb"], f["tag"]["lsb"]) == (7, 4) and f["st"]["enum"]["1"] == "S_RUN"
    o = {x["name"]: x for x in G.lookup_type(proj, "demo_tb.dut.outer")["fields"]}
    assert (o["inner.tag"]["msb"], o["inner.tag"]["lsb"]) == (8, 5) and (o["v"]["msb"], o["v"]["lsb"]) == (0, 0)
    assert G.lookup_type(proj, "demo_tb.dut.a") == {"kind": "plain"}
    assert G.lookup_type(proj, "nope.nothing_here") is None


def test_drivers(g):
    p = "demo_tb.dut."
    (a,) = g.drivers[p + "a"]
    assert a.kind == "assign" and a.data == [p + "din"] and a.file == "rtl/demo.sv"
    (sv,) = g.drivers[p + "sel_v"]
    assert set(sv.data) == {p + "a", p + "din"} and sv.ctrl == [p + "we"]
    (b,) = g.drivers[p + "b"]
    assert b.kind == "always_comb" and len(b.assigns) == 3
    assert p + "mem" in b.data and b.index == [p + "idx"] and {p + "state", p + "info"} <= set(b.ctrl)
    (st,) = g.drivers[p + "state"]
    assert st.kind == "always_ff" and st.clock == p + "clk" and st.edge == "posedge"
    assert st.has_reset_branch and st.reset == p + "rst_n" and st.reset_active == "low"
    (q,) = g.drivers[p + "u_leaf.q"]
    assert q.reset == p + "u_leaf.rst_n" and q.reset_active == "low" and q.has_reset_branch
    assert [a.in_reset for a in q.assigns] == [True, False]
    (mem,) = g.drivers[p + "mem"]
    assert mem.assigns[0].index is not None and mem.index == [p + "idx"] and mem.ctrl == [p + "we"]
    assert g.signals[p + "mem"].mem == (0, 5) and g.signals[p + "mem"].elem_width == 8
    # port connections link both directions across the hierarchy
    (d,) = g.drivers[p + "u_leaf.d"]
    assert d.kind == "port" and d.data == [p + "b"]
    (dout,) = g.drivers[p + "dout"]
    assert dout.kind == "port" and dout.data == [p + "u_leaf.q"]
    assert g.top_input(p + "din") and not g.top_input(p + "u_leaf.d")
    assert g.drivers[p + "g_bit[1].w"][0].kind == "assign"
    assert p + "u_arr[0].q" in g.drivers
    assert g.drivers[p + "din"][0].data == ["demo_tb.din"]
    assert g.drivers[p + "din"][0].to_dict()["kind"] == "port"


def test_block(g):
    blk = g.block("demo_top")
    ids = {n["id"] for n in blk["nodes"]}
    assert {"port:din", "port:dout", "u_leaf", "u_arr[0]", "logic"} <= ids
    e = [(x["src"], x["dst"], x["net"]) for x in blk["edges"]]
    assert ("logic", "u_leaf", "b") in e and ("u_leaf", "port:dout", "dout") in e and ("port:clk", "u_leaf", "clk") in e
    assert all(x["width"] for x in blk["edges"] if x["net"] == "b")
    leaf = g.block("demo_leaf")
    assert {"port:d", "port:q", "logic"} <= {n["id"] for n in leaf["nodes"]}
    with pytest.raises(KeyError):
        g.block("no_such_module")


def test_cache(proj):
    g1 = G.build(proj)
    assert G.build(proj) is g1
    G._MEM_CACHE.clear()
    g2 = G.build(proj)                      # from the pickle on disk
    assert g2 is not g1 and g2.signals.keys() == g1.signals.keys()
    assert list((proj.root / ".svkit" / "cache").glob("designgraph-*.pkl"))
    (proj.root / "rtl" / "demo.sv").write_text((D / "demo.sv").read_text() + "\nmodule extra_mod; endmodule\n")
    assert G.build(proj).source_hash != g1.source_hash


def test_robust_to_bad_code(tmp_path):
    f = tmp_path / "bad.sv"
    f.write_text("module bad(input logic a, output logic y);\n  assign y = a & undeclared_thing;\n"
                 "  missing_mod u_m (.x(a));\n  always_comb begin y = a; end\nendmodule\n")
    g = G.build_from_files([f], top="bad")
    assert "bad.y" in g.drivers and g.design_tops == ["bad"]


def test_path_aligner():
    al = G.PathAligner(["TOP.tb.dut.a", "TOP.tb.dut.u_x.a", "TOP.tb.clk", "other.clk"])
    assert al.map("tb.dut.u_x.a") == "TOP.tb.dut.u_x.a"
    assert al.map("tb.dut.a") == "TOP.tb.dut.a"
    assert al.map("tb.clk") == "TOP.tb.clk"
    assert al.map("x.y.zzz") is None
    assert G.split_path("a.\\b.c .g[1].x") == ["a", "\\b.c ", "g[1]", "x"]


def test_readmem_counts_as_initialization(tmp_path):
    f = tmp_path / "rom.sv"
    f.write_text("module rom(input logic [1:0] a, output logic [7:0] d);\n  logic [7:0] m [0:3];\n"
                 "  initial $readmemh(\"rom.hex\", m);\n  assign d = m[a];\nendmodule\n")
    g = G.build_from_files([f], top="rom")
    assert [d.kind for d in g.drivers["rom.m"]] == ["initial"]
