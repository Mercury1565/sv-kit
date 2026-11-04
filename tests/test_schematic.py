"""Schematic netlists for the Blocks / Gates diagrams (Yosys JSON drawn by netlistsvg in the browser)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from svkit.design import netlist as N
from svkit.design import schematic as S
from svkit.analysis import cone as CO

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "fixtures" / "lib" / "svkit_tiny.lib"
D = Path(__file__).parent / "data" / "design"

from test_design_routes import client  # noqa: E402,F401  (shared TestClient fixture)


# ---------------------------------------------------------------- cell -> symbol
def test_liberty_functions_pick_gate_symbols():
    lib = N.read_liberty_cells([LIB])
    want = {"INV_R": "$_NOT_", "BUF_R": "$_BUF_", "NAND2_R": "$_NAND_", "NOR2_R": "$_NOR_", "AND2_R": "$_AND_",
            "OR2_R": "$_OR_", "XOR2_R": "$_XOR_", "XNOR2_R": "$_XNOR_", "MUX2_R": "$_MUX_", "DFF_R": "$_DFF_P_",
            "DFFR_R": "$svkit_dffr", "DLATCH_R": "$_DLATCH_P_"}
    for cell, t in want.items():
        got = S.symbol(cell, lib[cell].dirs, lib[cell])
        assert got and got[0] == t, (cell, got)
    assert S.symbol("DFF_R", lib["DFF_R"].dirs, lib["DFF_R"])[1] == {"D": "D", "CLK": "C", "Q": "Q"}
    # complex gates and tie cells have no symbol: drawn as boxes with their pin names
    for cell in ("AOI21_R", "OAI21_R", "TIEHI_R"):
        assert S.symbol(cell, lib[cell].dirs, lib[cell]) is None


def test_liberty_ff_and_latch_groups():
    lib = N.read_liberty_cells([LIB])
    assert lib["DFFR_R"].ff["clocked_on"] == "CLK" and lib["DFFR_R"].ff["clear"] == "!RN"
    assert lib["DLATCH_R"].latch == {"vars": "IQ,IQN", "enable": "G", "data_in": "D"}
    assert N.read_liberty_dirs([LIB])["NAND2_R"] == {"A": "in", "B": "in", "Y": "out"}


@pytest.mark.parametrize("expr,ins,want", [
    ("!(A&B)", ["A", "B"], ("nand", ["A", "B"])),
    ("(A1 A2 A3)'", ["A1", "A2", "A3"], ("nand", ["A1", "A2", "A3"])),
    ("!(A+B+C+D)", list("ABCD"), ("nor", list("ABCD"))),
    ("(A^B)'", ["A", "B"], ("xnor", ["A", "B"])),
    ("(I0 S')+(I1 S)", ["I0", "I1", "S"], ("mux", ["I0", "I1", "S"])),
    ("(B&!S0)|(A&S0)", ["A", "B", "S0"], ("mux", ["B", "A", "S0"])),
    ("A1 B'", ["A1", "B"], None),                  # and-not: no symbol
    ("!((A1&A2)|B)", ["A1", "A2", "B"], None),     # AOI21
])
def test_classify_function(expr, ins, want):
    assert S.classify_function(expr, ins) == want


def test_name_convention_without_liberty():
    assert S.symbol("sky130_fd_sc_hd__nand3_1", {"A": "in", "B": "in", "C": "in", "Y": "out"})[0] == "$svkit_nand3"
    assert S.symbol("NAND2X1", {"IN1": "in", "IN2": "in", "QN": "out"}) == ("$_NAND_", {"IN1": "A", "IN2": "B", "QN": "Y"})
    assert S.symbol("MUX21X1", {"IN1": "in", "IN2": "in", "S": "in", "Q": "out"})[0] == "$_MUX_"
    assert S.symbol("DFFX1", {"CLK": "in", "D": "in", "Q": "out"})[0] == "$_DFF_P_"
    # two outputs (Q and QN), scan flops, unknown names: boxes
    assert S.symbol("DFFARX1", {"CLK": "in", "D": "in", "RSTB": "in", "Q": "out", "QN": "out"}) is None
    assert S.symbol("SDFFX1", {"CLK": "in", "D": "in", "SI": "in", "SE": "in", "Q": "out"}) is None
    assert S.symbol("FA_X1", {"A": "in", "B": "in", "CI": "in", "S": "out", "CO": "out"}) is None
    assert S.symbol("$_AND_", {"A": "in", "B": "in", "Y": "out"}) == ("$_AND_", {"A": "A", "B": "B", "Y": "Y"})


def test_box_width_follows_labels():
    assert S.box_type(["a"], ["y"], "m") == "$svkit_box_100"
    assert S.box_type(["a_very_long_input_name[31:0]"], ["result_out[63:0]"], "m") == "$svkit_box_380"
    assert S.box_type([], [], "x" * 25) == "$svkit_box_200"


# ---------------------------------------------------------------- netlists
def _check_yosys(nl):
    (name, mod), = nl["modules"].items()
    assert mod["attributes"]["top"] == 1
    keys = set(mod["cells"]) | set(mod["ports"])
    assert len(keys) == len(mod["cells"]) + len(mod["ports"])     # one id space in the drawing
    for c in mod["cells"].values():
        assert set(c["connections"]) == set(c["port_directions"])
        assert all(isinstance(b, int) or b in ("0", "1", "x", "z") for bits in c["connections"].values() for b in bits)
    return mod


def test_block_schematic_route(client):
    r = client.get("/api/design/schematic/block", params={"module": "xm_top"})
    assert r.status_code == 200
    b = r.json()
    mod = _check_yosys(b["netlist"])
    rf = mod["cells"]["u_rf"]
    assert rf["type"].startswith("$svkit_box_") and rf["attributes"]["type_label"] == "xm_regfile"
    assert b["cells"]["u_rf"] == {"kind": "instance", "name": "u_rf", "module": "xm_regfile"}
    # a net between two blocks is one shared bit list (netlistsvg draws a wire)
    outs = [tuple(v) for c in mod["cells"].values() for p, v in c["connections"].items() if c["port_directions"][p] == "output"]
    ins = [tuple(v) for c in mod["cells"].values() for p, v in c["connections"].items() if c["port_directions"][p] == "input"]
    ins += [tuple(p["bits"]) for p in mod["ports"].values() if p["direction"] == "output"]
    assert set(outs) & set(ins)
    assert any(v["kind"] == "port" for v in b["cells"].values())
    assert client.get("/api/design/schematic/block", params={"module": "nope"}).status_code == 404


def test_cone_schematic_route(client):
    rid = client.ids["synth"]
    r = client.get("/api/design/schematic/cone", params={"run": rid, "path": 0, "depth": 2}).json()
    mod = _check_yosys(r["netlist"])
    assert r["counts"]["on_path"] > 0 and r["path_nets"] and r["unresolved"] == []
    on = [k for k, v in r["cells"].items() if v.get("on_path") and v["kind"] == "cell"]
    assert on and all(k in mod["cells"] for k in on)
    assert any("delay" in mod["cells"][k]["attributes"] for k in on)
    # Yosys internal gates are drawn as symbols
    assert r["counts"]["symbols"] == r["counts"]["cells"]
    assert any(p["direction"] == "output" for p in mod["ports"].values())
    assert client.get("/api/design/schematic/cone", params={"run": rid}).status_code == 400


def test_cone_schematic_with_liberty_cells(tmp_path):
    """A netlist of library cells: symbols come from the Liberty functions, others are boxes."""
    v = tmp_path / "n.v"
    v.write_text("""
module t(input a, input b, input c, input clk, input rn, output q);
  wire n1, n2, n3;
  NAND2_R u1 (.A(a), .B(b), .Y(n1));
  AOI21_R u2 (.A1(n1), .A2(c), .B(a), .Y(n2));
  INV_R u3 (.A(n2), .Y(n3));
  DFFR_R r (.CLK(clk), .D(n3), .RN(rn), .Q(q));
endmodule
""")
    nl = N.load(v, top="t", liberty=[LIB])
    c = CO.cone(nl, "q", depth=5)
    r = S.cone_netlist(c, nl)
    mod = _check_yosys(r["netlist"])
    types = {k: mod["cells"][k]["type"] for k in ("u1", "u2", "u3", "r")}
    assert types["u1"] == "$_NAND_" and types["u3"] == "$_NOT_" and types["r"] == "$svkit_dffr"
    assert types["u2"].startswith("$svkit_box_") and set(mod["cells"]["u2"]["connections"]) == {"A1", "A2", "B", "Y"}
    assert mod["cells"]["u2"]["attributes"]["type_label"] == "AOI21_R"
    # the wire u1.Y -> u2.A1 is one bit shared by both ends
    assert mod["cells"]["u1"]["connections"]["Y"] == mod["cells"]["u2"]["connections"]["A1"]
    assert r["cells"]["r"]["type"] == "DFFR_R" and r["counts"] == {"cells": 4, "symbols": 3, "on_path": 0}


# ---------------------------------------------------------------- the real renderer
def _node():
    for n in (shutil.which("node"), os.path.expanduser("~/tools/node/bin/node")):
        if n and os.access(n, os.X_OK):
            return n
    return None


NETLISTSVG = REPO / "web" / "node_modules" / "netlistsvg"


@pytest.mark.skipif(not _node() or not NETLISTSVG.is_dir(), reason="needs Node and web/node_modules (npm ci)")
def test_netlistsvg_draws_both_with_the_skin(client, tmp_path):
    skin = REPO / "web" / "src" / "lib" / "schematic-skin.svg"
    script = tmp_path / "draw.cjs"
    script.write_text(f"""
const n = require({json.dumps(str(NETLISTSVG))});
const fs = require("fs");
const skin = fs.readFileSync({json.dumps(str(skin))}, "utf8");
const nl = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
n.render(skin, nl).then((s) => process.stdout.write(s)).catch((e) => {{ console.error(e); process.exit(1); }});
""")
    rid = client.ids["synth"]
    for name, url, params in (("block", "/api/design/schematic/block", {"module": "xm_top"}),
                              ("cone", "/api/design/schematic/cone", {"run": rid, "path": 0, "depth": 3})):
        data = client.get(url, params=params).json()
        f = tmp_path / f"{name}.json"
        f.write_text(json.dumps(data["netlist"]))
        out = subprocess.run([_node(), str(script), str(f)], capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        svg = out.stdout
        assert svg.startswith("<svg") and "<line" in svg
        for key in data["netlist"]["modules"][next(iter(data["netlist"]["modules"]))]["cells"]:
            assert f'id="cell_{key}"' in svg, key
        for cls in data["path_nets"]:
            assert f'class="{cls}"' in svg, cls
