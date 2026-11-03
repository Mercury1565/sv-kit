# svkit fixture Design Compiler script — generic template (fixtures/synopsys_common/syn_dc.tcl).
#
# A fixture copies this file VERBATIM to synopsys/syn.tcl (its `flow.script`, the file svkit's
# TCL knobs edit in the work dir). Nothing design- or library-specific lives here: the fixture
# Makefile (synopsys_common/vcs.mk) passes the design through the environment, and libraries come
# from the user's environment, so no library path is ever committed.
#
#   SVKIT_TOP           top module                       (required)
#   SVKIT_SOURCES       space-separated RTL files        (required)
#   SVKIT_INCDIRS       space-separated include dirs
#   SVKIT_DEFINES       space-separated NAME or NAME=VALUE
#   SVKIT_CLOCK_PORT    clock port                       (default: clock)
#   SVKIT_CLOCK_FILE    file holding the clock period in ps (default: clock_period.txt)
#   SVKIT_SYN_OUT       output dir                       (default: syn_out)
#   SVKIT_DC_TIME_UNIT  library time unit, ns or ps      (default: ns)
#   Libraries, one of:
#     SVKIT_DC_TARGET_LIB[_<corner>] (+ SVKIT_DC_LINK_LIB, SVKIT_DC_SEARCH_PATH)   .db files
#     SVKIT_DC_LIBERTY    a Liberty .lib converted here with read_lib/write_lib (needs Library Compiler)
#
# This script sets up and constrains the design; synopsys/syn_run.tcl sources it and then calls
# `svkit_compile` and writes the reports, so TCL appended to this file (e.g. an experiment's
# `tcl_append = "set_max_fanout 8 [current_design]"`) still runs before compile.

proc svkit_env {name default} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $default
}

# ---------------------------------------------------------------- knob-overridable settings
if {![info exists corner]} {set corner TT}
set top          [svkit_env SVKIT_TOP ""]
set sources      [svkit_env SVKIT_SOURCES ""]
set incdirs      [svkit_env SVKIT_INCDIRS ""]
set defines      [svkit_env SVKIT_DEFINES ""]
set clock_port   [svkit_env SVKIT_CLOCK_PORT clock]
set syn_out      [svkit_env SVKIT_SYN_OUT syn_out]
set time_unit    [svkit_env SVKIT_DC_TIME_UNIT ns]
set clock_file   [svkit_env SVKIT_CLOCK_FILE clock_period.txt]
if {$top eq "" || $sources eq ""} {
    echo "Error: SVKIT_TOP and SVKIT_SOURCES must be set (see synopsys_common/vcs.mk). (SVKIT-1)"
    exit 1
}

# clock period: the file holds ps (svkit's `clock_period` knob writes it); convert to library units
if {![info exists clock_period_ps]} {
    if {[file exists $clock_file]} {
        set fh [open $clock_file r]; set clock_period_ps [string trim [read $fh]]; close $fh
    } else {
        set clock_period_ps 10000
    }
}
switch -- $time_unit {
    ps      { set clock_period $clock_period_ps }
    ns      { set clock_period [expr {$clock_period_ps / 1000.0}] }
    default { echo "Error: SVKIT_DC_TIME_UNIT must be ns or ps. (SVKIT-2)"; exit 1 }
}

# ---------------------------------------------------------------- libraries (from the environment)
file mkdir $syn_out
if {[info exists ::env(SVKIT_DC_TARGET_LIB_$corner)]} {
    set target_library $::env(SVKIT_DC_TARGET_LIB_$corner)
} elseif {[info exists ::env(SVKIT_DC_TARGET_LIB)]} {
    set target_library $::env(SVKIT_DC_TARGET_LIB)
} elseif {[info exists ::env(SVKIT_DC_LIBERTY)]} {
    read_lib $::env(SVKIT_DC_LIBERTY)
    set svkit_libname [get_object_name [index_collection [get_libs] 0]]
    write_lib $svkit_libname -format db -output $syn_out/$svkit_libname.db
    set target_library $syn_out/$svkit_libname.db
} else {
    echo "Error: no library: set SVKIT_DC_TARGET_LIB (.db) or SVKIT_DC_LIBERTY (.lib). (SVKIT-3)"
    exit 1
}
if {[info exists ::env(SVKIT_DC_SEARCH_PATH)]} {
    set search_path [concat $search_path $::env(SVKIT_DC_SEARCH_PATH)]
}
set search_path [concat $search_path $incdirs]
if {[info exists ::env(SVKIT_DC_LINK_LIB)]} {
    set link_library [concat * $::env(SVKIT_DC_LINK_LIB)]
} else {
    set link_library [concat * $target_library]
}

# ---------------------------------------------------------------- read, elaborate, constrain
define_design_lib WORK -path $syn_out/work
if {$defines ne ""} {
    analyze -format sverilog -define $defines $sources
} else {
    analyze -format sverilog $sources
}
elaborate $top
current_design $top
link
uniquify

create_clock -name $clock_port -period $clock_period [get_ports $clock_port]
set_input_delay 0 -clock $clock_port [remove_from_collection [all_inputs] [get_ports $clock_port]]
set_output_delay 0 -clock $clock_port [all_outputs]

# ---------------------------------------------------------------- compile (called by syn_run.tcl)
# Kept as a top-level line so a `tcl_replace = '^compile\b.*$'` knob can swap in compile_ultra.
proc svkit_compile {} {
compile -map_effort medium
}
