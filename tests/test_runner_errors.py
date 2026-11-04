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


def test_first_error_line_in_failure_message(tmp_path):
    from svkit.adapters.synth.yosys import _failed
    from svkit.core.fsutil import first_error
    log = tmp_path / "synth.log"
    log.write_text("1. Executing Verilog-2005 frontend\n"
                   f"{tmp_path}/verilog/queue.sv:39: ERROR: syntax error, unexpected ';'\n"
                   "Parsing SystemVerilog input\n")
    assert first_error(log, root=tmp_path) == "verilog/queue.sv:39: ERROR: syntax error, unexpected ';'"
    msg = _failed("yosys", 1, log, tmp_path)
    assert msg == "yosys failed (exit 1): verilog/queue.sv:39: ERROR: syntax error, unexpected ';' (full log: synth.log)"
    log.write_text("nothing useful\n")
    assert _failed("yosys", 1, log, tmp_path) == "yosys failed (exit 1); see artifact synth.log"


def test_error_text_drops_type_for_tool_failures():
    from svkit.core.runner import _error_text
    assert _error_text(RuntimeError("yosys failed (exit 1): x")) == "yosys failed (exit 1): x"
    assert _error_text(KeyError("k")) == "KeyError: 'k'"
