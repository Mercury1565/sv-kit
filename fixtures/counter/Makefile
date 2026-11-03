# counter fixture (parameterized up-counter) -- a mini "user project" build usable by both flows.
#
#   make sim-verilator TEST=basic   Verilator 5 (--binary --timing), outputs in output/
#   make syn-yosys                  Yosys + toy liberty, outputs in syn_out/
#   make sim-vcs TEST=basic         VCS            (synopsys/vcs.mk)
#   make syn-dc                     Design Compiler (synopsys/vcs.mk -> synopsys/syn_run.tcl)
#
# Cross-flow knob convention: the clock period (ps) lives in clock_period.txt; both synthesis
# flows read it (exported as CLOCK_PERIOD for convenience).

TOP        = counter
TB_TOP     = counter_tb
SOURCES    = rtl/counter.sv
TB_SOURCES = tb/counter_tb.sv
CLOCK_PORT = clock
DEFINES   ?=
TEST      ?= basic
# svkit wrap runs export SVKIT_LIBERTY (corner-selected .lib files, absolute paths)
LIB       ?= $(if $(SVKIT_LIBERTY),$(firstword $(SVKIT_LIBERTY)),../lib/svkit_tiny.lib)
CLOCK_PERIOD ?= $(shell cat clock_period.txt)
export CLOCK_PERIOD

VERILATOR ?= verilator
YOSYS     ?= yosys

.PHONY: sim-verilator syn-yosys clean

# ---------------------------------------------------------------- open-source flow
build/verilator/Vsim: $(SOURCES) $(TB_SOURCES)
	mkdir -p build/verilator
	$(VERILATOR) --binary --timing -Wno-fatal --trace --top-module $(TB_TOP) \
	    --Mdir build/verilator -o Vsim $(addprefix +define+,$(DEFINES)) $(SOURCES) $(TB_SOURCES)

sim-verilator: build/verilator/Vsim
	mkdir -p output
	./build/verilator/Vsim +TEST=$(TEST)

syn-yosys:
	mkdir -p syn_out
	$(YOSYS) -q -l syn_out/yosys.log -p "read_verilog -sv $(SOURCES); synth -top $(TOP); \
	    dfflibmap -liberty $(LIB); abc -liberty $(LIB) -D $(CLOCK_PERIOD); opt_clean; \
	    tee -q -o syn_out/$(TOP).stat.json stat -json -liberty $(LIB); \
	    write_verilog -noattr syn_out/$(TOP).yosys.v"

# ---------------------------------------------------------------- Synopsys flow (verbatim template copy)
include synopsys/vcs.mk

clean: clean-synopsys
	rm -rf build output syn_out
