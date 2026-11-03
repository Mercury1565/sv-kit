# fifo fixture (synchronous FIFO) -- a mini "user project" build usable by both flows.
#
#   make sim-verilator TEST=fill_drain   Verilator 5 (--binary --timing), outputs in output/
#   make syn-yosys                  Yosys + toy liberty, outputs in syn_out/
#   make sim-vcs TEST=fill_drain         VCS        (Synopsys flow)
#   make syn-dc                     dc_shell-t -f synopsys/syn.tcl, reports in syn_out/
#
# Cross-flow knob convention: the clock period (ps) lives in clock_period.txt; it is
# exported as CLOCK_PERIOD (ps) to every synthesis tool.

TOP      := fifo
TB_TOP   := fifo_tb
RTL      := rtl/fifo.sv
TB       := tb/fifo_tb.sv
TEST     ?= fill_drain
LIB      ?= ../lib/svkit_tiny.lib
DEFINES  ?=
CLOCK_PERIOD ?= $(shell cat clock_period.txt)
export CLOCK_PERIOD

VERILATOR ?= verilator
YOSYS     ?= yosys
VCS       ?= vcs
DC_SHELL  ?= dc_shell-t

.PHONY: sim-verilator syn-yosys sim-vcs syn-dc clean

# ---------------------------------------------------------------- open-source flow
build/verilator/Vsim: $(RTL) $(TB)
	mkdir -p build/verilator
	$(VERILATOR) --binary --timing -Wno-fatal --trace --top-module $(TB_TOP) \
	    --Mdir build/verilator -o Vsim $(DEFINES) $(RTL) $(TB)

sim-verilator: build/verilator/Vsim
	mkdir -p output
	./build/verilator/Vsim +TEST=$(TEST)

syn-yosys:
	mkdir -p syn_out
	$(YOSYS) -q -l syn_out/yosys.log -p "read_verilog -sv $(RTL); synth -top $(TOP); \
	    dfflibmap -liberty $(LIB); abc -liberty $(LIB) -D $(CLOCK_PERIOD); opt_clean; \
	    tee -q -o syn_out/$(TOP).stat.json stat -json -liberty $(LIB); \
	    write_verilog -noattr syn_out/$(TOP).yosys.v"

# ---------------------------------------------------------------- Synopsys flow
build/vcs/simv: $(RTL) $(TB)
	mkdir -p build/vcs
	$(VCS) -sverilog -full64 -timescale=1ns/1ps $(DEFINES) -Mdir=build/vcs/csrc -o build/vcs/simv $(RTL) $(TB)

sim-vcs: build/vcs/simv
	mkdir -p output
	./build/vcs/simv +TEST=$(TEST)

syn-dc:
	mkdir -p syn_out
	$(DC_SHELL) -f synopsys/syn.tcl | tee syn_out/$(TOP)-synth.out

# ---------------------------------------------------------------- helpers
clean:
	rm -rf build output syn_out
