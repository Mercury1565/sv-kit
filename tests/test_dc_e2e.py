"""End-to-end Design Compiler runs on the fixtures (real dc_shell-t; CAEN only).

Needs `dc_shell-t` on PATH (marker `synopsys`) and a library in the environment:
SVKIT_DC_TARGET_LIB (.db) or SVKIT_DC_LIBERTY (.lib, converted with Library Compiler).
"""
import os

import pytest

from svkit import config as C
from svkit.core.runner import Runner

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = [pytest.mark.synopsys, pytest.mark.slow]


def _need(name: str):
    if not (FIXTURES / name / "svkit.toml").is_file():
        pytest.skip(f"fixture {name} not present")
    cfg = C.load(FIXTURES / name)
    if "dc" not in cfg.flows.synth:
        pytest.skip(f"fixture {name} has no [flows.synth.dc]")
    if not (os.environ.get("SVKIT_DC_TARGET_LIB") or os.environ.get("SVKIT_DC_LIBERTY")):
        pytest.skip("set SVKIT_DC_TARGET_LIB or SVKIT_DC_LIBERTY to run DC end to end")


@pytest.mark.parametrize("name", ["counter", "pipe5"])
def test_dc_synth(name, fixture_copy):
    _need(name)
    root = fixture_copy(name)
    cfg = C.load(root)
    run = Runner(cfg).synth(flow="dc", use_cache=False)
    assert run.status == "done", run.message
    m = run.metrics
    assert m["tool"] == "dc" and m["wns"] is not None and m["area_total"] and m["area_total"] > 0
    assert m["cell_count"] and m["cells_by_type"] and m["paths"]
    assert m["netlist"] == "netlist.v"
    if "clock_period" in cfg.knobs and cfg.knobs["clock_period"].default is not None:
        assert m["period"] == pytest.approx(float(cfg.knobs["clock_period"].default))
    # a tighter clock must not improve slack by more than noise
    tight = Runner(cfg).synth(flow="dc", sets={"clock_period": max(100, int(m["period"] or 1000) // 4)},
                              use_cache=False)
    assert tight.status == "done", tight.message
    assert tight.metrics["wns"] <= m["wns"] + 1.0
