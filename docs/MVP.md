# svkit MVP scope

> **Status:** Draft v0.1 (2026-11-02). Drawn from [`PRODUCT.md`](../PRODUCT.md) §6 and §10, plus the scope decisions logged in its [§13](../PRODUCT.md#13-decision-log).
> This file says **what v1 must do, what it leaves out, and how we know it is done**. For *how* each feature works, follow the `PRODUCT.md §x` links.

---

## 1. Goal and definition of done

The MVP covers the four core pain points from the decision log:
- the X-propagation tracer
- the synthesis dashboard and compare
- the clock-period sweep
- waveforms with CPI/TPI

Block and gate diagrams are included because the gate view hosts the critical-path highlight, and the block view reuses the same DesignGraph as the tracer.

**The MVP is done when** the loop below works end to end on the fixture corpus under **both** flows (VCS + DC and Verilator + Yosys + OpenSTA) **and** on the dogfood project (`/home/hermong/eecs470/470_proj-f26.group4`):

1. `svkit init` writes a `svkit.toml` that needs at most minor hand edits.
2. `svkit sim --all` records one Run per test with pass/fail against golden.
3. `svkit synth --matrix` runs every named experiment and opens a compare table with correct deltas.
4. `svkit sweep clock` finds the known T_min on the fixtures.
5. `svkit serve`:
   - a failing test opens the waveform at its first divergence
   - CPI and TPI are shown per program
6. `svkit xtrace` (CLI and UI) names the root cause of every planted X bug in the fixtures, within the supported root-cause classes (§4.6).

## 2. Supported toolchains

| Category | Synopsys flow | Open-source flow |
|---|---|---|
| Simulator | VCS | Verilator |
| Waveform input | VCD, including VPD converted with `vcs -vpd2vcd` | VCD, FST |
| Synthesis | Design Compiler | Yosys |
| Timing | DC `report_timing` / `report_constraints` | OpenSTA on the Yosys netlist |
| RTL front end | pyslang (shared) | pyslang (shared) |

**Post-MVP:** Icarus, native FSDB, lint adapters (slang, Verilator `--lint-only`, Verible).

## 3. Foundation (Phase 0)

Every feature depends on these. See PRODUCT.md [§5](../PRODUCT.md#5-core-concepts) and [§9](../PRODUCT.md#9-architecture).

| Piece | MVP content |
|---|---|
| Config | `svkit.toml` loader and validation for `[project]`, `[flows]`, `[knobs]`, `[experiments]`, `[perf]` and `[library]` |
| `svkit init` | Scans for Makefile targets, `*.f` filelists, `*.sv`, and existing `.rep` / `.vcd` / `.cpi` files, then writes a best-guess `svkit.toml` for the user to confirm |
| Run DB | `.svkit/runs.sqlite` plus `.svkit/runs/<id>/` artifacts. Records git SHA, dirty flag, source hash, knobs, metrics, exit status, duration and tags |
| Cache | Keyed by (source hash, knobs). Repeating a run is instant |
| Job runner | Parallel, with a configurable license-seat cap (`license_seats`) and live progress over WebSocket |
| Knob mechanics | Kinds: `file`, `env`, `tcl_var`, `tcl_replace` and `tcl_append`. Each run gets an isolated work dir. A generated TCL wrapper sets variables, then `source`s the user's unchanged script. Text replacement is the last resort |
| Adapters | `run()` + `parse()` interfaces. The normalized schemas are `SynthResult`, `TimingPath`, `PerfResult` and `XTraceNode` |
| Fixtures | A counter, a FIFO, a toy 5-stage pipeline, a planted X bug for each supported root-cause class, and a latch. Each one runs on both flows and has golden reports |
| Server / web | FastAPI + WebSocket, plus a React/TS shell with the sidebar from [§7](../PRODUCT.md#7-ux-sketch). Ships as prebuilt static assets |

**Exit:** `svkit sim` and `svkit synth` record Runs on the fixtures under VCS/DC **and** Verilator/Yosys.

## 4. MVP features

The features are listed in build order.

### 4.1 Synthesis dashboard and compare

See [§6.5](../PRODUCT.md#65-synthesis-dashboard-and-comparison).

**In scope**
- Parsers that normalize to `SynthResult`:
  - DC: `report_qor`, `report_area -hierarchy`, `report_timing`, `report_constraints`, `report_power`, `report_reference`
  - Yosys: `stat -json`
  - OpenSTA: timing reports
- A single-run dashboard showing:
  - WNS and TNS
  - hold WNS
  - total area and area by hierarchy (as a table)
  - power: dynamic, leakage and total
  - cell count
  - Vt mix, counted with `[library].vt_suffix`
  - the top critical paths
- An N-run compare table with a delta for each metric and better/worse markers.
- `svkit synth --set knob=value`, `--exp <name>` and `--matrix`, which runs every variant in `[experiments]` and opens the compare view.

**Acceptance**
- Parsed numbers match the golden fixture reports for each parser.
- The compare view shows correct deltas.
- `--matrix` on the dogfood project runs baseline, `compile_ultra` and SS corner without editing the user's TCL.

### 4.2 Minimum clock-period sweep

See [§6.4](../PRODUCT.md#64-minimum-clock-period-sweep).

**In scope**
- The initial estimate is `T* ≈ T0 − WNS0`. From there, run a parallel grid that is bounded by license seats and refined until the bracket is smaller than `--tol`.
- Each point is its own cached Run.
- A point passes when setup WNS ≥ 0 and there is no hold violation.
- Output:
  - a slack-vs-period chart (ECharts)
  - the chosen T_min
  - area and power at each point

**Acceptance:** the sweep finds the known T_min on the fixtures under both flows, and every chart point links to its synthesis Run.

### 4.3 Waveform viewer

See [§6.1](../PRODUCT.md#61-waveform-viewer).

**In scope**
- Loads VCD and FST through `wellen`. The server streams only the visible signals and time window.
- A canvas renderer with virtualized rows.
- Hierarchy search, zoom and pan, and cursors.
- Packed structs shown as named fields and enums shown as names. The types come from the DesignGraph (see §5).
- "Next change" and "next X" navigation.
- **Golden-diff jump:** a failing test opens at the first cycle where its output diverges from golden.

**Acceptance**
- A large VCD (hundreds of MB) pans and zooms smoothly over SSH port forwarding.
- A failing fixture test opens at the divergence.

### 4.4 CPI and TPI

See [§6.6](../PRODUCT.md#66-performance-cpi-and-tpi).

**In scope**
- A regex metric source over existing output files (`[perf].source`).
- A table and a chart of CPI per test program.
- TPI = CPI × the clock period of the latest synthesis Run, or T_min when a sweep exists.

**Acceptance:** the CPI values for the dogfood project match its `.cpi` files, and TPI updates when a new synthesis or sweep Run lands.

### 4.5 Block and gate diagrams

See [§6.2](../PRODUCT.md#62-block-and-gate-level-diagrams).

**In scope**
- A block diagram at module level, built from the DesignGraph, laid out with ELK.js, and expandable by hierarchy.
- A gate-level cone for a chosen net, N levels deep. The input is the DC `.vg` (read with pyslang) or the Yosys JSON netlist.
- The critical path highlighted on the gate cone, with per-cell delays, for both DC and OpenSTA paths.

**Acceptance:** the critical path from each fixture's timing report maps onto the netlist instances with no unresolved points.

### 4.6 X-propagation tracer ★

See [§6.3](../PRODUCT.md#63-x-propagation-tracer--flagship).

**In scope**
- A driver graph built from the DesignGraph covering `assign`, `always_comb`, `always_ff` and port connections, at signal granularity.
- A backward trace from `(signal, t)`:
  - combinational drivers are checked at `t`
  - sequential drivers are checked at the previous active clock edge
  - the trace stops at a depth or time bound
- Root-cause classes:
  1. a register that is never reset or never assigned
  2. an uninitialized memory read or an out-of-range index
  4. X or Z entering from the testbench or a top-level port
- Output: `svkit xtrace <hier.signal> @<time> [--depth N]` prints a tree, and the UI shows the same tree. Each node links to its waveform time and source line.

**Acceptance:** the tracer pinpoints every planted class-1, class-2 and class-4 bug in the fixtures.

**Known limitation:** the tracer models standard Verilog X semantics only. VCS `-xprop=tmerge` semantics come after the MVP. This matters for the dogfood project, which simulates with `-xprop=tmerge`: traces through `if` and `case` may differ from what VCS shows.

## 5. Cross-links in the MVP

These are the links from [§7](../PRODUCT.md#7-ux-sketch) that ship:

| From | To |
|---|---|
| Failing test | Waveform at the first divergence |
| X value in the waveform | X-Trace tree for that signal and time |
| X-Trace node | Waveform position and source line |
| Timing path (synthesis dashboard) | Gate cone with the path highlighted |
| Sweep point | Its full synthesis Run |
| Signal in the waveform | Source line, once the DesignGraph is loaded |

## 6. CLI in the MVP

The commands come from [§8](../PRODUCT.md#8-cli).

```
svkit init
svkit sim <test>|--all [--flow vcs|verilator]
svkit synth [--set knob=value ...] [--exp name | --matrix]
svkit sweep clock [--tol 50ps] [--jobs N]
svkit xtrace <hier.signal> @<time> [--depth N]
svkit runs [ls|show|diff <a> <b>|tag]
svkit serve [--port 8470]
```

`svkit report --html` comes after the MVP.

## 7. Out of scope (post-MVP)

| Area | Deferred | Source |
|---|---|---|
| Waves | Pipeline (instruction-by-cycle) view; "next time `expr` is true" search; derived signals; saved views in `svkit.toml` | §6.1 |
| Waves | Native FSDB reader | §5.3 |
| Diagrams | Gate view built from the RTL; full-netlist views | §6.2 |
| X tracer | Classes 3 (multiple drivers / contention) and 5 (case without default / latch); bit-range granularity; tmerge semantics; gate-level / SDF tracing | §6.3, §12 |
| X tracer | First-X timeline; static X-risk lint | §6.3 |
| Sweep | Gate-level sim at T_min; a view of how the critical-path endpoints move | §6.4 |
| Synthesis | Area treemap, critical-path waterfall, cell-type histogram, power breakdown chart, Pareto chart, triaged warnings, the "stage optimized away" detector | §6.5 |
| Perf | The `svkit_perf.sv` JSON-lines source; stall-cause bars; CPI history across commits | §6.6 |
| Convenience | Test matrix and watch mode, log triage, lint aggregation, notifications, HTML report, branch compare | §6.7 |
| Interfaces | TUI, VS Code wrapper, LLM explanations | §6.7 |
| Toolchains | Icarus, lint adapters | §5.3 |

## 8. Build order

This follows [§10](../PRODUCT.md#10-roadmap), with one adjustment: the waveform viewer's struct/enum decoding (Phase 2) needs the DesignGraph's **hierarchy and types**. That part of the DesignGraph moves up into Phase 2. Driver extraction stays in Phase 3.

| Phase | MVP content | Exit criteria |
|---|---|---|
| 0 · Foundation | §3 | `sim` and `synth` record Runs on fixtures under both flows |
| 1 · Synthesis | §4.1, §4.2 | Sweep finds the known T_min on the fixtures; compare shows correct deltas; `--matrix` works on the dogfood project |
| 2 · Waves + Perf | DesignGraph (hierarchy and types), §4.3, §4.4 | A large VCD opens smoothly; a failing test opens at the divergence; CPI matches the `.cpi` files |
| 3 · Design + X | DesignGraph drivers, §4.5, §4.6 | The tracer pinpoints every planted class-1/2/4 bug; the critical path maps onto the gate cone |

Phase 4 (polish) is after the MVP.

## 9. Open decisions

**Needed before or early in Phase 0** (see CONTEXT.md §2):
- Minimum Python: 3.11 proposed (#8).
- Frontend build: prebuilt assets shipped in the package proposed (#9).
- Knob mechanics for DC (#10). This now blocks `--matrix`.
- Wrap existing build scripts only, never generate them (#11).

**Needed by Phase 2:**
- Whether to embed Surfer or build our own wave renderer (#13).
- How the golden-diff jump maps the first mismatching output line to a sim time. Possible answers:
  - a time or cycle field captured by a regex in the output file
  - a `[flows.sim.*]` rule naming a commit signal whose Nth assertion matches line N

**Not blocking:** the final name and git hosting (#6, #7), and the license (#12).
