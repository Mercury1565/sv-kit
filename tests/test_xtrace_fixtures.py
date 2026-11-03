"""MVP acceptance: the X tracer pinpoints every planted class-1/2/4 bug in the xbug_* fixtures.

The committed goldens (``fixtures/golden/xtrace/<fx>/waves.vcd``) were produced with iverilog
(4-state); Verilator is 2-state and never shows X. ``python tests/test_xtrace_fixtures.py``
regenerates them. The ``iverilog`` test re-simulates and checks the trace results are identical.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "fixtures"
GOLD = FIX / "golden" / "xtrace"
FIXTURES = ["xbug_noreset", "xbug_mem", "xbug_input"]

_OSS = Path.home() / "tools" / "oss-cad-suite" / "bin"


def _tool(name: str):
    return shutil.which(name) or (str(_OSS / name) if (_OSS / name).is_file() else None)


HAVE_IVERILOG = bool(_tool("iverilog") and _tool("vvp"))
needs_iverilog = pytest.mark.skipif(not HAVE_IVERILOG, reason="iverilog/vvp (4-state simulator) not found")


def simulate(fx: str, out_dir: Path) -> Path:
    """Simulate fixture ``fx`` with iverilog in out_dir; returns the VCD."""
    from svkit import config as C
    from svkit.core import project as P

    cfg = C.load(FIX / fx)
    files = [str(f) for f in P.resolve_sources(cfg) + P.resolve_sources(cfg, cfg.project.tb_sources)]
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "VERILATOR_ROOT"}
    subprocess.run(["nice", _tool("iverilog"), "-g2012", "-s", cfg.project.tb_top, "-o", "sim.vvp", *files],
                   cwd=out_dir, check=True, env=env, timeout=120)
    subprocess.run(["nice", _tool("vvp"), "-n", "sim.vvp"], cwd=out_dir, check=True, env=env, timeout=120,
                   capture_output=True)
    return out_dir / "waves.vcd"


def run_cases(fx: str, vcd: Path) -> list[dict]:
    from svkit import config as C
    from svkit.analysis import xtrace as X
    from svkit.design import graph as G
    from svkit.design.wavesrc import open_waves

    cfg = C.load(FIX / fx)
    g = G.build(cfg, use_cache=False)
    w = open_waves(vcd)
    exp = json.loads((GOLD / fx / "expected.json").read_text())
    out = []
    for case in exp["cases"]:
        node = X.trace(g, w, case["signal"], case["time"], depth=case.get("depth", 12))
        roots = [(r.signal, r.cause.value) for r in X.leaves(node)]
        out.append({"case": case, "node": node, "roots": roots})
    return out


@pytest.mark.parametrize("fx", FIXTURES)
def test_planted_bugs_found(fx):
    for r in run_cases(fx, GOLD / fx / "waves.vcd"):
        case = r["case"]
        want = (case["root"], case["cause"])
        assert want in r["roots"], f"{fx} {case['signal']}@{case['time']}: expected {want}, got {r['roots']}"
        if case.get("only"):
            assert r["roots"] == [want], f"{fx}: extra root causes {r['roots']}"
        if "min_depth" in case:
            def depth(n):
                return 1 + max((depth(c) for c in n.children), default=0)
            assert depth(r["node"]) >= case["min_depth"]


@needs_iverilog
@pytest.mark.parametrize("fx", FIXTURES)
def test_regenerated_waves_give_same_traces(fx, tmp_path):
    vcd = simulate(fx, tmp_path / fx)
    new = run_cases(fx, vcd)
    old = run_cases(fx, GOLD / fx / "waves.vcd")
    assert [r["node"].model_dump() for r in new] == [r["node"].model_dump() for r in old]


if __name__ == "__main__":   # regenerate goldens: python tests/test_xtrace_fixtures.py [fx ...]
    import tempfile
    sys.path.insert(0, str(REPO / "src"))
    for fx in sys.argv[1:] or FIXTURES:
        with tempfile.TemporaryDirectory() as d:
            vcd = simulate(fx, Path(d))
            (GOLD / fx).mkdir(parents=True, exist_ok=True)
            shutil.copy(vcd, GOLD / fx / "waves.vcd")
            print("wrote", GOLD / fx / "waves.vcd")
