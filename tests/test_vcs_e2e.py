"""End-to-end VCS runs on the fixtures (real vcs; CAEN only, marker `synopsys`)."""
import pytest

from svkit import config as C
from svkit.core.runner import Runner

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = [pytest.mark.synopsys, pytest.mark.slow]


@pytest.mark.parametrize("name", ["counter", "pipe5"])
def test_vcs_sim(name, fixture_copy):
    if not (FIXTURES / name / "svkit.toml").is_file():
        pytest.skip(f"fixture {name} not present")
    root = fixture_copy(name)
    cfg = C.load(root)
    if "vcs" not in cfg.flows.sim:
        pytest.skip(f"fixture {name} has no [flows.sim.vcs]")
    r = Runner(cfg)
    tests = r.tests("vcs")
    assert tests, "fixture defines no tests"
    run = r.sim(tests[0], flow="vcs", use_cache=False)
    assert run.status == "passed", run.message
    assert run.metrics["waves"] in ("waves.vcd", "waves.fst")
    assert run.metrics["tool"] == "vcs"
