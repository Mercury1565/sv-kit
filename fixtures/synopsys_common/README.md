# Synopsys flow templates for fixtures

Generic, design-agnostic scripts that give every fixture a VCS + Design Compiler flow
that svkit **wraps** (the fixture is a mini "user project"; svkit never generates its build).

| File | Copy into the fixture as | Purpose |
|---|---|---|
| `vcs.mk` | `synopsys/vcs.mk` | `sim-vcs` and `syn-dc` make targets |
| `syn_dc.tcl` | `synopsys/syn.tcl` | DC setup + constraints; the file svkit's TCL knobs edit (`flow.script`) |
| `syn_run.tcl` | `synopsys/syn_run.tcl` | sources `syn.tcl`, compiles, writes the combined report and netlist |

**Copy, don't reference.** Synthesis runs in an isolated copy of the fixture directory
(`.svkit/work/<run>/`), so `../synopsys_common/...` does not exist there. Copy the three files
verbatim; `tests/test_dc_templates.py` checks that fixture copies stay identical to these
templates.

## Fixture Makefile

```make
TOP        = counter
TB_TOP     = counter_tb
SOURCES    = rtl/counter.sv
TB_SOURCES = tb/counter_tb.sv
CLOCK_PORT = clock
include synopsys/vcs.mk
```

The testbench should read `+TEST=<name>` (`$value$plusargs("TEST=%s", test)`), write its golden
output and a VCD (`$dumpfile` / `$dumpvars`) relative to the fixture root.

## Fixture `svkit.toml`

```toml
[flows.sim.vcs]
cmd   = "make sim-vcs TEST={test}"
log   = "vcs_out/{test}.log"
waves = "<what the TB dumps, e.g. {test}.vcd>"

[flows.synth.dc]
cmd     = "make syn-dc"
script  = "synopsys/syn.tcl"
reports = ["syn_out/{top}.rep"]
netlist = "syn_out/{top}.vg"
log     = "syn_out/{top}.log"
license_seats = 2

[knobs]
clock_period = { file = "clock_period.txt", unit = "ps", default = 4000 }
corner       = { tcl_var = "corner" }
compile_cmd  = { tcl_replace = '^compile\b.*$', with = "{value}" }   # e.g. "compile_ultra"
```

## Libraries (never committed)

Set in your shell (or `[flows.synth.dc].env` in a private, uncommitted config):

* `SVKIT_DC_TARGET_LIB="/abs/path/lib_tt.db"` — target `.db` library list;
  `SVKIT_DC_TARGET_LIB_<corner>` overrides it for the `corner` knob (e.g. `SVKIT_DC_TARGET_LIB_SS`);
  optional `SVKIT_DC_LINK_LIB`, `SVKIT_DC_SEARCH_PATH`.
* or `SVKIT_DC_LIBERTY=/abs/path/fixtures/lib/svkit_tiny.lib` — the original open toy library,
  converted to `.db` at synthesis time with `read_lib` / `write_lib` (needs a Library Compiler
  license).
* `SVKIT_DC_TIME_UNIT=ps` when the library's time unit is ps (default ns); svkit's parser
  also detects the unit from `report_units`.

## Synopsys tests

`pytest -m synopsys` (on a host with `vcs` and `dc_shell-t`) runs the counter / pipe5 fixtures
end to end under VCS + DC. The DC tests skip unless one of the library variables above is set.
