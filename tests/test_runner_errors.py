"""Readable errors when a flow cannot run on this host."""
import pytest

from svkit import config as C
from svkit.config import ConfigError
from svkit.core.runner import Runner


def _proj(tmp_path, sim: str):
    (tmp_path / "rtl.sv").write_text("module top; endmodule\n")
    (tmp_path / "svkit.toml").write_text(f"""
[project]
name = "p"
top = "top"
sources = {{ glob = ["rtl.sv"] }}

[tools]
vcs = "svkit-no-such-vcs"
verilator = "svkit-no-such-verilator"

{sim}
""")
    return C.load(tmp_path)


def test_build_cmd_failure_shows_log_tail_and_tool_hint(tmp_path):
    cfg = _proj(tmp_path, """
[flows.sim.vcs]
cmd = "true"
build_cmd = "echo first-line; echo the-real-reason >&2; exit 2"
tests = { names = ["t1"] }
""")
    with pytest.raises(ConfigError) as e:
        Runner(cfg).sim_all(flow="vcs")
    msg = str(e.value)
    assert "exit 2" in msg and "the-real-reason" in msg
    assert "svkit-no-such-vcs" in msg and "not on PATH" in msg


def test_builtin_flow_fails_fast_without_tool(tmp_path):
    cfg = _proj(tmp_path, """
[flows.sim.verilator]
mode = "builtin"
tests = { names = ["t1"] }
""")
    with pytest.raises(ConfigError, match="svkit-no-such-verilator"):
        Runner(cfg).sim("t1", flow="verilator")
