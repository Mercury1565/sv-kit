"""End to end on the open-source flow: init -> sim -> synth -> server API returns the Runs."""
import pytest
from fastapi.testclient import TestClient

from svkit import config as C
from svkit import init as I
from svkit.core.runner import Runner
from svkit.server.app import create_app

pytestmark = [pytest.mark.oss, pytest.mark.slow]


def test_init_sim_synth_serve(fixture_copy):
    root = fixture_copy("pipe5")
    (root / "svkit.toml").unlink()
    out, notes = I.init(root)
    cfg = C.load(out)
    # the generated file defaults to the first detected flows (VCS/DC on a Synopsys host);
    # on this flow test pick the open-source ones explicitly
    r = Runner(cfg)
    # init writes wrap mode (`make sim-verilator`); the Makefile shares one build dir -> serial
    gid, sims = r.sim_all(flow="verilator", jobs=1)
    status = {x.test: x.status for x in sims}
    assert status == {"sum": "passed", "hazards": "passed", "memcopy": "passed", "fib": "passed",
                      "ldst_fwd": "failed"}, {x.test: x.message for x in sims}
    syn = r.synth(flow="yosys")
    assert syn.status == "done", syn.message
    assert syn.metrics["wns"] > 0 and syn.metrics["area_total"] > 0

    client = TestClient(create_app(cfg))
    runs = client.get("/api/runs").json()
    ids = {x["id"] for x in runs}
    assert {x.id for x in sims} | {syn.id} <= ids
    one = client.get(f"/api/runs/{syn.id}").json()
    assert one["metrics"]["tool"] == "yosys+opensta" and one["metrics"]["paths"]
    bad = next(x for x in sims if x.test == "ldst_fwd")
    assert client.get(f"/api/runs/{bad.id}").json()["metrics"]["divergence"]["cycle"] == 10
    proj = client.get("/api/project").json()
    assert proj["top"] == "p5_core"
