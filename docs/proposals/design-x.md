# Track 4 (Design + X) contract proposals

Status: proposed by Track 4, for the lead. Nothing here was applied to the shared contracts.

## 1. A 4-state simulator on the open-source flow (X-trace parity) — escalation

Verilator is 2-state, so it never produces X/Z. On the open-source flow the X tracer has nothing to trace,
so §4.6 cannot reach toolchain parity (Hard constraint 2) with Verilator alone. The xbug fixture goldens were
made with **iverilog** (`iverilog -g2012`, in OSS CAD Suite), which simulates all three fixtures correctly.

Proposal: add an `iverilog` builtin sim adapter (in OSS CAD Suite, no new install) used for X debugging on the
open-source flow, and add it to the MVP toolchain matrix for §4.6. Icarus is listed as post-MVP in MVP.md §7, so
this is a scope change for the owner. Until then the tracer runs on VCS waves (Synopsys flow) and on any 4-state VCD.

## 2. Document extra response fields (additive, no breaking change)

The endpoints return the documented shape plus these keys. I propose adding them to ARCHITECTURE.md §6:

* `GET /api/design/hierarchy`: `tree` (instance tree from the design top: `{name, path, module, children}`),
  `sim_roots`, `design_tops`, `diagnostics` (slang errors, at most 50).
* `GET /api/design/block`: `module`, `instance` (the representative instance path); each node has `kind`
  (`port|instance|logic`) and port nodes have `dir`; each edge has `id`, `src_port`, `dst_port`.
  Module-level glue logic (assign/always) is one `logic` node.
* `GET /api/design/cone`: `root_net`, `path_cells` (on-path node ids in path order), `slack`, `run`, `netlist`;
  nodes have `kind` (`cell|port|const`), `pins: {pin: {dir, nets}}`, and for on-path cells `delay` (output-pin incr),
  `wire_delay` (input-pin incr) and `arrival`; edges have `id`, `src_pin`, `dst_pin`;
  `unresolved: [{index, inst, pin, reason}]`.
* `GET /api/waves/{run}/decode` (via `svkit.design.graph.lookup_type`): enum keys are **decimal strings** of the value.
  Nested struct fields are flattened with dotted names; `msb`/`lsb` are absolute bit positions.

## 3. Netlist naming: the STA netlist must be the cone netlist

Yosys `write_json` keeps internal cell names (`$abc$163$...`), but `write_verilog` renames cells (`_19_`).
OpenSTA reads the Verilog netlist, so its paths only map onto the Verilog netlist. `routes_design` uses
`SynthResult.netlist` first, then prefers `.vg`/`.v` over `.json`. **Ask for Track 1:** set `SynthResult.netlist` to the
netlist file that STA read (`netlist.v`).

## 4. XTraceNode signal names

`XTraceNode.signal` holds the **waveform** name when the signal is in the waveform, so the UI can jump to it directly.
Otherwise it holds the elaborated design path. Memory words read as `mem[5]`. When nothing is dumped, `value` is `"?"`.
I propose stating this in ARCHITECTURE.md §3.
