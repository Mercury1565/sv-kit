"""Unit-test suites: one testbench file per test, listed apart from the system tests."""
import shutil
import sqlite3
import textwrap

import pytest

from svkit import config as C
from svkit import init as I
from svkit.core import unit as U
from svkit.core.rundb import RunDB
from svkit.core.runner import Runner, resolve_sim

ACC = """\
module acc(input logic clock, input logic reset, input logic en, input logic [7:0] d, output logic [7:0] q);
  always_ff @(posedge clock)
    if (reset) q <= '0;
    else if (en) q <= q + d + BUG;
endmodule
"""

# system testbench: instantiates the design, portless, has initial
SYS_TB = """\
module sys_tb;
  logic clock = 0, reset = 1, en = 0; logic [7:0] d = 0, q;
  acc dut(.clock(clock), .reset(reset), .en(en), .d(d), .q(q));
  always #5 clock = ~clock;
  initial begin
    @(negedge clock) reset = 0;
    repeat (3) @(negedge clock);
    $display("q=%0d", q);
    $finish;
  end
endmodule
"""

UNIT_TB = """\
`timescale 1ns/1ps
module acc_tb;
  logic clock = 0, reset = 1, en = 0; logic [7:0] d = 0, q;
  int errors = 0;
  acc dut(.clock(clock), .reset(reset), .en(en), .d(d), .q(q));
  always #5 clock = ~clock;
  task automatic check(input logic [7:0] want);
    if (q !== want) begin
      $display("[%0t] ERROR: q=%0d, expected %0d", $time, q, want);
      errors++;
    end
  endtask
  initial begin
    @(negedge clock) reset = 0;
    en = 1; d = 3;
    @(negedge clock) check(3);
    @(negedge clock) check(6);
    if (errors == 0) $display("@@@ PASSED");
    else $display("@@@ FAILED: %0d errors", errors);
    $finish;
  end
endmodule
"""

TOML = """
[project]
name = "acc"
top = "acc"
tb_top = "sys_tb"
sources = { glob = ["rtl/*.sv"] }
tb_sources = { glob = ["tb/sys_test.sv"] }
include = ["rtl"]
%s

[flows.sim.verilator]
mode = "builtin"
tests = { names = ["smoke"] }

[flows.sim.unit]
adapter = "%s"
mode = "builtin"
testbench = "tb/{test}_test.sv"
pass_regex = '@@@ PASSED'
fail_regex = '@@@ FAILED'
%s
"""


def make(tmp_path, bug=0, adapter="verilator", extra="", tools=""):
    (tmp_path / "rtl").mkdir(exist_ok=True)
    (tmp_path / "tb").mkdir(exist_ok=True)
    (tmp_path / "rtl" / "acc.sv").write_text(f"`define BUG {bug}\n" + ACC.replace("BUG", "`BUG"))
    (tmp_path / "tb" / "sys_test.sv").write_text(SYS_TB)
    (tmp_path / "tb" / "acc_test.sv").write_text(UNIT_TB)
    (tmp_path / "svkit.toml").write_text(TOML % (tools, adapter, extra))
    return C.load(tmp_path)


# ---------------------------------------------------------------- config / discovery
def test_discover_tests_from_testbench_template(tmp_path):
    cfg = make(tmp_path)
    (tmp_path / "tb" / "fifo_test.sv").write_text("module fifo_tb; endmodule\n")
    (tmp_path / "tb" / "wip_test.sv").write_text("module wip_tb; endmodule\n")
    cfg = C.load(tmp_path)
    cfg.flows.sim["unit"].tests.exclude = ["wip"]
    # sys_test.sv is the system testbench ([project].tb_sources): never a unit test
    assert U.discover(cfg, cfg.flows.sim["unit"]) == ["acc", "fifo"]
    assert Runner(cfg).tests("unit") == ["acc", "fifo"]


def test_testbench_needs_test_placeholder(tmp_path):
    with pytest.raises(Exception, match=r"\{test\}"):
        C.SimFlow(mode="builtin", testbench="tb/acc_test.sv")


def test_suites_and_default_flow(tmp_path):
    cfg = make(tmp_path)
    # the unit flow is never the default sim flow, even when listed first
    cfg.flows.sim = {"unit": cfg.flows.sim["unit"], "verilator": cfg.flows.sim["verilator"]}
    assert cfg.sim_flow(None)[0] == "verilator"
    assert cfg.sim_adapter("unit") == "verilator"
    su = {s["id"]: s for s in Runner(cfg).suites()}
    assert su["system"]["tests"] == ["smoke"] and su["system"]["default"] and not su["system"]["unit"]
    assert su["unit"]["tests"] == ["acc"] and su["unit"]["unit"] and su["unit"]["flow"] == "unit"
    assert su["unit"]["label"] == "Unit tests"


def test_top_detection(tmp_path):
    cfg = make(tmp_path)
    fl = cfg.flows.sim["unit"]
    assert U.testbench_top(cfg, fl, "acc") == "acc_tb"
    (tmp_path / "tb" / "two_test.sv").write_text(textwrap.dedent("""\
        // module not_this;
        module helper(input logic a); endmodule
        module two_tb;
          logic a;
          helper h(.a(a));
        endmodule
        """))
    assert U.testbench_top(cfg, fl, "two") == "two_tb"
    fl.top = "{test}_top"
    assert U.testbench_top(cfg, fl, "two") == "two_top"


def test_log_divergence_first_error_with_time():
    log = "== phase 1\n[111000] ERROR (full): full=0, expected 1\nERROR: later\n@@@ FAILED: 2 errors\n"
    d = U.log_divergence(log, None)
    assert d.line == 2 and d.time == 111000 and d.source == "log" and "full=0" in d.actual
    d = U.log_divergence("t=5 boom\nCHECK BAD @ 70 ns\n", r"CHECK BAD @ (?P<time>\d+)")
    assert d.line == 2 and d.time == 70
    assert U.log_divergence("all good\n", None) is None


def test_fallback_stays_inside_the_suite(tmp_path):
    cfg = make(tmp_path, adapter="vcs", tools='[tools]\nvcs = "svkit-no-such-vcs"\nverilator = "true"')
    rs = resolve_sim(cfg, "unit")
    # vcs missing: the unit flow compiles with Verilator; it never falls over to the system flow
    assert rs.name == "unit" and rs.label == "verilator" and rs.flow.testbench


def test_rundb_migrates_flow_column(tmp_path):
    d = tmp_path / ".svkit"
    d.mkdir()
    c = sqlite3.connect(d / "runs.sqlite")
    c.executescript("""CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('version', '1');
        CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, adapter TEXT NOT NULL, test TEXT,
          status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, finished_at REAL, duration_s REAL,
          git_sha TEXT, git_dirty INTEGER, source_hash TEXT, knobs TEXT NOT NULL DEFAULT '{}', experiment TEXT,
          cache_key TEXT, cached_from TEXT, exit_code INTEGER, metrics TEXT, artifacts TEXT NOT NULL DEFAULT '{}',
          group_id TEXT, message TEXT);
        INSERT INTO runs (id, kind, adapter, status, created_at) VALUES ('r_0001', 'sim', 'vcs', 'passed', 1);""")
    c.commit()
    c.close()
    db = RunDB(d)
    assert db.get_run("r_0001").flow is None
    r = db.create_run("sim", "verilator", test="acc", flow="unit")
    assert db.get_run(r.id).flow == "unit"
    RunDB(d)       # idempotent


# ---------------------------------------------------------------- svkit init
def test_init_writes_unit_suite(tmp_path):
    make(tmp_path)
    (tmp_path / "svkit.toml").unlink()
    (tmp_path / "Makefile").write_text("sim:\n\tverilator --binary tb/sys_test.sv rtl/acc.sv\n")
    text, notes = I.infer(tmp_path)
    assert "[flows.sim.unit]" in text and 'testbench = "tb/{test}_test.sv"' in text
    assert "pass_regex = '@@@ PASSED'" in text and "fail_regex = '@@@ FAILED'" in text
    tb_line = next(ln for ln in text.splitlines() if ln.startswith("tb_sources"))
    assert "acc_test.sv" not in tb_line and "sys_test.sv" in tb_line
    (tmp_path / "svkit.toml").write_text(text)
    cfg = C.load(tmp_path)
    assert cfg.project.tb_top == "sys_tb" and Runner(cfg).tests("unit") == ["acc"]


# ---------------------------------------------------------------- end to end
def _check_run(r, run, passed):
    assert run.flow == "unit" and run.test == "acc"
    assert run.status == ("passed" if passed else "failed"), (run.message, (r.db.run_dir(run.id) / "svkit.log").read_text()[-3000:])
    m = run.metrics
    assert m["waves"] in run.artifacts and m["waves"].startswith("waves.")
    from svkit.adapters.waves.wellen import find_vars, open_wave
    wf = open_wave(r.db.run_dir(run.id) / m["waves"])
    assert find_vars(wf, "q", under="acc_tb")                  # the whole testbench, dumped without editing it
    if not passed:
        d = m["divergence"]
        assert d["source"] == "log" and d["time"] is not None and "ERROR" in d["actual"]


@pytest.mark.oss
@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("verilator") and not __import__("svkit.compat", fromlist=["x"]).locate("verilator"),
                    reason="needs Verilator 5")
def test_unit_suite_on_verilator(tmp_path):
    cfg = make(tmp_path, bug=1)
    r = Runner(cfg)
    _, runs = r.sim_all(flow="unit", jobs=1, use_cache=False)
    _check_run(r, runs[0], passed=False)
    t_fail = runs[0].metrics["divergence"]["time"]
    assert t_fail == 20000                         # first check (second falling edge), in waveform ticks (ps)
    # fix the bug: the unit build is rebuilt and passes
    (tmp_path / "rtl" / "acc.sv").write_text("`define BUG 0\n" + ACC.replace("BUG", "`BUG"))
    _check_run(r, r.sim("acc", flow="unit"), passed=True)


@pytest.mark.synopsys
@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("vcs"), reason="needs VCS")
def test_unit_suite_on_vcs(tmp_path):
    cfg = make(tmp_path, bug=1, adapter="vcs")
    r = Runner(cfg)
    run = r.sim("acc", flow="unit", use_cache=False)
    assert run.adapter == "vcs"
    _check_run(r, run, passed=False)
