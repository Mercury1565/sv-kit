"""VcsAdapter tests: VCS log parser goldens + wrap/builtin runs with FAKE vcs/simv on PATH
(run everywhere; no Synopsys tools needed)."""
import json
import os
import stat
import time
from pathlib import Path

import pytest

from svkit import config as C
from svkit.adapters import registry
from svkit.adapters.sim.vcs import VcsAdapter, final_time_ps, parse_vcs_log
from svkit.adapters.waves import vpd as V
from svkit.core.runner import Runner

REPO = Path(__file__).resolve().parents[1]
VCS_SAMPLES = REPO / "fixtures" / "golden" / "synopsys" / "vcs"
REGEN = os.environ.get("SVKIT_REGEN_GOLDEN") == "1"


# ---------------------------------------------------------------- log parser goldens
@pytest.mark.parametrize("sample", sorted(VCS_SAMPLES.glob("*.log")), ids=lambda p: p.name)
def test_vcs_log_golden(sample):
    text = sample.read_text()
    msgs, failures = parse_vcs_log(text)
    got = {"messages": [m.model_dump(mode="json") for m in msgs], "failures": failures,
           "final_time_ps": final_time_ps(text)}
    exp = sample.with_suffix(".expected.json")
    if REGEN:
        exp.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert got == json.loads(exp.read_text())


def test_vcs_log_details():
    msgs, fails = parse_vcs_log((VCS_SAMPLES / "vcs_compile_errors.log").read_text())
    ind = next(m for m in msgs if m.code == "IND")
    assert ind.severity == "error" and (ind.file, ind.line) == ("rtl/fifo.sv", 41)
    assert "rd_ok" in ind.text and msgs[0].code == "IND"
    assert next(m for m in msgs if m.code == "TFIPC").text.endswith("(x2)")
    assert fails == ["VCS Error-[IND] Identifier not declared at rtl/fifo.sv:41"]
    msgs, fails = parse_vcs_log((VCS_SAMPLES / "vcs_runtime.log").read_text())
    assert any("a_no_overflow" in f for f in fails)
    assert any(f.startswith("$error at tb/fifo_tb.sv:70: data mismatch") for f in fails)
    assert final_time_ps("Time: 500000 ps") == 500000 and final_time_ps("Time: 3 ns") == 3000


# ---------------------------------------------------------------- fake tools
FAKE_VCS = r"""#!/bin/sh
echo "$*" >> "$FAKE_VCS_CALLS"
if [ "$1" = "-vpd2vcd" ]; then
  shift; [ "$1" = "+splitpacked" ] && shift
  cp "$1" "$2"; exit 0
fi
out=simv; log=compile.log
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift;;
    -l) log="$2"; shift;;
    +define+BROKEN) printf 'Error-[IND] Identifier not declared\nrtl/core.sv, 3\n  Identifier x not declared.\n' > "$log"; exit 1;;
  esac
  shift
done
cat > "$out" <<'EOF'
#!/bin/sh
test=""
for a in "$@"; do case "$a" in +TEST=*) test="${a#+TEST=}";; esac; done
[ "$test" = "$SVKIT_TEST" ] || { echo "SVKIT_TEST mismatch"; exit 3; }
printf '$timescale 1ps $end\n$scope module tb $end\n$var wire 1 ! clk $end\n$upscope $end\n$enddefinitions $end\n#0\n0!\n#5000\n1!\n' > dump.vcd
if [ "$test" = "bad" ]; then echo "result 2" > "out_$test.txt"; else echo "result 1" > "out_$test.txt"; fi
echo "Warning-[W1] fake runtime warning"
echo "Time: 5000 ps"
EOF
chmod +x "$out"
echo "compile ok" > "$log"
"""

# the "user's build" for wrap mode: writes a log, a VPD (VCD text; the fake vcs -vpd2vcd copies it)
# and the golden-compared output
USER_BUILD = r"""#!/bin/sh
t="$1"
mkdir -p out
[ "$t" = "nothing" ] && exit 0
printf '$timescale 1ns $end\n$enddefinitions $end\n#0\n#7\n' > "out/$t.vpd"
echo "running $t with SVKIT_TEST=$SVKIT_TEST defines=$SVKIT_VCS_DEFINES" > "out/$t.log"
case "$t" in
  bad)   echo "@@ 10 r1=5" > "out/$t.wb"; echo "@@ 20 r2=9" >> "out/$t.wb";;
  fatal) cp "golden/$t.wb" "out/$t.wb"; printf 'Fatal: "tb.sv", 12: tb: at time 5 ns\nboom\n' >> "out/$t.log";;
  *)     cp "golden/$t.wb" "out/$t.wb";;
esac
echo "Time: 7 ns"
"""

WRAP_TOML = r'''
[project]
name = "fake-vcs"
top = "core"
sources = { glob = ["rtl/*.sv"] }

[flows.sim.vcs]
cmd = "sh user_build.sh {test}"
log = "out/{test}.log"
waves = "out/{test}.vpd"
golden = { actual = "out/{test}.wb", expected = "golden/{test}.wb", time_regex = '@@ (\d+)' }
tests = { names = ["good", "bad", "fatal", "nothing"] }

[knobs]
clock_period = { define = "CLOCK_PERIOD", applies_to = ["sim"] }
'''


def _exe(p: Path, text: str) -> None:
    p.write_text(text)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fake_vcs(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    _exe(bindir / "vcs", FAKE_VCS)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_VCS_CALLS", str(tmp_path / "vcs_calls.txt"))
    monkeypatch.setenv("SVKIT_LOCK_DIR", str(tmp_path / "locks"))
    return tmp_path / "vcs_calls.txt"


@pytest.fixture
def wrap_project(tmp_path):
    root = tmp_path / "wproj"
    (root / "rtl").mkdir(parents=True)
    (root / "golden").mkdir()
    (root / "rtl" / "core.sv").write_text("module core; endmodule\n")
    for t in ("good", "bad", "fatal", "nothing"):
        (root / "golden" / f"{t}.wb").write_text("@@ 10 r1=5\n@@ 20 r2=6\n")
    _exe(root / "user_build.sh", USER_BUILD)
    (root / "svkit.toml").write_text(WRAP_TOML)
    return root


def test_registry(wrap_project, fake_vcs):
    ad = registry.get("vcs", C.load(wrap_project))
    assert isinstance(ad, VcsAdapter) and ad.available()


def test_wrap_pass_with_vpd(wrap_project, fake_vcs):
    run = Runner(C.load(wrap_project)).sim("good", use_cache=False)
    assert run.status == "passed", run.message
    m = run.metrics
    assert m["tool"] == "vcs" and m["passed"] is True and m["waves"] == "waves.vcd"
    assert m["sim_time"] == 7                                   # "Time: 7 ns" in 1 ns VCD ticks
    rd = wrap_project / ".svkit" / "runs" / run.id
    assert (rd / "waves.vcd").read_text().startswith("$timescale 1ns")
    assert {"sim.log", "sim.stdout.log", "waves.vcd", "good.wb"} <= set(run.artifacts)
    assert "+splitpacked" in fake_vcs.read_text()               # vcs -vpd2vcd +splitpacked used
    assert "SVKIT_TEST=good" in (rd / "sim.log").read_text()


def test_wrap_golden_mismatch(wrap_project, fake_vcs):
    run = Runner(C.load(wrap_project)).sim("bad", use_cache=False)
    assert run.status == "failed"
    d = run.metrics["divergence"]
    assert d["line"] == 2 and d["time"] == 20 and d["expected"] == "@@ 20 r2=6"


def test_wrap_fatal_fails_even_if_golden_matches(wrap_project, fake_vcs):
    run = Runner(C.load(wrap_project)).sim("fatal", use_cache=False)
    assert run.status == "failed" and run.message.startswith("$fatal at tb.sv:12")
    assert any(w["code"] == "$fatal" for w in run.metrics["warnings"])


def test_wrap_stale_outputs_are_not_trusted(wrap_project, fake_vcs):
    out = wrap_project / "out"
    out.mkdir()
    (out / "nothing.wb").write_text("@@ 10 r1=5\n@@ 20 r2=6\n")     # matches the golden...
    (out / "nothing.vpd").write_text("$timescale 1ns $end\n")
    old = time.time() - 3600
    for f in out.iterdir():
        os.utime(f, (old, old))                                    # ...but is from an earlier run
    run = Runner(C.load(wrap_project)).sim("nothing", use_cache=False)
    assert run.status == "failed" and "not produced" in run.message
    assert run.metrics["waves"] is None
    assert any(w["code"] == "WAVES" for w in run.metrics["warnings"])


def test_wrap_up_to_date_outputs_are_used(wrap_project, fake_vcs):
    # the build left last run's outputs alone (make: up to date) and they are newer than every source
    src_t, out_t = time.time() - 7200, time.time() - 3600
    os.utime(wrap_project / "rtl" / "core.sv", (src_t, src_t))
    out = wrap_project / "out"
    out.mkdir()
    (out / "nothing.wb").write_text("@@ 10 r1=5\n@@ 20 r2=6\n")
    (out / "nothing.vpd").write_text("$timescale 1ns $end\n$enddefinitions $end\n#0\n")
    for f in out.iterdir():
        os.utime(f, (out_t, out_t))
    run = Runner(C.load(wrap_project)).sim("nothing", use_cache=False)
    assert run.status == "passed", run.message
    assert run.metrics["waves"] == "waves.vcd"
    assert sum(w["code"] == "UPTODATE" for w in run.metrics["warnings"]) == 2


def test_wrap_define_knob_exported(wrap_project, fake_vcs):
    run = Runner(C.load(wrap_project)).sim("good", sets={"clock_period": 4000}, use_cache=False)
    log = (wrap_project / ".svkit" / "runs" / run.id / "sim.log").read_text()
    assert "defines=+define+CLOCK_PERIOD=4000" in log


def test_vpd_without_converter(wrap_project, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")                    # no vcs, no vpd2vcd
    monkeypatch.setenv("SVKIT_LOCK_DIR", str(tmp_path / "locks"))
    cfg = C.load(wrap_project)
    cfg.flows.fallback = False       # this test is about the missing converter, not the vcs fallback
    assert V.vpd_converter(cfg) is None
    run = Runner(cfg).sim("good", use_cache=False)
    assert run.status == "passed" and run.metrics["waves"] is None
    assert any("cannot convert" in w["text"] for w in run.metrics["warnings"])


# ---------------------------------------------------------------- builtin
BUILTIN_TOML = r'''
[project]
name = "fake-vcs-builtin"
top = "core"
tb_top = "tb"
sources = { glob = ["rtl/*.sv"] }
tb_sources = { glob = ["tb/*.sv"] }
include = ["inc"]
defines = { WIDTH = 8 }

[flows.sim.vcs]
mode = "builtin"
golden = { actual = "out_{test}.txt", expected = "golden/{test}.txt" }
tests = { names = ["t1", "t2", "bad"] }
args = ["-assert", "svaext", "--", "+verbose"]
'''


@pytest.fixture
def builtin_project(tmp_path):
    root = tmp_path / "bproj"
    for d in ("rtl", "tb", "inc", "golden"):
        (root / d).mkdir(parents=True)
    (root / "rtl" / "core.sv").write_text("module core; endmodule\n")
    (root / "tb" / "tb.sv").write_text("module tb; core u(); endmodule\n")
    for t in ("t1", "t2", "bad"):
        (root / "golden" / f"{t}.txt").write_text("result 1\n")
    (root / "svkit.toml").write_text(BUILTIN_TOML)
    return root


def test_builtin_compiles_once(builtin_project, fake_vcs):
    r = Runner(C.load(builtin_project))
    gid, runs = r.sim_all(jobs=3, use_cache=False)
    assert [x.status for x in runs] == ["passed", "passed", "failed"]
    calls = fake_vcs.read_text().splitlines()
    assert len(calls) == 1                                       # one compile for three tests
    c = calls[0]
    for s in ("-sverilog", "-full64", "-timescale=1ns/1ps", "-debug_access+all", "+vcs+lic+wait",
              "-top tb", "+define+WIDTH=8", "-assert svaext", "-o simv"):
        assert s in c, s
    assert "+incdir+" in c and "core.sv" in c and "tb.sv" in c and "+verbose" not in c
    m = runs[0].metrics
    assert m["waves"] == "waves.vcd" and m["sim_time"] == 5000
    assert any(w["code"] == "W1" for w in m["warnings"])
    assert "compile.log" in runs[0].artifacts
    assert len(list((builtin_project / ".svkit" / "build").glob("vcs-*/simv"))) == 1


def test_builtin_compile_error(builtin_project, fake_vcs):
    text = (builtin_project / "svkit.toml").read_text().replace('args = ["-assert", "svaext", "--", "+verbose"]',
                                                                'args = ["+define+BROKEN"]')
    (builtin_project / "svkit.toml").write_text(text)
    run = Runner(C.load(builtin_project)).sim("t1", use_cache=False)
    assert run.status == "failed" and run.message.startswith("VCS compile failed")
    ind = next(w for w in run.metrics["warnings"] if w["code"] == "IND")
    assert (ind["file"], ind["line"]) == ("rtl/core.sv", 3)
    assert not list((builtin_project / ".svkit" / "build").glob("vcs-*/simv"))


def test_concurrent_runs_of_one_test_do_not_race(tmp_path, fake_vcs):
    # a wrap command that, like `make t.out` + VPD conversion, writes then deletes a shared file;
    # two overlapping runs of the same test used to delete it under each other (rm: exit 2)
    import threading
    root = tmp_path / "race"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "core.sv").write_text("module core; endmodule\n")
    _exe(root / "run.sh", '#!/bin/sh\nmkdir -p out\necho x > "out/$1.vpd"\nsleep 0.5\nrm "out/$1.vpd"\n')
    (root / "svkit.toml").write_text('[project]\nname = "r"\ntop = "core"\nsources = { glob = ["rtl/*.sv"] }\n'
                                     '[flows.sim.vcs]\ncmd = "sh run.sh {test}"\ntests = { names = ["t"] }\n')
    cfg = C.load(root)
    runs = []
    ts = [threading.Thread(target=lambda: runs.append(Runner(cfg).sim("t", use_cache=False))) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert [r.status for r in runs] == ["passed", "passed"], [r.message for r in runs]
