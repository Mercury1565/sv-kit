# Minimal user-style Makefile for the xbug_mem fixture (4-state sims only).
RTL := $(wildcard rtl/*.sv)
TB  := $(wildcard tb/*.sv)
TOP := tb_xm
TEST ?= smoke

sim-vcs:
	vcs -sverilog -full64 -debug_access+all -top $(TOP) $(RTL) $(TB) -o simv && ./simv | tee sim.log

sim-iverilog:
	iverilog -g2012 -s $(TOP) -o sim.vvp $(RTL) $(TB) && vvp -n sim.vvp | tee sim.log

clean:
	rm -rf simv simv.daidir csrc sim.vvp sim.log waves.vcd ucli.key

.PHONY: sim-vcs sim-iverilog clean
