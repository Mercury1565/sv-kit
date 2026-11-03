"""Gate netlist readers (Yosys JSON, structural Verilog, Liberty) and the cone / critical-path overlay."""
from pathlib import Path

import pytest

from svkit.analysis.cone import cone, map_path
from svkit.design import netlist as N
from svkit.schemas import TimingPath, TimingPoint

D = Path(__file__).parent / "data" / "design"


def test_yosys_json_and_verilog_agree():
    nj = N.load(D / "acc4.json")
    nv = N.load(D / "acc4.vg")
    assert nj.top == nv.top == "acc4"
    assert len(nj.cells) == len(nv.cells) > 10
    assert sorted(c.type for c in nj.cells.values()) == sorted(c.type for c in nv.cells.values())
    # MUX select is an input even though 'S' can be an adder sum output
    mux = next(c for c in nv.cells.values() if c.type == "$_MUX_")
    assert mux.dirs["S"] == "in" and mux.dirs["Y"] == "out"
    for nl in (nj, nv):
        assert nl.ports["a[0]"] == "in" and nl.ports["par"] == "out"
        assert all(n in nl.driver for n in ("acc[0]", "acc[3]", "par"))


def test_acc4_critical_path_maps_with_zero_unresolved():
    nl = N.load(D / "acc4.vg")
    tp = TimingPath.model_validate_json((D / "acc4_path.json").read_text())
    mapped, unresolved = map_path(nl, tp)
    assert unresolved == [] and len(mapped) == len(tp.points)
    out = cone(nl, None, depth=2, path=tp)
    assert out["unresolved"] == []
    on = {n["id"] for n in out["nodes"] if n["on_path"]}
    assert {p.inst for p in tp.points} <= on
    # every on-path cell with an output row carries its delay; chain edges are marked
    ids = {n["id"]: n for n in out["nodes"]}
    assert ids["_30_"]["delay"] == 20.0 and ids["acc_reg[3]"]["delay"] == 35.0
    pe = [(e["src"], e["dst"]) for e in out["edges"] if e["on_path"]]
    assert ("acc_reg[3]", "_30_") in pe and ("_34_", "par_reg") in pe
    # endpoint net is the root of the cone
    assert out["root_net"] == nl.pin_net("par_reg", "D")


def test_cone_depth_and_ports():
    nl = N.load(D / "acc4.json")
    c1 = cone(nl, "par", depth=1)
    c3 = cone(nl, "par", depth=3)
    assert len(c3["nodes"]) > len(c1["nodes"]) >= 1
    assert all(e["src"] in {n["id"] for n in c3["nodes"]} for e in c3["edges"])
    big = cone(nl, "acc[1]", depth=20)
    kinds = {n["kind"] for n in big["nodes"]}
    assert "port" in kinds    # reaches the primary inputs
    with pytest.raises(KeyError):
        cone(nl, "no_such_net", depth=2)


def test_liberty_reader():
    dirs = N.read_liberty_dirs([D / "dc_style.lib"])
    assert dirs["NAND2_X1_R"] == {"A1": "in", "A2": "in", "ZN": "out"}
    assert dirs["DFFR_X1_R"]["CK"] == "in" and dirs["DFFR_X1_R"]["QN"] == "out"
    assert dirs["MUX2_X1_L"]["S"] == "in"


def test_dc_style_hierarchical_netlist_and_path():
    nl = N.load(D / "dc_style.vg", liberty=[D / "dc_style.lib"])
    assert nl.top == "dc_top"
    assert {"data_reg[0]", "data_reg[1]", "u_sub/U3", "u_sub/U4", "U9", "out_reg"} == set(nl.cells)
    assert nl.cells["u_sub/U3"].pins["A1"] == ["data_q[0]"]          # port mapping through hierarchy
    assert nl.cells["u_sub/U4"].pins["I1"] == ["1'b1"]
    # DC-style path: escaped names, '/' hierarchy, DC cell names
    pts = [
        TimingPoint(inst="data_reg[1]", pin="CK", cell="DFFR_X1_R", incr=0, arrival=0),
        TimingPoint(inst="\\data_reg[1] ", pin="Q", cell="DFFR_X1_R", incr=80.5, arrival=80.5, net="data_q[1]"),
        TimingPoint(inst="u_sub/U3", pin="A2", cell="NAND2_X1_R", incr=1.0, arrival=81.5),
        TimingPoint(inst="u_sub/U3", pin="ZN", cell="NAND2_X1_R", incr=20.0, arrival=101.5, net="u_sub/n1"),
        TimingPoint(inst="u_sub.U4", pin="I0", cell="MUX2_X1_L", incr=0.5, arrival=102.0),
        TimingPoint(inst="u_sub/U4", pin="Z", cell="MUX2_X1_L", incr=30.0, arrival=132.0),
        TimingPoint(inst="U9", pin="I", cell="INV_X1_R", incr=0.0, arrival=132.0),
        TimingPoint(inst="U9", pin="ZN", cell="INV_X1_R", incr=10.0, arrival=142.0),
        TimingPoint(inst="out_reg", pin="D", cell="DFFR_X1_R", incr=0.2, arrival=142.2),
    ]
    tp = TimingPath(startpoint="data_reg[1]/CK", endpoint="out_reg/D", slack=-12.0, points=pts)
    out = cone(nl, None, depth=1, path=tp)
    assert out["unresolved"] == []
    ids = {n["id"]: n for n in out["nodes"]}
    assert ids["u_sub/U4"]["delay"] == 30.0 and ids["u_sub/U4"]["on_path"]
    assert ids["out_reg"]["on_path"] and not ids.get("data_reg[0]", {"on_path": False})["on_path"]
    assert [e for e in out["edges"] if e["on_path"] and e["src"] == "u_sub/U3" and e["dst"] == "u_sub/U4"]
    # an unresolvable point is reported, not dropped
    bad = TimingPath(startpoint="x", endpoint="y", slack=0,
                     points=pts[:2] + [TimingPoint(inst="U999", pin="A", incr=1, arrival=1)])
    assert [u["inst"] for u in cone(nl, "n6", 2, path=bad)["unresolved"]] == ["U999"]


def test_verilog_reader_odd_syntax(tmp_path):
    f = tmp_path / "odd.v"
    f.write_text("""
    /* block comment */ module odd(a, y); input [1:0] a; output y;
      wire w; (* keep *) wire [1:0] bus;
      assign bus = {a[0], a[1]}, w = bus[1];
      AND2 #(.P(1)) g1 (w, bus[0], y);   // ordered connections
      BUF b0 (.A(1'b0), .Y());
    endmodule
    """)
    nl = N.load(f)
    assert set(nl.cells) == {"g1", "b0"}
    assert nl.cells["g1"].pins["_0"] == ["a[0]"]        # w -> bus[1] -> a[0] through assigns
