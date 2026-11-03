# svkit fixture Makefile snippet: Synopsys targets (VCS simulation, Design Compiler synthesis).
# Generic template (fixtures/synopsys_common/vcs.mk) — see README.md next to it.
#
# A fixture Makefile sets these, then includes a verbatim copy of this file
# (`include synopsys/vcs.mk`):
#   TOP         synthesis top module                       (required)
#   TB_TOP      simulation top (testbench module)          (default: $(TOP))
#   SOURCES     RTL files                                  (required)
#   TB_SOURCES  testbench files
#   INCDIRS     include directories
#   DEFINES     NAME or NAME=VALUE macros
#   CLOCK_PORT  clock port of $(TOP)                       (default: clock)
#
# Targets:
#   make sim-vcs TEST=<name>   compile (once) with VCS and run simv +TEST=<name>
#   make syn-dc                synthesize with DC -> $(SYN_OUT)/$(TOP).rep, .vg, .log
#   make clean-synopsys

VCS        ?= vcs
DC         ?= dc_shell-t
TB_TOP     ?= $(TOP)
CLOCK_PORT ?= clock
TEST       ?= default
VCS_OUT    ?= vcs_out
SYN_OUT    ?= syn_out
SYN_SCRIPT ?= synopsys/syn.tcl
SYN_RUN    ?= synopsys/syn_run.tcl
VCS_FLAGS  ?= -sverilog -full64 -timescale=1ns/1ps -debug_access+all +vcs+lic+wait
SIMV_ARGS  ?=

# SVKIT_VCS_DEFINES is exported by svkit's VCS adapter for `define` knobs (e.g. +define+CLOCK_PERIOD=4000)
_VCS_DEFS  = $(addprefix +define+,$(DEFINES)) $(SVKIT_VCS_DEFINES)
_VCS_INCS  = $(addprefix +incdir+,$(abspath $(INCDIRS)))

.PHONY: sim-vcs syn-dc clean-synopsys

$(VCS_OUT)/simv: $(SOURCES) $(TB_SOURCES)
	mkdir -p $(VCS_OUT)
	cd $(VCS_OUT) && $(VCS) $(VCS_FLAGS) -top $(TB_TOP) $(_VCS_INCS) $(_VCS_DEFS) \
	    $(abspath $(SOURCES) $(TB_SOURCES)) -o simv -Mdir=csrc -l compile.log

sim-vcs: $(VCS_OUT)/simv
	./$(VCS_OUT)/simv +TEST=$(TEST) +vcs+lic+wait $(SIMV_ARGS) -l $(VCS_OUT)/$(TEST).log

# `tee` keeps the log streaming to svkit; `test -s` turns "no netlist" into a failing exit code.
syn-dc:
	mkdir -p $(SYN_OUT)
	SVKIT_TOP="$(TOP)" SVKIT_SOURCES="$(abspath $(SOURCES))" SVKIT_INCDIRS="$(abspath $(INCDIRS))" \
	SVKIT_DEFINES="$(DEFINES)" SVKIT_CLOCK_PORT="$(CLOCK_PORT)" SVKIT_SYN_OUT="$(SYN_OUT)" \
	SVKIT_SYN_SCRIPT="$(SYN_SCRIPT)" \
	    $(DC) -f $(SYN_RUN) 2>&1 | tee $(SYN_OUT)/$(TOP).log
	test -s $(SYN_OUT)/$(TOP).vg

clean-synopsys:
	rm -rf $(VCS_OUT) $(SYN_OUT) csrc simv simv.daidir ucli.key vc_hdrs.h *.vpd DVEfiles
