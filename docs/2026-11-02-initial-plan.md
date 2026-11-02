# svkit (working name): a plug-in debug and PPA workbench for SystemVerilog projects

## Context
Debugging and tuning SV designs today means reading terminal output, grepping `.rep` files, opening Verdi, and editing `cpu.clock` by hand. The goal is a **generic toolkit that drops into any SV repo** through one config file. It provides a local web dashboard plus CLI (a TUI comes later) covering waveforms, diagrams, X root-causing, clock-period search, synthesis report comparison, and CPI/TPI. It supports **Synopsys (VCS/Verdi/DC) and open-source (Verilator/Icarus/Yosys/OpenSTA) flows equally from day one**. It lives in its own new repo. This EECS 470 project (VCS + DC + ASAP7, `.vpd→.vcd`, `.rep`, `.cpi`) is only the first dogfood target.

---

## 1. Product shape

**Unifying idea: every simulation or synthesis is a "Run".** A run records its inputs (git SHA, source hash, knobs such as clock period, effort, Vt mix, corner) and its parsed outputs (waves, metrics, logs) in `.svkit/runs.sqlite` plus an artifact dir. Every feature is then a view over runs, which makes comparing runs, keeping history, and catching regressions cheap.

```
svkit init            # scan repo (Makefile, *.f filelists, *.sv) → write svkit.toml
svkit sim  <test>     # run sim via adapter, record Run, index waves
svkit synth [--set k=v ...]   # run synth, parse reports, record Run
svkit sweep clock     # find min passing clock period
svkit xtrace <sig> @<time>    # CLI X root-cause
svkit serve           # web dashboard (SSH: forward port)
svkit report --html   # static shareable snapshot
```

**Dashboard pages:** Runs/History · Waves · Design (block + gate diagrams) · X-Trace · Synthesis (single + compare) · Timing Sweep · Performance (CPI/TPI) · Lint/Log triage.

**Plug-in contract (`svkit.toml`):** sources/filelist, include dirs, defines, top module, clock/reset names; per-flow command templates with knobs (e.g. `period = {env = "CLOCK_PERIOD"}` or `{file = "cpu.clock"}` or `{tcl_var = "clock_period"}`); artifact globs (`synth/*.rep`, `output/*.vcd`, `output/*.cpi`); named experiment variants. Users can **wrap their existing Makefile** instead of replacing it. This is the key to "plug into any project".

## 2. Architecture

```
svkit/
  core/        config, Run DB (sqlite), artifact store, job runner (parallel, license-aware)
  adapters/    sim: vcs, verilator, icarus | waves: vcd, fst, vpd*, fsdb* | synth: dc, yosys | sta: dc, opensta
  design/      pyslang-based elaboration → DesignGraph (hierarchy, ports, nets, drivers, types)
  analysis/    xtrace, sweep, synth_diff, perf (cpi/tpi), log_triage, lint
  server/      FastAPI + WebSocket (live run progress, wave chunk streaming)
  web/         React + TS (canvas/WebGL waves, ELK.js diagrams, ECharts charts)
  tui/         (later) Textual, reusing core APIs
```
- **Python core.** It has the best libraries for this job: `pyslang` (full SV-2017 front end), `pywellen` (fast VCD/FST/GHW reader written in Rust), and is easy to pip-install on CAEN.
- **Adapters normalize to shared schemas:** `SynthResult{area_by_hier, cells_by_type, vt_mix, paths[], wns, tns, hold, power, warnings}`, `WaveDB`, `PerfResult`. The UI only ever sees the schemas, so supporting both toolchains doesn't fork the UI.
- **Proprietary formats:** VPD via `vcs -vpd2vcd`, FSDB via `fsdb2vcd` or Verdi's FSDB reader when available. Prefer FST where the simulator supports it.
- **Fixture corpus:** small designs (counter, FIFO, 5-stage toy pipe, intentional X bug, intentional latch) that run on *both* flows. These double as golden tests for every parser.

## 3. Feature strategies

### Waveforms
- Backend: wellen indexes the dump; the server streams only the visible signals and time window, so huge VCDs stay usable.
- Frontend: custom canvas renderer (evaluate embedding Surfer/WASM for raw waves vs. building our own; we need custom overlays either way).
- **SV-aware decoding.** DesignGraph types render packed structs as expandable fields and enums as names, which is a big win over raw hex.
- Click-to-source, signal search by hierarchy, saved views and groups in `svkit.toml`, "next change" / "next X" / "next time `expr` is true" navigation, and derived signals (`valid && ready`).
- **Pipeline view:** the user declares per-stage PC/valid signals, and svkit draws an instruction-by-cycle diagram (stalls and flushes visible).
- **First-divergence jump:** diff a test's output against the golden file (like the `.wb` check here), then open the waves at the first mismatching cycle.

### Block and gate diagrams
- **Block diagram:** DesignGraph from RTL → ELK.js layered layout → interactive SVG that expands and collapses hierarchy and shows bus widths.
- **Gate level:** parse the synthesized `.vg` (pyslang reads structural Verilog) or Yosys JSON. **Render only the fan-in/fan-out cone of a selected net, to N levels**, because whole netlists are unreadable.
- **Critical-path overlay:** highlight the cells of a timing-report path on the gate view, annotated with per-cell delay.

### X-propagation tracer (the differentiator)
1. **Driver graph:** from pyslang, for each signal record its drivers (assign / always_comb / always_ff) and their data and control dependencies (RHS, if/case conditions, index expressions). Start at signal granularity and refine to bit ranges later. Gate-level sims use the netlist directly.
2. **Trace backwards** from (signal, t):
   - Combinational drivers are checked at time t.
   - Flops are checked at the previous active clock edge.
   - The tracer recurses into whichever inputs were X and builds a causality tree annotated with times.
3. **Classify root causes:**
   - reg without reset or never assigned
   - uninitialized memory or out-of-range index
   - multiple drivers / contention
   - X or Z from a TB/input port
   - case without default, or an inferred latch
4. **Supporting views:** a "first-X timeline" scan that shows where X first appears after reset deasserts, plus static X-risk lint (no reset, incomplete case, latch).
5. **UI:** a clickable tree; each node jumps to its waves and source line.
- Note: this works best with full dumps (`+all` / `-debug_access+all`). VCS `-xprop=tmerge` semantics differ from plain Verilog, so the tracer must model both.

### Minimum clock-period sweep
- Start from one run at period T, then estimate `T_min ≈ T − WNS`. Run a **small parallel grid** around the estimate, not a pure bisection: synthesis is non-monotonic, because the tool works harder under tight constraints. Refine until the result is within a tolerance.
- **License-aware concurrency** (DC seat limit, configurable). Each run gets an isolated work dir, and results are cached by (source hash, knobs).
- **Output:** a slack-vs-period plot, the lowest period with WNS ≥ 0 and no hold violations, and how the critical path endpoints shift. An optional step runs a gate-level sim at T_min to confirm correctness.

### Synthesis dashboard and compare
- **Parsers:**
  - DC: `report_qor/area -hier/timing/constraints/power/reference/resources`
  - Yosys: `stat -json`
  - OpenSTA: timing reports
- **Views:**
  - QoR summary cards
  - hierarchical area treemap
  - critical-path waterfall
  - cell-type histogram
  - **Vt mix** (counted from library cell-name suffixes, e.g. ASAP7 `_R/_L/_SL`, with a configurable suffix map)
  - power breakdown
  - warnings triage (removed registers or constant-propagated logic, latches, unloaded nets: the "my stage got optimized away" detector)
- **Experiments:** named variants in config (`high_area_effort`, `mixed_vt`, `max_fanout_8`, `ss_corner`, `compile_ultra`) are TCL or knob overrides. `svkit synth --matrix` runs them all and shows a **side-by-side diff with deltas** plus an area/period/power Pareto plot.

### CPI / TPI
- **Generic metrics source:**
  - (a) a regex or parse rule over the sim output (e.g. this project's `.cpi`), or
  - (b) a tiny drop-in `svkit_perf.sv` module that emits JSON-lines counters: cycles, retired instructions, stall cycles by cause, mispredicts.
- **Dashboard:** CPI per program, stall breakdown as a stacked bar, history across commits, and **TPI = CPI × T_clk** joined with sweep results. This shows the real trade-off between "deeper pipeline at a faster clock" and "lower CPI".

### Convenience features
- Test matrix (programs × sim/hsyn/syn) with pass/fail and regression alerts, plus a watch mode that reruns affected tests on save.
- Log triage: dedupe and classify VCS/DC/Verilator warnings, link them to source, and hide known noise.
- Lint aggregation (slang, Verilator `--lint-only`, Verible).
- Desktop or terminal notification when a long synthesis finishes.
- Static HTML report export for teammates or a TA, and comparing runs across branches.

## 4. Roadmap
- **Phase 0 (foundation):** config and `svkit init` auto-detect, Run DB, job runner, adapter interfaces, fixture corpus on both flows, and the server/web shell.
- **Phase 1:** synthesis parsers (DC + Yosys + OpenSTA), dashboard, compare/matrix, clock sweep. This is the quickest win and is pure parsing plus orchestration.
- **Phase 2:** wave backend and viewer with SV type decoding, golden diff with first-divergence jump, CPI/TPI.
- **Phase 3:** DesignGraph-driven block and gate diagrams with critical-path overlay, then the X tracer. These share the driver graph, so they go together.
- **Phase 4:** convenience features, TUI (Textual), and a VS Code webview wrapper.

## 5. Risks and open questions
- FSDB/VPD are proprietary, so conversion is slow on large dumps. Mitigate with FST where possible and with dump scoping.
- Driver extraction from pyslang must handle generate blocks, arrays, partial selects, and interfaces. Signal-level first, bit-level later.
- DC report formats vary by version, so parsers need tolerant parsing plus golden fixtures for each version seen.
- License limits on parallel DC runs and CAEN environment quirks (no root; pip `--user` / venv).

## 6. Verification
- Unit tests: golden-file tests for every parser (DC, Yosys, OpenSTA, VCD/FST, `.cpi`) against the fixture corpus.
- End-to-end on fixtures under both flows: `svkit init → sim → synth → sweep → serve`. Check that dashboards populate, that the sweep finds the known T_min, and that `xtrace` finds the planted X bug's root cause.
- Dogfood on this repo: `svkit init` should detect the Makefile, `cpu.clock`, `synth/*.rep`, and `output/*.cpi`. Run a synthesis matrix (default vs `compile_ultra` vs SS corner), compare, sweep the clock, inject an unreset register, and confirm the tracer pins it.
