# svkit fixture Design Compiler driver — generic template (fixtures/synopsys_common/syn_run.tcl).
# A fixture copies this file verbatim to synopsys/syn_run.tcl. `dc_shell-t -f synopsys/syn_run.tcl`
# sources the fixture's knob-editable script ($SVKIT_SYN_SCRIPT, default synopsys/syn.tcl), then
# compiles and writes one combined report $syn_out/<top>.rep plus the netlist $syn_out/<top>.vg.

if {[info exists ::env(SVKIT_SYN_SCRIPT)] && $::env(SVKIT_SYN_SCRIPT) ne ""} {
    source $::env(SVKIT_SYN_SCRIPT)
} else {
    source synopsys/syn.tcl
}

svkit_compile

set rep $syn_out/$top.rep
file delete -force $rep
foreach cmd {
    {report_design}
    {report_units}
    {report_qor}
    {report_timing -path full -delay_type max -max_paths 2}
    {report_timing -path full -delay_type min -max_paths 2}
    {report_constraints -all_violators}
    {report_power}
    {report_resources -hierarchy}
    {report_area -hierarchy}
    {report_reference -hierarchy}
} {
    if {[catch {redirect -append $rep $cmd} err]} { echo "Warning: $cmd failed: $err (SVKIT-10)" }
}
write -format verilog -hierarchy -output $syn_out/$top.vg
exit
