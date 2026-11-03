# svkit — Product & Design Document

> **Status:** Draft v0.1 (2026-11-02) · **Name:** working title, to be finalized
> A living document: edit freely, and record decisions in [§13 Decision Log](#13-decision-log).
> **MVP scope:** [`docs/MVP.md`](docs/MVP.md) lists exactly what v1 ships, what it leaves out, and its acceptance criteria.

---

## 1. Vision

**svkit is a debug and PPA (performance, power, area) workbench that plugs into any SystemVerilog project.** You add one config file to an existing repo and get a local web dashboard plus a CLI that answer the questions designers actually ask:

- *What is my design doing?* (waveforms, block and gate diagrams)
- *Why is this signal X?* (root-cause tracing)
- *How fast can it go?* (minimum clock period search)
- *What did that synthesis change buy me?* (report dashboards and side-by-side comparison)
- *Is it actually faster?* (CPI, TPI)

It replaces log files and terminal scrollback with views that are **interactive, linked, and comparable over time**.

## 2. Problem

Today's SV workflow, especially in academic and small-team settings, looks like this:

| Task | Today | Pain |
|---|---|---|
| Inspect behavior | Verdi/DVE/GTKWave, raw hex values | No struct/enum decoding, heavy GUI over SSH, no link to test outcome |
| Find an X source | Manually walk fan-in in the waveform | Slow, error-prone; X can travel many cycles and modules from its origin |
| Understand structure | Read code; no diagrams | Hard to see hierarchy, connectivity, or what synthesis produced |
| Find min clock period | Edit `cpu.clock`, resynthesize, grep slack, repeat | Hours of manual iteration, no record of results |
| Read synthesis results | Scroll a multi-thousand-line `.rep` | Key numbers buried; comparing two runs means `diff` on text |
| Evaluate optimizations | Run variants by hand, copy numbers into a spreadsheet | Easy to lose track of which settings produced which result |
| Measure performance | Read `.cpi` files one at a time | No aggregate view, no history, no link to clock period |

The tools exist, but **nothing ties them together**, and nothing remembers what you ran.

## 3. Target users

1. **Students in architecture or VLSI courses** (e.g. EECS 470). They use Synopsys VCS/DC on shared Linux servers over SSH, under deadline pressure, and spend most of their time on debugging and timing closure.
2. **Hobbyists and open-source hardware developers** using Verilator, Icarus, Yosys and OpenSTA. They want polish that open-source flows lack.
3. **Small research or startup teams** who want lightweight experiment tracking across synthesis runs without a heavy EDA framework.

## 4. Product principles

1. **Plug in, don't take over.** svkit wraps the project's existing Makefile or scripts. It never demands a restructure.
2. **Toolchain-neutral.** Synopsys and open-source flows are first-class from day one. The UI only ever sees normalized data.
3. **Everything is a Run.** Every simulation and synthesis is recorded with its inputs and outputs, so history and comparison come for free.
4. **Linked views.** Every number or node links somewhere useful: a signal to source, a timing path to the gate diagram, a test failure to the waveform at the divergence cycle, an X to its root cause.
5. **Remote-first.** It must work well over SSH on shared servers: browser via port forward, no root, `pip install --user`.
6. **CLI parity.** Anything in the dashboard can also be scripted from the CLI (for CI and power users). A TUI comes later.

---

## 5. Core concepts

### 5.1 Project (`svkit.toml`)
The single plug-in contract. It describes sources, the top module, clock and reset, how to run each flow, the knobs those flows accept, and where artifacts land.

```toml
[project]
name    = "p3-pipeline"
top     = "cpu"
sources = { glob = ["verilog/*.sv"], filelist = "" }
include = ["verilog/"]
defines = {}
clock   = "clock"
reset   = { name = "reset", active = "high" }

[flows.sim.vcs]                       # wrap the existing Makefile
cmd       = "make {test}.out"
waves     = "output/{test}.vcd"
log       = "output/{test}.out"
golden    = { actual = "output/{test}.wb", expected = "output_correct/{test}.wb" }
tests     = { glob = "programs/*.lc2k" }

[flows.synth.dc]
cmd       = "make cpu.vg"
reports   = ["synth/cpu.rep"]
netlist   = "synth/cpu.vg"
log       = "synth/cpu-synth.out"
license_seats = 2

[knobs]                                # how svkit changes a setting
clock_period = { file = "cpu.clock", unit = "ps" }   # or { env = "CLOCK_PERIOD" } / { tcl_var = "clock_period" }
corner       = { tcl_var = "corner" }
map_effort   = { tcl_replace = "compile -map_effort {value}" }

[experiments]                          # named synthesis variants
baseline         = {}
high_area_effort = { area_effort = "high" }
mixed_vt         = { target_vt = ["RVT", "LVT"] }
max_fanout_8     = { tcl_append = "set_max_fanout 8 [current_design]" }
ss_corner        = { corner = "SS" }
compile_ultra    = { tcl_replace_compile = "compile_ultra" }

[perf]                                 # where CPI comes from
source = { regex_file = "output/{test}.cpi", cycles = 'cycles\s*=\s*(\d+)', instrs = 'instrs\s*=\s*(\d+)' }

[library]
vt_suffix = { "_R" = "RVT", "_L" = "LVT", "_SL" = "SLVT" }   # e.g. ASAP7 cell naming
```

`svkit init` scans the repo (Makefile targets, `*.f` filelists, `*.sv`, existing `.rep`/`.vcd`) and writes a best-guess `svkit.toml` for the user to confirm.

### 5.2 Run
A recorded execution of one flow.

| Field | Example |
|---|---|
| id, kind | `r_0042`, `synth` / `sim` / `sweep-point` |
| inputs | git SHA, dirty flag, source content hash, knobs `{clock_period: 4000, corner: TT}` |
| outputs | parsed metrics (normalized schema), artifact paths, exit status, duration |
| tags | user labels (`"before-bypass-fix"`) |

Stored in `.svkit/runs.sqlite`, with artifacts copied or linked under `.svkit/runs/<id>/`. Results are cached by `(source hash, knobs)`, so repeating a run is instant.

### 5.3 Adapter
A plugin that knows how to **run** a tool and **parse** its outputs into a normalized schema.

| Category | Adapters (v1) |
|---|---|
| Simulator | VCS, Verilator (Icarus after the MVP) |
| Waveform | VCD, FST (native); VPD (via `vcs -vpd2vcd`), FSDB (via `fsdb2vcd` or Verdi reader if present) |
| Synthesis | Design Compiler, Yosys |
| Timing | DC `report_timing`, OpenSTA |
| Lint | slang, Verilator `--lint-only`, Verible |

### 5.4 DesignGraph
An elaborated model of the RTL (or gate netlist) built with **pyslang**. It holds the hierarchy, ports, nets, types (structs/enums), and for every signal its **drivers** together with their data and control dependencies. It is shared by the diagrams, the X tracer, and waveform type decoding.

---

## 6. Features

Each feature lists user stories, the approach, and its MVP cut.

### 6.1 Waveform viewer
**Stories**
- As a designer, I see packed structs as named fields and enums as names, not hex.
- I can jump to the next change, the next X, or the next time `valid && !ready` is true.
- I click a signal and land on its source line.
- When a test fails, I open the waveform already positioned at the first cycle where output diverges from golden.
- I can see my pipeline as an instruction-by-cycle chart (stalls and flushes visible).

**Approach**
- Backend: `wellen` (Rust, via Python bindings) indexes VCD/FST. The server streams only the visible signals and time window over WebSocket, so multi-GB dumps stay usable.
- Frontend: canvas/WebGL renderer with virtualized rows. *Open question:* embed Surfer (WASM, scriptable via its control protocol) for raw waves vs. our own renderer. Custom overlays are needed either way.
- Type decoding comes from the DesignGraph.
- Saved views (signal groups, radix, colors) are stored in `svkit.toml` so the team shares them.
- **Pipeline view:** the user declares per-stage `{pc, valid}` signals, and svkit renders the classic diagonal pipeline chart.

**MVP:** load VCD/FST, hierarchy search, zoom/pan, cursors, struct/enum decoding, next-change/next-X, golden-diff jump.

### 6.2 Block and gate-level diagrams
**Stories**
- I see my design as a block diagram I can expand and collapse by hierarchy.
- I pick a net in the synthesized netlist and see its fan-in cone as gates.
- I see the critical path from the timing report highlighted on the gates, with per-cell delays.

**Approach**
- Block view: DesignGraph → ELK.js layered layout → interactive SVG with bus widths and port directions.
- Gate view: parse the `.vg` netlist (pyslang handles structural Verilog) or Yosys JSON. **Render only the cone of a selected net, N levels deep**, because a full netlist is unreadable.
- Critical-path overlay: map timing-report path points to netlist instances.

**MVP:** module-level block diagram, gate-level cone view for a chosen net, critical-path highlight.

### 6.3 X-propagation tracer ★ (flagship)
**Stories**
- I right-click an X in the waveform and svkit tells me *where it came from*, as a chain from root cause to symptom.
- Before simulating, svkit warns me about X risks: registers with no reset, `case` statements without a default, inferred latches.

**Approach**
1. **Driver graph** (from DesignGraph): for each signal, its drivers (`assign`, `always_comb`, `always_ff`, port connections) and their dependencies: RHS operands, `if`/`case` conditions, and array index expressions.
2. **Backward trace** from `(signal, t)`:
   - combinational driver → evaluate inputs at `t`
   - sequential driver → evaluate inputs at the previous active clock edge
   - recurse into inputs that are X; stop at a root cause or after a depth or time bound
3. **Root-cause classification**
   - register never reset or never assigned
   - uninitialized memory read, or out-of-range index
   - multiple drivers or bus contention (Z/X resolution)
   - X/Z entering from the testbench or a top-level port
   - `case` with no default / inferred latch holding X
4. **Output:** a causality tree with times. Each node links to its waveform position and source line.
5. **Companion views:**
   - a **first-X timeline** (when each signal first went X after reset deasserted)
   - **static X-risk lint**

**Notes**
- Needs full-visibility dumps (`-debug_access+all` / `+all`).
- VCS `-xprop=tmerge` changes how X propagates through `if`/`case`, so the tracer models both standard and tmerge semantics.
- The tracer starts at signal granularity and refines to bit ranges later.

**MVP:** RTL-level trace through assign/always_comb/always_ff with signal granularity; root-cause classes 1, 2, and 4; CLI and UI tree.

### 6.4 Minimum clock period sweep
**Stories**
- I run one command and get the lowest clock period that meets timing (setup and hold), with a plot.
- I see how the critical path moves as the period tightens.

**Approach**
```
T0 = current period → synth → WNS0
estimate T* ≈ T0 − WNS0
launch a small parallel grid around T* (bounded by license seats)
pick the tightest passing point and the loosest failing point → refine the grid between them
stop when the bracket < tolerance (e.g. 50 ps)
```
- A grid is used instead of pure bisection because **synthesis is non-monotonic**: the tool tries harder under tighter constraints, so a tighter target can sometimes pass where a looser one failed.
- Each point is its own Run in an isolated work dir and is cached.
- Optional: run gate-level simulation at the chosen period to confirm functional correctness.

**Output:** a slack-vs-period chart, the chosen T_min, area and power at each point, and how the critical-path endpoints change.

**MVP:** sweep with license-aware parallelism, chart, chosen T_min.

### 6.5 Synthesis dashboard and comparison
**Stories**
- I open a synthesis run and immediately see: WNS/TNS, hold, area (total and per module), power, cell count, Vt mix, and warnings that matter.
- I select several runs (baseline, high area effort, mixed-Vt, max fanout 8, SS corner) and see a side-by-side table with deltas and a Pareto chart.
- svkit tells me when synthesis **removed** a register or stage because it was constant or unloaded (the "my stage got optimized away" bug).

**Approach**
- Parsers: DC (`report_qor`, `report_area -hierarchy`, `report_timing`, `report_constraints`, `report_power`, `report_reference`, `report_resources`), Yosys (`stat -json`), OpenSTA reports. All normalize to `SynthResult`.
- Views:
  - QoR cards
  - hierarchical area treemap
  - critical-path waterfall (per-stage delay)
  - cell-type histogram
  - **Vt-mix breakdown** (counted from cell-name suffixes via `[library].vt_suffix`)
  - power breakdown
  - triaged warnings
- **Experiment matrix:** `svkit synth --matrix` runs every named variant from `[experiments]` and opens the comparison view.

**MVP:** DC, Yosys, and OpenSTA parsers, single-run dashboard, N-run compare table with deltas, `svkit synth --exp` / `--matrix`.

### 6.6 Performance: CPI and TPI
**Definitions:** CPI = cycles ÷ retired instructions. **TPI** (time per instruction) = CPI × clock period. TPI is the metric that settles trade-offs like "deeper pipeline at a faster clock" vs. "lower CPI".

**Approach**
- **Metric sources** (generic, because every project prints differently):
  - (a) regex rules over existing output files, or
  - (b) a drop-in `svkit_perf.sv` module that emits JSON-lines counters: cycles, retired instructions, stall cycles by cause, branch mispredicts.
- Dashboard:
  - CPI per test program
  - stall-cause stacked bars
  - history across commits
  - **TPI joined with synthesis/sweep runs** (CPI at commit X × T_min at commit X)

**MVP:** regex source, per-program CPI table and chart, TPI using the latest synthesis clock period.

### 6.7 Convenience features
- **Test matrix:** tests × {RTL, half-synth, full-synth} with pass/fail status, regression alerts, and watch mode that reruns affected tests on save.
- **Log triage:** deduplicate and classify VCS/DC/Verilator messages, link them to source, and maintain a per-project "known noise" list.
- **Lint aggregation:** slang, Verilator, and Verible results in one table.
- **Notifications:** desktop or terminal alert when long synthesis or sweeps finish.
- **Shareable report:** `svkit report --html` produces a static snapshot for teammates or TAs.
- **Branch compare:** compare runs across git branches or commits.
- **Later:** TUI (Textual), VS Code webview wrapper, LLM-assisted "explain this trace/report".

---

## 7. UX sketch

```
┌──────────────────────────────────────────────────────────────────────────┐
│ svkit · p3-pipeline   main@c81a80e (dirty)            [Run ▾] [Sweep] ⚙  │
├────────────┬─────────────────────────────────────────────────────────────┤
│ Runs       │  Synthesis · compare (3)                                    │
│ Tests      │  ┌───────────┬──────────┬──────────┬──────────┐             │
│ Waves      │  │ metric    │ baseline │ hi_area  │ ss_corner│             │
│ Design     │  ├───────────┼──────────┼──────────┼──────────┤             │
│ X-Trace    │  │ WNS (ps)  │   +12    │   −30 ▼  │  −410 ▼  │             │
│ Synthesis ●│  │ Area (µm²)│  8 210   │ 7 640 ▲  │  8 230   │             │
│ Sweep      │  │ Power (mW)│   3.1    │   2.9 ▲  │   2.7    │             │
│ Perf       │  │ LVT cells │   0 %    │   0 %    │   0 %    │             │
│ Logs/Lint  │  └───────────┴──────────┴──────────┴──────────┘             │
│            │  [area treemap]   [critical path waterfall]   [Pareto]      │
└────────────┴─────────────────────────────────────────────────────────────┘
```

**Key cross-links**
- test failure → waves at divergence
- X in waves → X-Trace tree
- timing path → gate diagram
- signal → source line
- sweep point → full synthesis run

---

## 8. CLI

```
svkit init                         # detect project, write svkit.toml
svkit sim <test>|--all [--flow vcs|verilator|icarus]
svkit synth [--set knob=value ...] [--exp name | --matrix]
svkit sweep clock [--tol 50ps] [--jobs N]
svkit xtrace <hier.signal> @<time> [--depth N]
svkit runs [ls|show|diff <a> <b>|tag]
svkit serve [--port 8470]          # dashboard; tunnel with ssh -L
svkit report --html [runs...]
```

## 9. Architecture

```
svkit/
  core/       config loader, Run DB (sqlite), artifact store, job runner (parallel, license-aware, cache)
  adapters/   sim/ waves/ synth/ sta/ lint/   — each: run() + parse() → normalized schema
  design/     pyslang elaboration → DesignGraph (hierarchy, types, drivers)
  analysis/   xtrace, sweep, synth_diff, perf, log_triage
  server/     FastAPI + WebSocket (run progress, wave chunks)
  web/        React + TypeScript · canvas/WebGL waves · ELK.js diagrams · ECharts
  tui/        (later) Textual, same core API
fixtures/     tiny designs runnable on both flows + golden reports
```

**Why Python for the core:**
- `pyslang` (full SV front end) and `wellen` bindings (fast wave parsing) are available.
- It installs easily on shared servers with no root.
- It is the language most hardware engineers already script in.

**Normalized schemas (sketch)**
```python
SynthResult: wns, tns, hold_wns, area_total, area_by_hier{}, cells_by_type{}, vt_mix{},
             power{dynamic, leakage, total}, paths[TimingPath], warnings[Msg], period, corner
TimingPath:  startpoint, endpoint, slack, points[{inst, cell, pin, incr, arrival}]
PerfResult:  test, cycles, instrs, cpi, stalls{cause: cycles}, period → tpi
XTraceNode:  signal, time, value, driver{kind, file, line}, cause?, children[]
```

## 10. Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 · Foundation** | config + `init`, Run DB, job runner, adapter interfaces, fixture corpus on both flows, server/web shell | `svkit sim` and `svkit synth` record Runs on fixtures under VCS/DC **and** Verilator/Yosys |
| **1 · Synthesis** | DC/Yosys/OpenSTA parsers, synthesis dashboard, compare/matrix, clock sweep | Sweep finds the known T_min on fixtures; compare view shows correct deltas |
| **2 · Waves + Perf** | DesignGraph hierarchy and types, wellen backend, viewer with type decoding, golden-diff jump, CPI/TPI | Open a large VCD smoothly; failing test opens at divergence |
| **3 · Design + X** | DesignGraph drivers, block and gate diagrams, critical-path overlay, X tracer | Tracer pinpoints every planted X bug in the fixtures |
| **4 · Polish** | test matrix and watch mode, log triage, lint, HTML report, TUI, VS Code wrapper | Dogfooded through a full course project |

## 11. Risks

| Risk | Mitigation |
|---|---|
| VPD/FSDB are proprietary; conversion is slow on big dumps | Prefer FST; scope dumps; use the Verdi FSDB reader when licensed |
| Driver extraction is hard (generate blocks, arrays, partial selects, interfaces) | Signal-level first, bit-level later; fixture coverage for each construct |
| DC report formats vary by version | Tolerant parsers; golden fixtures for every version encountered |
| DC license seats limit parallel sweeps | Configurable seat cap and queue; cache aggressively |
| Shared-server constraints (no root, old Python) | Pure-pip install, vendored web build, minimum Python version decided early |
| Scope creep across seven feature areas | Strict phase exit criteria; fixtures as the definition of done |

## 12. Open questions

- [x] Repo location: `/home/hermong/svkit`.
- [ ] Final name (`svkit` is a working title) and git hosting (GitHub? public/private).
- [ ] License (MIT / Apache-2.0?): deferred until the repo goes public.
- [x] Minimum Python version: 3.11 (decided).
- [x] Frontend build: prebuilt assets shipped in the Python package (decided).
- [x] Wrap existing builds by default; `builtin` adapter mode for projects without one (decided).
- [x] Experiment knob mechanics: see decision log and ARCHITECTURE.md §2.3 (decided).
- [ ] Waveform frontend: embed Surfer vs. build our own renderer (decide in Phase 2).
- [ ] How to model the gate-level X tracer for sims that use SDF timing (Phase 3+).
- [x] Golden-diff jump: `golden.time_regex` (decided).

## 13. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-11-02 | Local web app + CLI first; TUI later | Rich views (waves, diagrams, charts) need a browser; must still work over SSH |
| 2026-11-02 | Synopsys and open-source flows equally from day one | Toolkit must be generic; adapters + normalized schemas keep the UI single |
| 2026-11-02 | MVP includes X tracer, synthesis dashboard, clock sweep, waves + CPI | All four are the core pain points |
| 2026-11-02 | Python core, React/TS frontend | pyslang + wellen ecosystem; easy install on shared servers |
| 2026-11-02 | Project lives at `/home/hermong/svkit` | Separate from any course repo; the tool is generic |
| 2026-11-02 | Open-source tools from OSS CAD Suite in `~/tools/oss-cad-suite` | No OSS EDA tools on CAEN; one prebuilt bundle, no root needed |
| 2026-11-02 | MVP scope written up in `docs/MVP.md` | One place for what v1 ships, what it defers, and acceptance criteria |
| 2026-11-02 | OpenSTA is in the MVP | Yosys has no timing; sweep, WNS, and the critical path need it for open-source parity |
| 2026-11-02 | `svkit synth --exp` / `--matrix` are in the MVP | Directly answers the original ask (area effort, mixed Vt, fanout, SS corner); reuses the sweep's knob mechanics |
| 2026-11-02 | MVP simulators are VCS and Verilator; Icarus after the MVP | Matches Phase 0 exit criteria; keeps adapter count down |
| 2026-11-02 | Python ≥ 3.11; built web assets committed into the package | Shared servers lack Node/new Python; `pip install` must be enough |
| 2026-11-02 | Knobs: isolated full-copy work dir; `tcl_var` injected into the work-dir copy with the user's own default guarded | A plain `source` wrapper is defeated by `set corner TT` inside the user's script; the original is never edited |
| 2026-11-02 | Flows run in `wrap` mode (user's command) by default; adapters also offer `builtin` mode | Projects without a build (and the fixtures) still work; svkit never writes build files into the user's repo |
| 2026-11-02 | Golden-diff jump uses `golden.time_regex` (time or cycle) on the first mismatching line | Generic; works for any output format that prints a time or cycle |
| 2026-11-02 | OSS fixtures use an original toy Liberty library | Deterministic T_min, no PDK licensing questions |
| 2026-11-02 | Sweep falls back to a setup-only criterion when hold fails at the loosest point, and records why | Hold slack is period-independent; real DC flows without `set_fix_hold` otherwise never pass and the search grows without bound |
| 2026-11-02 | In wrap mode, an output the build left untouched counts if it is newer than every source | Make skips up-to-date targets; older files are still treated as stale |
| 2026-11-02 | DC runs with unmapped logic, unresolved references or an unreadable target library are errors | DC exits 0 after these, and their QoR numbers are meaningless |
| 2026-11-03 | When a flow's tools are missing on the host, svkit falls back to open-source tools automatically (`[flows].fallback = true` by default). Order: another configured flow that can run → for a VCS wrap flow, the user's own build with a `vcs` stand-in that compiles with Verilator → for DC, builtin Yosys + OpenSTA (on `[library].liberty`, else svkit's generic demo library). Every fallback run is labelled with the tool actually used and an info note; cached results never mix backends | Users without Synopsys access (laptops, non-CAEN hosts) must not have to hand-write a second flow. The stand-in keeps the user's Makefile (program assembly, golden diff) untouched, so the result is the same test. Verilator is 2-state, so X-trace still needs a 4-state simulator |
| 2026-11-03 | Makefiles that set `SHELL := $(SHELL) <bash options>` run with bash on hosts where /bin/sh is dash (via `MAKEFLAGS=SHELL=<wrapper>`) | Course Makefiles assume /bin/sh is bash (CAEN); the Makefile is never edited |
