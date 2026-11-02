"""Clock sweep: search logic with a fake runner (deterministic, no tools) + an OSS integration test."""
import json
import threading
import time
from pathlib import Path

import pytest

from svkit import config as C
from svkit.analysis import sweep as S
from svkit.config import ConfigError
from svkit.core.events import EventBus
from svkit.core.rundb import RunDB

REPO = Path(__file__).resolve().parents[1]

TOML = """
[project]
name = "demo"
top = "core"
[flows.synth.yosys]
mode = "builtin"
license_seats = {seats}
[knobs.clock_period]
env = "CLOCK_PERIOD"
unit = "ps"
{default}
"""


def _cfg(tmp_path, *, seats=0, default="default = 2000", knob=True):
    text = TOML.format(seats=seats, default=default)
    if not knob:
        text = text.split("[knobs.clock_period]")[0]
    (tmp_path / "svkit.toml").write_text(text)
    return C.load(tmp_path)


class FakeRunner:
    """Mimics Runner.synth: records a run whose metrics come from wns_fn(T)."""

    def __init__(self, cfg, wns_fn, hold_fn=None, delay=0.0):
        self.cfg = cfg
        self.db = RunDB.for_project(cfg.root)
        self.bus = EventBus()
        self.fn, self.hold = wns_fn, hold_fn
        self.calls: list[float] = []
        self.delay = delay
        self.active = self.max_active = 0
        self._l = threading.Lock()

    def synth(self, *, flow=None, experiment=None, sets=None, group_id=None, kind="synth", use_cache=True):
        t = sets["clock_period"]
        with self._l:
            self.calls.append(t)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        wns = self.fn(float(t))
        r = self.db.create_run(kind, "yosys", knobs=dict(sets), group_id=group_id, status="running")
        if wns is None:
            self.db.update_run(r.id, status="error", message="tool crashed")
        else:
            m = {"tool": "fake", "top": "core", "period": float(t), "wns": wns, "area_total": 1e6 / t,
                 "power": {"total": t / 1000}}
            if self.hold:
                m["hold_wns"] = self.hold(float(t))
            self.db.update_run(r.id, status="done", metrics=m)
        with self._l:
            self.active -= 1
        return self.db.get_run(r.id)


def _check_bracket(res, true_min, tol):
    assert res.status == "done"
    assert res.t_min is not None and res.t_min >= true_min - 1e-9
    assert res.t_min - true_min <= tol
    below = [p for p in res.points if not p.passed and p.period < res.t_min]
    assert below and res.t_min - max(p.period for p in below) <= tol


def test_linear(tmp_path):
    r = FakeRunner(_cfg(tmp_path), lambda t: t - 1234)
    res = S.sweep_clock(r, tol=50, jobs=2)
    _check_bracket(res, 1234, 50)
    assert len(res.points) <= 8
    assert r.calls[0] == 2000                      # starts at the knob default
    assert [p.period for p in res.points] == sorted(p.period for p in res.points)
    # every point is a sweep-point run of the group; group result holds the final SweepResult
    g = r.db.get_group(res.group_id)
    assert g.kind == "sweep" and g.status == "done" and g.result["t_min"] == res.t_min
    assert g.params["tol"] == 50 and g.params["start"] == 2000
    runs = r.db.list_runs(group_id=res.group_id)
    assert {x.kind for x in runs} == {"sweep-point"} and len(runs) == len(res.points)
    assert res.t_min_run_id in {x.id for x in runs}


def test_poor_estimate_and_tight_tol(tmp_path):
    # WNS changes 3x slower than the period: the T0 - WNS0 estimate is far off
    r = FakeRunner(_cfg(tmp_path, default="default = 5000"), lambda t: (t - 1800) / 3)
    res = S.sweep_clock(r, tol=5, jobs=3)
    _check_bracket(res, 1800, 5)


def test_start_failing_goes_looser(tmp_path):
    # wns saturates: estimate from T0 is too optimistic
    r = FakeRunner(_cfg(tmp_path, default="default = 500"), lambda t: -100.0 if t < 3100 else t - 3100)
    res = S.sweep_clock(r, tol=50, jobs=2)
    _check_bracket(res, 3100, 50)
    assert len(res.points) <= 20


def test_non_monotonic_picks_tightest_pass(tmp_path):
    # passes for T >= 1000, except a failing island [1300, 1320): a tighter target passes where a
    # looser one failed. Bisection from T0=1310 would never look below the island.
    def wns(t):
        return -5.0 if 1300 <= t < 1320 else t - 1000
    r = FakeRunner(_cfg(tmp_path, default="default = 1310"), wns)
    res = S.sweep_clock(r, tol=20, jobs=3)
    _check_bracket(res, 1000, 20)
    assert any(not p.passed and p.period > res.t_min for p in res.points)   # the island was seen


def test_hold_and_error_points(tmp_path):
    # below 1500 the tool errors (counted as failing), hold fails below 1600
    r = FakeRunner(_cfg(tmp_path), lambda t: None if t < 1500 else t - 1200, hold_fn=lambda t: t - 1600)
    res = S.sweep_clock(r, tol=25, jobs=2)
    _check_bracket(res, 1600, 25)
    errs = [p for p in res.points if p.wns is None]
    assert all(not p.passed for p in errs)


def test_license_seats_cap_parallelism(tmp_path):
    r = FakeRunner(_cfg(tmp_path, seats=1), lambda t: t - 1234, delay=0.01)
    res = S.sweep_clock(r, tol=50, jobs=4)
    assert r.max_active == 1
    assert r.db.get_group(res.group_id).params["width"] == 1
    _check_bracket(res, 1234, 50)
    (tmp_path / "b").mkdir()
    r2 = FakeRunner(_cfg(tmp_path / "b"), lambda t: t - 1234, delay=0.02)
    S.sweep_clock(r2, tol=50, jobs=3)
    assert r2.max_active <= 3 and r2.max_active >= 2


def test_events_and_max_points(tmp_path):
    r = FakeRunner(_cfg(tmp_path), lambda t: -1.0)          # never passes
    q = r.bus.subscribe()
    res = S.sweep_clock(r, tol=50, jobs=2, max_points=7)
    assert res.status == "failed" and res.t_min is None and len(res.points) == 7
    assert r.db.get_group(res.group_id).status == "failed"
    evs = []
    while not q.empty():
        evs.append(q.get())
    ups = [e for e in evs if e["type"] == "group.updated" and e["group_id"] == res.group_id]
    assert len(ups) >= 8 and ups[-1]["status"] == "failed"
    assert len(ups[-2]["result"]["points"]) == 7


def test_start_override_and_config_errors(tmp_path):
    r = FakeRunner(_cfg(tmp_path), lambda t: t - 700)
    res = S.sweep_clock(r, tol=50, jobs=1, start=900)
    assert r.calls[0] == 900
    _check_bracket(res, 700, 50)
    d1 = tmp_path / "noknob"
    d1.mkdir()
    with pytest.raises(ConfigError, match="clock_period"):
        S.sweep_clock(FakeRunner(_cfg(d1, knob=False), lambda t: 0), tol=50)
    d2 = tmp_path / "nodefault"
    d2.mkdir()
    with pytest.raises(ConfigError, match="--start"):
        S.sweep_clock(FakeRunner(_cfg(d2, default=""), lambda t: 0), tol=50)


def test_first_point_error_fails_cleanly(tmp_path):
    r = FakeRunner(_cfg(tmp_path), lambda t: None)
    res = S.sweep_clock(r, tol=50)
    assert res.status == "failed" and len(res.points) == 1 and not res.points[0].passed


# ---------------------------------------------------------------- real flow (Track 1 fixtures)
@pytest.mark.oss
@pytest.mark.slow
@pytest.mark.parametrize("fx", ["pipe5", "counter"])
def test_sweep_oss_fixture(fx, fixture_copy):
    if not (REPO / "fixtures" / fx / "svkit.toml").is_file():
        pytest.skip(f"fixture {fx} not present yet")
    from svkit.core.runner import Runner
    root = fixture_copy(fx)
    cfg = C.load(root)
    if "clock_period" not in cfg.knobs:
        pytest.skip(f"fixture {fx} has no clock_period knob")
    flow = "yosys" if "yosys" in cfg.flows.synth else None
    res = S.sweep_clock(Runner(cfg), tol=50, jobs=2, flow=flow)
    assert res.status == "done" and res.t_min is not None
    gold = REPO / "fixtures" / "golden" / "oss" / fx / "expected.json"
    if gold.is_file():
        exp = json.loads(gold.read_text()).get("t_min_ps")
        if exp is not None:
            assert abs(res.t_min - exp) <= 50


def test_cli_sweep_config_error(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from svkit.cli import app
    _cfg(tmp_path, knob=False)
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["sweep", "clock", "--tol", "50"])
    assert res.exit_code == 2 and "clock_period" in res.output


# ---------------------------------------------------------------- real Runner, stub adapter
from svkit.adapters.base import SynthAdapter  # noqa: E402
from svkit.schemas import SynthResult  # noqa: E402


class StubSynth(SynthAdapter):
    name = "stubsynth"

    def run(self, ctx, flow):
        p = ctx.plan.period_ps
        return SynthResult(tool="stub", top=self.cfg.project.top, period=p, wns=p - 1234.0, hold_wns=5.0,
                           area_total=1e6 / p)

    def parse(self, files, *, top):
        raise NotImplementedError


STUB_TOML = """
[project]
name = "demo"
top = "core"
[flows.synth.stubsynth]
mode = "builtin"
[knobs.clock_period]
env = "CLOCK_PERIOD"
unit = "ns"
default = 2000
"""


@pytest.fixture
def stub_project(tmp_path, monkeypatch):
    from svkit.adapters import registry
    monkeypatch.setitem(registry.BUILTIN, "stubsynth", f"{__name__}:StubSynth")
    (tmp_path / "svkit.toml").write_text(STUB_TOML)
    return C.load(tmp_path)


def test_runner_integration_and_cache(stub_project):
    from svkit.core.runner import Runner
    r = Runner(stub_project)
    res = S.sweep_clock(r, tol=50, jobs=2)
    _check_bracket(res, 1234, 50)
    runs = r.db.list_runs(group_id=res.group_id)
    assert all(x.kind == "sweep-point" and x.status == "done" for x in runs)
    assert {float(x.knobs["clock_period"]) for x in runs} == {p.period for p in res.points}
    # a second sweep re-uses every point from the cache
    res2 = S.sweep_clock(r, tol=50, jobs=2)
    assert res2.t_min == res.t_min and res2.group_id != res.group_id
    assert all(x.cached_from for x in r.db.list_runs(group_id=res2.group_id))


def test_sweep_job_through_api(stub_project):
    from fastapi.testclient import TestClient
    from svkit.server.app import create_app
    c = TestClient(create_app(stub_project))
    jid = c.post("/api/jobs", json={"kind": "sweep", "args": {"tol": 50, "jobs": 2}}).json()["job_id"]
    for _ in range(200):
        job = next(j for j in c.get("/api/jobs").json() if j["job_id"] == jid)
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    res = c.get(f"/api/sweeps/{job['group_id']}").json()
    assert res["status"] == "done" and abs(res["t_min"] - 1234) <= 50
    g = c.get(f"/api/groups/{job['group_id']}").json()
    assert len(g["runs"]) == len(res["points"])
