"""`svkit init`: scans a project and writes a loadable best-guess svkit.toml."""
import textwrap

import pytest

from svkit import config as C
from svkit import init as I


@pytest.mark.parametrize("fx,top,tb,tests", [
    ("counter", "counter", "counter_tb", {"names": ["basic", "clear", "enable"]}),
    ("fifo", "fifo", "fifo_tb", {"names": ["fill_drain", "interleave", "random"]}),
    ("latch", "latch_hold", "latch_tb", {"names": ["basic"]}),
    ("pipe5", "p5_core", "p5_tb", {"glob": "programs/*.mem"}),
])
def test_init_on_fixtures(fixture_copy, fx, top, tb, tests):
    root = fixture_copy(fx)
    (root / "svkit.toml").unlink()
    out, notes = I.init(root)
    cfg = C.load(out)                      # must validate as written
    assert not any("WARNING" in n for n in notes), notes
    assert (cfg.project.top, cfg.project.tb_top) == (top, tb)
    assert cfg.project.clock == "clock" and cfg.project.reset.name == "reset"
    assert set(cfg.flows.sim) == {"vcs", "verilator"} and set(cfg.flows.synth) == {"dc", "yosys"}
    v = cfg.flows.sim["verilator"]
    assert v.mode == "wrap" and v.cmd == "make sim-verilator TEST={test}"
    assert cfg.flows.sim["vcs"].cmd == "make sim-vcs TEST={test}"
    assert v.golden is not None and v.golden.expected.startswith("expected/{test}")
    assert v.golden.time_regex == r"^@(\d+)" and v.golden.time_unit == "cycle"
    t = v.tests
    assert (t.glob == tests.get("glob")) and (t.names == tests.get("names", []))
    dc = cfg.flows.synth["dc"]
    assert dc.cmd == "make syn-dc" and dc.script == "synopsys/syn.tcl"
    assert cfg.flows.synth["yosys"].sta == "opensta"
    assert cfg.knobs["clock_period"].file == "clock_period.txt" and cfg.knobs["clock_period"].default == 2000
    assert cfg.knobs["corner"].tcl_var == "corner"
    assert cfg.library.liberty == ["../lib/svkit_tiny.lib"]
    if fx == "pipe5":
        assert cfg.perf.source is not None
    # never overwrite without force
    with pytest.raises(FileExistsError):
        I.init(root)
    I.init(root, force=True)


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))


def test_init_on_makefile_style_project(tmp_path):
    """A synthetic project shaped like a typical course pipeline repo (invented names): VCS sim via
    %.out pattern rules, a %.check golden diff, DC synthesis via %.vg, a clock-period file + env var,
    a DC script with a corner variable, test programs and a correct-output dir."""
    root = tmp_path / "proj"
    _w(root / "Makefile", """\
        CLOCK_PERIOD = $(shell cat core.period)
        export CLOCK_PERIOD
        HEADERS = defs.svh
        SOURCES = verilog/zcore.sv verilog/zstage_a.sv verilog/zstage_b.sv
        TESTBENCH = test/zcore_test.sv
        build/simv: $(SOURCES) $(TESTBENCH)
        \tvcs -sverilog -xprop=tmerge +define+CLOCK_PERIOD=$(CLOCK_PERIOD) $^ -o $@
        %.out: programs/%.mem build/simv
        \t./build/simv +MEMORY=$< | tee output/$*.out
        %.check: %.out
        \tdiff output/$*.wb correct_out/$*.wb
        synth/%.vg: $(SOURCES)
        \tcd synth && MODULE=$* dc_shell-t -f ../zsynth.tcl | tee $*-synth.out
        %.cpi: %.out
        clean:
        \trm -rf build output synth/*.vg
        """)
    (root / "core.period").write_text("25000\n")
    _w(root / "defs.svh", "`define WIDTH 16\n")
    _w(root / "verilog" / "zcore.sv", """\
        module zcore(input logic clk, input logic rst_n, output logic [7:0] q);
          zstage_a sa (.clk(clk), .d(q), .q());
          zstage_b #(.N(2)) sb (.clk(clk), .q(q));
        endmodule
        """)
    _w(root / "verilog" / "zstage_a.sv", "module zstage_a(input logic clk, input logic [7:0] d, output logic [7:0] q);\n"
                                         "  always_ff @(posedge clk) q <= d; endmodule\n")
    _w(root / "verilog" / "zstage_b.sv", "module zstage_b #(parameter N=1)(input logic clk, output logic [7:0] q);\n"
                                         "  always_ff @(posedge clk) q <= q + N; endmodule\n")
    _w(root / "test" / "zcore_test.sv", """\
        module zcore_test;
          logic clk, rst_n; logic [7:0] q;
          zcore dut (.clk(clk), .rst_n(rst_n), .q(q));
          initial begin $dumpfile("output/x.vcd"); $finish; end
        endmodule
        """)
    _w(root / "zsynth.tcl", """\
        set corner TT
        set clock_period $env(CLOCK_PERIOD)
        read_file -format sverilog $sources
        link
        compile -map_effort high -area_effort none
        # set_max_fanout 8 [current_design]
        report_timing > synth/$design_name.rep
        write -format verilog -hierarchy -output synth/$design_name.vg
        """)
    for t in ("alpha", "beta", "gamma"):
        _w(root / "programs" / f"{t}.mem", "0000\n")
        _w(root / "programs" / f"{t}.src", "nop\n")
        _w(root / "correct_out" / f"{t}.wb", f"PC=0 r1={t}\n")
    _w(root / "output" / "alpha.cpi", "@@  Total cycles: 120   Total instructions: 100\n")

    out, notes = I.init(root)
    text = out.read_text()
    cfg = C.load(out)
    assert cfg.project.top == "zcore" and cfg.project.tb_top == "zcore_test"
    assert cfg.project.clock == "clk"
    assert (cfg.project.reset.name, cfg.project.reset.active) == ("rst_n", "low")
    assert cfg.project.sources.glob == ["verilog/*.sv"] and cfg.project.tb_sources.glob == ["test/zcore_test.sv"]
    assert cfg.project.include == ["."]
    assert list(cfg.flows.sim) == ["vcs"]
    vcs = cfg.flows.sim["vcs"]
    assert vcs.cmd == "make {test}.out"
    assert vcs.golden.actual == "output/{test}.wb" and vcs.golden.expected == "correct_out/{test}.wb"
    assert vcs.tests.glob == "programs/*.mem"
    dc = cfg.flows.synth["dc"]
    assert dc.cmd == "make synth/{top}.vg" and dc.script == "zsynth.tcl"
    assert dc.reports == ["synth/{top}.rep"] and dc.netlist == "synth/{top}.vg"
    k = cfg.knobs["clock_period"]
    assert (k.file, k.unit, k.default) == ("core.period", "ps", 25000)
    assert cfg.knobs["corner"].tcl_var == "corner" and cfg.knobs["corner"].default == "TT"
    assert "compile_cmd" in text and "max_fanout" in text           # commented suggestions
    assert cfg.perf.source.regex_file == "output/{test}.cpi"
    import re
    assert re.search(cfg.perf.source.cycles, "@@  Total cycles: 120").group(1) == "120"
    assert re.search(cfg.perf.source.instrs, "Total instructions: 100").group(1) == "100"
    assert "TODO" in text
    assert "zcore" in notes[0]


def test_init_empty_project(tmp_path):
    out, notes = I.init(tmp_path)
    assert "TODO" in out.read_text()
    assert any("no HDL" in n for n in notes)


def test_init_generated_goldens_and_dc_details(tmp_path):
    """Shaped like a real course repo on a clean checkout (invented names): the golden dir does not
    exist yet (a make rule generates it), `%.simv` builds the simulator while `./%.out` runs a test,
    waves come from a `%.vcd: %.vpd` rule, a comment mentions `make core.vg`, DC runs after
    `cd synth` and tees its log, and the target library glob uses the corner variable."""
    libdir = tmp_path / "libs"
    for c in ("TT", "SS"):
        _w(libdir / f"zz7_RVT_{c}_x.db", "")
    root = tmp_path / "proj"
    _w(root / "Makefile", f"""\
        # make core.vg        <- synthesizes the core
        SOURCES = $(wildcard verilog/*.sv)
        build/simv: $(SOURCES) test/core_test.sv
        \tvcs -sverilog -xprop=tmerge $^ -o $@
        build/syn.simv: synth/core.vg test/core_test.sv
        \tvcs -sverilog $^ -o $@
        %.simv: build/%.simv ;
        synth/%.vg: verilog/%.sv zsynth.tcl
        \tcd synth && MODULE=$* dc_shell-t -f ../zsynth.tcl | tee $*-synth.out
        %.vg: synth/%.vg ;
        output/%.fmem: programs/build/%.mem build/simv
        \tbuild/simv +MEMORY=./$< +OUTPUT=./output/$*
        golden_out/%.fmem: programs/build/%.mem
        \tprograms/refsim --memory $< --outdir golden_out
        %.cpi %.wb %.out %.vpd: %.fmem ;
        %.vcd: %.vpd
        \tvcs -vpd2vcd +splitpacked $< $@
        ./%.out: output/%.vcd ;
        %.check: output/%.wb golden_out/%.wb
        \tdiff $^
        %.verdi: build/simv
        \tcd build && ./simv -gui=verdi
        """)
    _w(root / "core.clock", "30000\n")
    _w(root / "verilog" / "core.sv", "module core(input logic clock, input logic reset, output logic q);\n"
                                     "  always_ff @(posedge clock) q <= reset; endmodule\n")
    _w(root / "test" / "core_test.sv", """\
        module core_test; logic clock, reset, q; int f, c, i;
          core dut(.clock(clock), .reset(reset), .q(q));
          initial begin
            f = $fopen("out.cpi");
            $display("  %16t : %d cycles", $realtime, c);
            $fdisplay(f, "@@@  %0d cycles / %0d instrs = %f CPI", c, i, real'(c) / i);
          end
        endmodule
        """)
    _w(root / "zsynth.tcl", f"""\
        set corner TT
        set target_library [glob {libdir}/*_RVT_${{corner}}_*.db ]
        read_file -format sverilog $sources
        link
        set_driving_cell -lib_cell INVx1_ZZ7_R -pin Y [all_inputs]
        compile -map_effort high
        report_timing > $design_name.rep
        write -format verilog -hierarchy -output $design_name.vg
        """)
    for t in ("alpha", "beta"):
        _w(root / "programs" / f"{t}.s", "nop\n")

    out, notes = I.init(root)
    text = out.read_text()
    cfg = C.load(out)
    vcs = cfg.flows.sim["vcs"]
    # running a test also builds its golden (the golden dir is generated by make)
    assert vcs.cmd == "make {test}.out golden_out/{test}.wb"
    assert vcs.build_cmd == "make build/simv"
    assert vcs.golden.actual == "output/{test}.wb" and vcs.golden.expected == "golden_out/{test}.wb"
    assert vcs.waves == "output/{test}.vcd"
    assert vcs.tests.glob == "programs/*.s"
    dc = cfg.flows.synth["dc"]
    assert dc.cmd == "make synth/{top}.vg" and dc.netlist == "synth/{top}.vg"
    assert dc.log == "synth/{top}-synth.out"
    assert cfg.perf.source.regex_file == "output/{test}.cpi"
    import re
    line = "@@@  133 cycles / 147 instrs = 0.904762 CPI"     # learned from the $fdisplay format
    assert re.search(cfg.perf.source.cycles, line).group(1) == "133"
    assert re.search(cfg.perf.source.instrs, line).group(1) == "147"
    assert cfg.library.vt_suffix == {"_R": "RVT", "_L": "LVT", "_SL": "SLVT"}
    assert cfg.experiments["ss_corner"] == {"corner": "SS"}
    assert any("tmerge" in n for n in notes)
