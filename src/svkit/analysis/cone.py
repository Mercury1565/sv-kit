"""Gate-level fan-in cone of a net, with the critical path from a timing report overlaid.

``cone(netlist, net, depth, path=TimingPath)`` returns an ELK-ready graph::

    {nodes: [{id, cell, pins, on_path, delay?, kind}],
     edges: [{id, src, dst, net, on_path, src_pin, dst_pin}],
     unresolved: [{index, inst, pin, reason}], root_net, path_cells}

Nodes are netlist cell instances (``kind: "cell"``), top-level ports (``"port"``) and constants
(``"const"``). ``delay`` is the cell delay in ps from the path (the ``incr`` of the cell's output
pin row; input-pin rows are wire delays and are reported as ``wire_delay``).
"""
from __future__ import annotations

from typing import Any, Optional

from svkit.design.netlist import CONST_NETS, Netlist
from svkit.schemas import TimingPath


def map_path(nl: Netlist, path: TimingPath) -> tuple[list[dict], list[dict]]:
    """Map each TimingPoint to a netlist cell/port. Returns (mapped, unresolved)."""
    mapped, unresolved = [], []
    for i, pt in enumerate(path.points):
        if not pt.inst:
            n = nl.find_net(pt.pin) or (nl.find_net(pt.net) if pt.net else None)
            if n is not None:
                mapped.append({"index": i, "kind": "port", "id": f"port:{n}", "net": n, "point": pt})
            else:
                unresolved.append({"index": i, "inst": pt.inst, "pin": pt.pin, "reason": "port/net not in netlist"})
            continue
        c = nl.find_cell(pt.inst)
        if c is None:
            unresolved.append({"index": i, "inst": pt.inst, "pin": pt.pin, "reason": "instance not in netlist"})
            continue
        net = nl.pin_net(c, pt.pin)
        if net is None:
            unresolved.append({"index": i, "inst": pt.inst, "pin": pt.pin, "reason": f"cell {c} has no pin {pt.pin}"})
            continue
        mapped.append({"index": i, "kind": "cell", "id": c, "pin": pt.pin, "net": net, "point": pt,
                       "dir": nl.pin_dir(c, pt.pin) or "in"})
    return mapped, unresolved


def endpoint_net(nl: Netlist, path: TimingPath) -> Optional[str]:
    mapped, _ = map_path(nl, path)
    return mapped[-1]["net"] if mapped else None


def cone(nl: Netlist, net: Optional[str], depth: int = 4, *, path: Optional[TimingPath] = None,
         max_nodes: int = 2000) -> dict[str, Any]:
    mapped, unresolved = map_path(nl, path) if path is not None else ([], [])
    if net is None:
        if not mapped:
            raise ValueError("no net given and the timing path has no resolvable points")
        net = mapped[-1]["net"]
    root = nl.find_net(net)
    if root is None:
        c = nl.find_cell(net)       # allow a cell instance: use its first output pin net
        if c is not None:
            outs = [ns[-1] for p, ns in nl.cells[c].pins.items() if nl.cells[c].dirs.get(p) == "out" and ns]
            root = outs[0] if outs else None
    if root is None:
        raise KeyError(f"net {net!r} not found in netlist")

    ports = {nl.canon(p): d for p, d in nl.ports.items()}
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def src_node(n: str) -> Optional[str]:
        """Node id driving net n (adds port/const nodes)."""
        if n in nl.driver:
            return nl.driver[n][0]
        if n in CONST_NETS:
            nid = f"const:{n}"
            nodes.setdefault(nid, {"id": nid, "cell": n, "kind": "const", "pins": {}, "on_path": False})
            return nid
        nid = f"port:{n}"
        nodes.setdefault(nid, {"id": nid, "cell": "PORT" if n in ports else "UNDRIVEN", "kind": "port",
                               "pins": {}, "on_path": False, "dir": ports.get(n)})
        return nid

    def add_cell(c: str) -> dict:
        if c not in nodes:
            cc = nl.cells[c]
            nodes[c] = {"id": c, "cell": cc.type, "kind": "cell", "on_path": False,
                        "pins": {p: {"dir": cc.dirs.get(p, "in"), "nets": ns} for p, ns in cc.pins.items()}}
        return nodes[c]

    def add_edge(s: str, d: str, n: str, sp: Optional[str], dp: Optional[str]) -> None:
        key = (s, d, n, dp)
        if key not in edges:
            edges[key] = {"id": f"e{len(edges)}", "src": s, "dst": d, "net": n, "src_pin": sp, "dst_pin": dp,
                          "on_path": False}

    # breadth-first fan-in walk
    start = src_node(root)
    frontier = [start] if start in nl.cells else []
    if start in nl.cells:
        add_cell(start)
    seen = set(frontier)
    for _ in range(max(depth, 0)):
        nxt = []
        for c in frontier:
            cc = nl.cells[c]
            for p, ns in cc.pins.items():
                if cc.dirs.get(p, "in") == "out":
                    continue
                for n in ns:
                    s = src_node(n)
                    if s is None:
                        continue
                    sp = nl.driver[n][1] if n in nl.driver else None
                    add_edge(s, c, n, sp, p)
                    if s in nl.cells and s not in seen:
                        if len(nodes) >= max_nodes:
                            continue
                        seen.add(s)
                        add_cell(s)
                        nxt.append(s)
        frontier = nxt
        if not frontier:
            break

    # overlay the timing path
    path_cells: list[str] = []
    prev_out: Optional[dict] = None
    for m in mapped:
        pt = m["point"]
        if m["kind"] == "port":
            nid = m["id"]
            nodes.setdefault(nid, {"id": nid, "cell": "PORT", "kind": "port", "pins": {}, "dir": ports.get(m["net"])})
            nodes[nid]["on_path"] = True
            prev_out = {"id": nid, "net": m["net"], "pin": None}
            if nid not in path_cells:
                path_cells.append(nid)
            continue
        node = add_cell(m["id"])
        node["on_path"] = True
        if m["id"] not in path_cells:
            path_cells.append(m["id"])
        if m["dir"] == "out":
            node["delay"] = round(node.get("delay", 0.0) + pt.incr, 3)
            node.setdefault("arrival", pt.arrival)
            node["arrival"] = pt.arrival
            prev_out = {"id": m["id"], "net": m["net"], "pin": m["pin"]}
        else:
            node["wire_delay"] = round(node.get("wire_delay", 0.0) + pt.incr, 3)
            if prev_out is not None and prev_out["id"] != m["id"]:
                add_edge(prev_out["id"], m["id"], m["net"], prev_out["pin"], m["pin"])
                edges[(prev_out["id"], m["id"], m["net"], m["pin"])]["on_path"] = True
    for n in nodes.values():
        n.setdefault("on_path", False)
    return {"root_net": root, "nodes": list(nodes.values()), "edges": list(edges.values()),
            "unresolved": unresolved, "path_cells": path_cells,
            "slack": path.slack if path is not None else None}
