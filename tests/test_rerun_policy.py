"""Failed runs without a design result are not cached; parallel wrap tests warm up shared prerequisites."""
import shutil

import pytest

from svkit import config as C
from svkit.core.rundb import RunDB
from svkit.core.runner import Runner


def _run(db, key, status, metrics):
    r = db.create_run("sim", "x", test="t", cache_key=key)
    db.update_run(r.id, status=status, metrics=metrics)
    return r.id


def test_infrastructure_failures_are_not_reused(tmp_path):
    db = RunDB(tmp_path / ".svkit")
    _run(db, "k1", "failed", {"passed": False, "reason": "exit code 2"})
    assert db.find_cached("k1") is None                      # nothing compared: rerun
    rid = _run(db, "k2", "failed", {"passed": False, "divergence": {"line": 3}})
    assert db.find_cached("k2").id == rid                    # a real output mismatch is a result
    ok = _run(db, "k3", "passed", {"passed": True})
    _run(db, "k3", "failed", {"passed": False, "reason": "Text file busy"})
    assert db.find_cached("k3").id == ok


TOML = """
[project]
name = "race"
top = "top"
sources = { glob = ["*.sv"] }

[flows.sim.custom]
cmd = "make out/{test}.out"
golden = { actual = "out/{test}.out", expected = "exp/{test}.out" }
tests = { names = ["a", "b", "c", "d", "e", "f"] }
"""

# a shared tool created on demand by the first make that needs it; two makes racing on it fail
MAKEFILE = """\
tool:
\t@if [ -e tool.lock ]; then echo "tool: Text file busy" >&2; exit 126; fi
\ttouch tool.lock; sleep 0.3; printf '#!/bin/sh\\necho ok\\n' > tool; chmod +x tool; rm tool.lock
out/%.out: tool
\tmkdir -p out; ./tool > $@
"""


@pytest.mark.skipif(not shutil.which("make"), reason="needs make")
def test_parallel_wrap_tests_warm_up_first(tmp_path, monkeypatch):
    from svkit.adapters import registry
    from svkit.adapters.sim.verilator import VerilatorAdapter

    class Custom(VerilatorAdapter):       # the generic wrap path, without a tool requirement
        name = "custom"
        tools = ()
    monkeypatch.setitem(registry.BUILTIN, "custom", f"{__name__}:Custom")
    globals()["Custom"] = Custom
    (tmp_path / "top.sv").write_text("module top; endmodule\n")
    (tmp_path / "Makefile").write_text(MAKEFILE)
    (tmp_path / "exp").mkdir()
    for t in "abcdef":
        (tmp_path / "exp" / f"{t}.out").write_text("ok\n")
    (tmp_path / "svkit.toml").write_text(TOML)
    gid, runs = Runner(C.load(tmp_path)).sim_all(jobs=6, use_cache=False)
    assert [r.status for r in runs] == ["passed"] * 6, [r.message for r in runs]


def test_cache_hits_point_at_the_original_run_and_show_its_log(tmp_path):
    from fastapi.testclient import TestClient
    from svkit.server.app import create_app
    (tmp_path / "top.sv").write_text("module top; endmodule\n")
    (tmp_path / "svkit.toml").write_text('[project]\nname = "c"\ntop = "top"\nsources = { glob = ["*.sv"] }\n'
                                         '[flows.sim.verilator]\nmode = "builtin"\n')
    cfg = C.load(tmp_path)
    r = Runner(cfg)
    orig = _run(r.db, "kk", "passed", {"passed": True})
    r.db.update_run(orig, artifacts={"sim.log": "sim.log"})
    (r.db.run_dir(orig) / "svkit.log").write_text("built\nran\n")
    first = r._cached("kk", "sim", "x", test="t")
    second = r._cached("kk", "sim", "x", test="t")
    assert first.cached_from == orig and second.cached_from == orig
    assert second.artifacts == {"sim.log": f"../{orig}/sim.log"}
    lines = TestClient(create_app(cfg)).get(f"/api/runs/{second.id}/log").json()["lines"]
    assert lines[-2:] == ["built", "ran"] and orig in lines[0]


def test_backends_name_the_missing_tool(tmp_path):
    from svkit.core.runner import backends
    (tmp_path / "top.sv").write_text("module top; endmodule\n")
    (tmp_path / "svkit.toml").write_text('[project]\nname = "c"\ntop = "top"\nsources = { glob = ["*.sv"] }\n'
                                         '[tools]\ndc_shell-t = "svkit-no-dc"\nyosys = "true"\nsta = "true"\n'
                                         '[flows.synth.dc]\ncmd = "make syn"\n')
    b = backends(C.load(tmp_path))["synth"]
    assert b["configured"] == "dc" and b["missing"] == ["svkit-no-dc"] and b["runs_on"] == "yosys"
