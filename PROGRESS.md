# svkit progress

## Owner summary
- **Phase:** Phases 0–3 implemented. **Both flows now run on real tools** (VCS + DC on CAEN; Verilator + Yosys + OpenSTA on the dev host). **~85 % to MVP.** Remaining: a frontend rebuild (sweep note, new API endpoints; needs Node), browser click-through of every view, web proposal 5, the Icarus decision, and the OSS flow on CAEN (blocked on OpenSTA).
- **Dogfood checkpoint (EECS 470 copy in `~/svkit-work/dogfood`):** `svkit init` needs **no hand edits**. 21/21 tests pass on VCS, the DC matrix runs (TT/SS/FF), and the clock sweep finds **T_min = 4708 ps** (14 DC runs). TPI uses it, and the dashboard API works on the real CPU.
- **Demoable on CAEN:** `cd ~/svkit-work/dogfood && svkit serve`, then `ssh -L 8470:localhost:8470 <caen host>` from your laptop.
- **New (iter 4–6):** runs on a laptop without Synopsys (automatic Verilator/Yosys/OpenSTA fallback), plain-language dashboard with (i) explanations, **unit tests as their own suite** (Tests → Unit tests, waves at the first error), FST waveform archive, and **schematic Blocks/Gates diagrams** (netlistsvg, gate symbols from Liberty).
- **Needs owner:**
  1. **OpenSTA on CAEN:** installing it (micromamba + `litex-hub::openroad` into `~/tools`) was blocked by the permission classifier as running downloaded code. Approve it, or install it yourself, to run the OSS flow and its tests on CAEN.
  2. **X-trace parity on the OSS flow:** Verilator is 2-state, so it never shows X. Recommend pulling an Icarus sim adapter into the MVP (scope change vs decision #18).
  3. **Synopsys-generated goldens are now committed** (`fixtures/golden/synopsys/real/`, paths and banner stripped). Their redistribution needs a check before the repo goes public.
  4. Git hosting, remote and license (#7, #12): the repo now has a GitHub `origin` (`Mercury1565/sv-kit`), presumably yours. Nothing has been pushed by me; confirm and record the decision.
---

## Iteration 1 — 2026-11-02 — Contracts and core skeleton
**Status:** done
**What works now:**
- `docs/ARCHITECTURE.md`: layout, `svkit.toml` schema, normalized schemas, adapter interface, Run DB, REST/WS API, CLI, fixtures.
- Typed contracts in code: `svkit.schemas` (SynthResult, TimingPath, SimResult, PerfResult, XTraceNode, Compare/Sweep), `svkit.config` (svkit.toml loader with readable errors), `svkit.core.rundb` (sqlite Run DB), `svkit.adapters.base` (adapter ABCs, RunContext, KnobPlan).
- Core: knob mechanics (file/env/tcl_var/tcl_replace/tcl_append/define), isolated work dirs, source hash + cache key, golden diff with first divergence, job runner with cache, license-seat semaphores and live events.
- FastAPI app with project/runs/groups/artifacts/jobs/source routes and `/ws/events`; Typer CLI skeleton.
**Decisions made (and why):**
- Python ≥ 3.11 declared; the dev venv uses `python3.12` because this host has no 3.11.
- System Node v22 is used on this host (it is already a modern LTS); CAEN still needs `~/tools/node`.
- OpenSTA is not part of OSS CAD Suite: installed via conda (`litex-hub::openroad`) into `~/tools/sta-env`, symlinked as `~/tools/bin/sta`. Tests put `~/tools/bin` and the OSS CAD Suite on PATH.
- Work dirs are full copies, not hard links: tools often truncate their outputs in place, which would write through a hard link into the user's repo.
- `tcl_var` knobs are injected into the work-dir copy of the script with the user's own `set var default` guarded (a pure `source` wrapper would be defeated by that line).
- Synth runs are always isolated; sim runs run in place unless `isolate = true` or a file knob applies.
- Pydantic v2 for all schemas (typed, JSON round-trip, FastAPI-native).
**Known gaps / risks:**
- No adapters, fixtures, or web UI yet.
- License seats are enforced per process only; two `svkit` processes could exceed the cap together.
**Next iteration plan:**
- Vertical slice: counter fixture → `svkit sim` on Verilator → Run recorded → listed in a web shell.
- Then spawn tracks (OSS adapters + fixtures, Synopsys adapters, analysis, web) on worktrees.
**How to see it:** `.venv/bin/pytest -q`

## Iteration 2 — 2026-11-02 — Five parallel tracks merged: both flows, analysis, design/X, web
**Status:** partial (the OSS flow is complete; the Synopsys flow is not yet run on real tools)
**What works now:**
- OSS flow: Verilator sim (builtin and wrap), Yosys synth with an OpenSTA timing/power merge, an original toy Liberty library (TT/SS, `_R`/`_L`), and the fixtures counter/fifo/pipe5/latch plus xbug_noreset/xbug_mem/xbug_input. Each fixture has goldens and a known T_min.
- Synopsys flow: DC report parsers (timing/qor/area/constraints/power/reference/resources/design/log, combined `.rep`), the DC adapter (wrap and builtin), the VCS adapter (VPD→VCD conversion), and a host-wide 2-seat cap. Tested with fake tools; real-tool tests skip here.
- Analysis: N-run compare with deltas, a non-monotonic-aware clock sweep (pipe5: T_min 693 ps at tol 20 vs measured 683 ps), CPI/TPI from the sweep T_min, a pywellen waveform backend (250 MB VCD: 0.46 s first window, ~1 ms pans), and divergence→time mapping.
- Design + X: the pyslang DesignGraph (hierarchy, types, drivers), an X tracer that pinpoints all planted class 1/2/4 bugs in 4-state waves, a gate cone with the critical path (0 unresolved points), and struct/enum decode for waves.
- `svkit init` with generic heuristics; tested on the fixtures and on a synthetic dogfood-shaped project.
- Web: a React/TS dashboard with all MVP views, a canvas wave renderer, ECharts and ELK. Its built assets are committed (entry 58 kB gzip). It went through worst-case data checks.
- Tests: 196 pytest passed, 4 skipped (Synopsys end-to-end), with the OSS tests run for real; 35 Vitest.
**Decisions made (and why):**
- Applied track proposals to the contracts:
  - `SweepPoint.note`; sweep jobs forward `start`.
  - The runner applies `flow.env`.
  - `SynthFlow.append_before` anchors experiment `tcl_append`.
  - `SimFlow.build_cmd` and `parallel` handle wrap builds that race when run in parallel.
  - `RunContext.cache_dir` (`.svkit/build`) for adapter build caches.
  - The sim cache key covers the per-test input and golden files.
  - Docs: the dense-waveform marker `"*"`, the `/` hierarchy separator, the cycle→time convention, and the host-wide Synopsys seat cap.
- The fixture DC scripts compile from a proc called after the script body, so an experiment's `tcl_append` needs no anchor there.
- Liberty paths are resolved against the project root (isolated work dirs don't contain sibling dirs).
**Known gaps / risks:**
- No real VCS/DC run yet. The DC report formats are hand-written reproductions and need checking on CAEN.
- No dogfood checkpoint (repo not on this host).
- X tracing on the OSS flow needs a 4-state sim (escalated). The X goldens come from iverilog as test data.
- Open web proposals: `/api/design/signal` for exact source lookup, an `X-Total-Count` header, and earlier `run_ids` on `job.updated`. Interactive browser checks were done from code only.
- The README still lacks setup, the `svkit.toml` reference and the adapter guide.
**Next iteration plan:**
- On CAEN: run the `synopsys`-marked tests, replace the hand-written DC samples with real output, and do the dogfood checkpoint on `~/svkit-work/dogfood/`.
- If the owner approves: an Icarus adapter for X-trace runs on the OSS flow.
- Remaining web proposals; README (setup, `svkit.toml` reference, how to add an adapter); `pip install` packaging test.
**How to see it:**
```
cp -r fixtures/pipe5 fixtures/lib /tmp/x/ && cd /tmp/x/pipe5
export PATH=~/tools/bin:~/tools/oss-cad-suite/bin:$PATH; unset VERILATOR_ROOT
svkit sim --all --flow verilator; svkit synth --matrix --flow yosys; svkit sweep clock --tol 20 --jobs 4
svkit serve   # http://127.0.0.1:8470 ; remote: ssh -L 8470:localhost:8470 <host>
```

## Iteration 3 — 2026-11-02 — Real VCS/DC on CAEN, dogfood checkpoint, README
**Status:** done
**What works now:**
- `pytest -m synopsys` passes on real VCS X-2025.06 and DC V-2023.12 (counter, pipe5), with ASAP7 RVT libraries.
- pipe5 on VCS matches the Verilator results exactly: the same divergence line and cycle for the planted failing test, and the same perf counts.
- DC matrix (baseline / SS / RVT-only) and DC clock sweep on pipe5: T_min = 428 ps at tol 50, in 9 points.
- Real DC goldens: 4 reports + 1 log in `fixtures/golden/synopsys/real/`, cross-checked against the raw report text (WNS, area, power).
- Dogfood (copy of the 470 repo):
  - `svkit init` writes a correct config: `make {test}.out output_correct/{test}.wb`, `build_cmd`, waves, golden, the DC netlist and log, `ss_corner`/`ff_corner` experiments found from the library glob, the ASAP7 Vt map, and perf regexes learned from the testbench's `$fdisplay` format.
  - 21/21 tests pass on VCS.
  - The DC matrix runs at about 40 s per run.
  - DC clock sweep: T_min = 4708 ps (bracket 4672 fail / 4708 pass, tol 50). There were 14 points, from 40 s each at loose periods to about 8 min near T_min. Slack scales with period here (the project's I/O delays track the clock), so the T0 − WNS estimate undershoots and the grid refines from there.
  - TPI for `fib`: CPI 0.905 × 4708 ps = 4260 ps/instr (period source: the sweep).
  - The API works on the real CPU: hierarchy, block diagram, waves meta/search on a 5.4 µs VCD, perf/TPI, compare, and a gate cone with the critical path (32 cells, 0 unresolved points).
- README: install, quick start, the full `svkit.toml` reference, how to add an adapter, development, known limitations.
- Install test: a wheel built from a clean copy installs into a fresh venv with no Node; `svkit serve` serves `/api/project` and the dashboard.
- Tests: 201 passed, 15 skipped (OpenSTA-dependent) on CAEN, including the real VCS/DC end-to-end tests.
- Web API proposals 1 and 3: `GET /api/design/signal` (wave path → declaration file/line, via the DesignGraph) and an `X-Total-Count` header on `GET /api/runs`. The UI adopts them at the next frontend build.
**Decisions made (and why):**
- **CIFS homes:** `shutil.copy2`/`copytree` fail with EAGAIN on `listxattr`. Added `svkit.core.fsutil` (copies without xattrs, keeps mode and mtime) and used it everywhere.
- **The VCS adapter now creates output dirs** before running (shared `prepare_outputs`, as Verilator already did). The testbench `$fopen("output/...")` failed when `output/` was excluded from the copy.
- **DC "success" with unmapped logic, unresolved references or an unreadable target library is an error.** DC exits 0 in these cases and writes a netlist of generic cells.
- **report_reference parsing:** rows wrapped over 2–3 lines (name / library / numbers) are rejoined. Real ASAP7 names are long enough to wrap, so `cells_by_type` and `vt_mix` were empty.
- **Sweep:** if the loosest point meets setup but fails hold, the criterion becomes setup-only, with `SweepResult.criterion` and a `note`. The search also stops at 64× the start period. Real DC on pipe5 has −3.69 ps hold at every period (no `set_fix_hold`), and the sweep had grown the period to 3e20 ps before it was stopped.
- **Wrap mode:** an output the build did not rewrite (make: up to date) counts if it is newer than every source, with an info note. Older outputs are still stale. Re-running `sim --all` had failed every test that make skipped.
- **`init`:**
  - whole-line Makefile comments are ignored (`# make cpu.vg` became the netlist path)
  - goldens come from a `%.check: out/%.x ref/%.x` rule even when the reference dir is generated
  - sim-running pattern rules are preferred over `%.simv` / `%.verdi`
  - the DC log path comes from `cd dir && … | tee log`
  - corner experiments are found by globbing the library pattern with other corners
  - the Vt map comes from `-lib_cell` names
  - perf regexes handle number-before-label
  - a note warns about `-xprop=tmerge`
- OSS CAD Suite on CAEN was only partly extracted; it was re-extracted. RHEL 9 lacks Perl `FindBin`, so the core module is in `~/tools/perl5` (`PERL5LIB`), and conftest sets it.
- DC option B (toy Liberty via `read_lib`) does not work on CAEN. See `docs/CAEN.md`; option A (real `.db`) is the documented path.
- **Tool processes are stopped on Ctrl-C / SIGTERM / exit.** Tools run in their own session, so a Ctrl-C on `svkit sweep` never reached `dc_shell`, which kept running and kept its license. `RunContext.sh` now tracks live process groups and `svkit.cli:main` (the console entry point now) kills them. Checked end to end: SIGTERM to `svkit sim` stops the tool, and svkit exits 143.
- The DC adapter tests no longer read the ambient `SVKIT_DC_*` variables, which a CAEN shell exports for the real-DC tests.
- Follow-up (2026-11-03, after the tag), from the owner's lab7 project (a second real project; copy in `~/svkit-work/lab7`):
  - **Overlapping runs of one test raced:** three `sim --all` started 20 s apart deleted each other's `output/<t>.vpd`, so 7 tests failed with `exit code 2`. In-place sims now hold a per-test lock (`.svkit/locks/sim-<flow>-<test>.lock`); a second run waits.
  - **Opening a fresh Run DB from several processes at once** could fail with "database is locked" (the WAL switch ignores the busy timeout). It now retries.
  - **A non-zero exit plus a golden mismatch** is reported as the mismatch, with its divergence, plus the exit code. Check targets exit non-zero because their diff failed.
  - **`init` reads check rules that build their own reference** (`ref > $<.correct` + `diff $< $<.correct`): the per-test command becomes `make {test}.check`, with golden and waves set. Before, lab7 "passed" on make's exit code alone.
- Follow-up (2026-11-03, after the tag): project copies skip CIFS `.__smbXXXX` placeholders (left by a delete-while-open; listed, but opening fails). A stale one in a lab project made every `svkit synth` fail with FileNotFoundError.
**Known gaps / risks:**
- **Golden-diff jump on the dogfood:** `.wb` is a final memory image sorted by address, with no time or cycle, so the waveform cursor cannot be placed. svkit still reports the first differing line. Projects whose outputs carry a cycle work (`golden.time_regex`).
- **The web UI does not show `SweepResult.note` / `criterion` yet.** There is no usable Node on CAEN to rebuild the frontend; the CLI prints the note.
- **The OSS flow was not run on CAEN** (no OpenSTA here); it was validated on the dev host in iteration 2.
- **Toy library LC compliance:** clock-pin power tables and RN recovery/removal arcs need fixing before option B can work. This changes OSS power goldens, so it needs a host with `sta`.
- **Hold on the dogfood:** `470synth.tcl` reports only max-delay paths, so `hold_wns` is not reported and the sweep judges setup only by construction.
- Browser click-through still not done (no browser on CAEN from this session).
**Next iteration plan:**
- Owner decisions: OpenSTA install on CAEN, Icarus adapter, golden redistribution, remote/license.
- Web proposal 5 (early `run_ids` on `job.updated`) needs a group-first job API.
- With Node available: show the sweep note/criterion in the Sweep view, and implement the web API proposals (`/api/design/signal`, `X-Total-Count`, early `run_ids`).
- **Frontend source is ahead of the committed build:** the Waves view has **⇤ start / end ⇥** buttons (+ Home/End keys), requested by the owner 2026-11-03. The owner will rebuild on a laptop (`cd web && npm ci && npm run build && npm test`) and commit `src/svkit/server/static/`.
- Waves view (owner feedback 2026-11-03):
  - It opens with no signals; arriving from a failed test, it should preload useful ones.
  - A very long run opens fully zoomed out, where every signal is a grey "dense" band; it should open at a readable zoom near the start.
  - "Open waves at divergence" navigates away before its "could not be mapped to a sim time" message can be read.
- Design view: the "Gate cone" tab is disabled until a run is chosen, with only a hover tooltip. Make it open the latest synthesis run's worst path, or show an inline "pick a run" list (owner hit this on lab7, 2026-11-03).
- Fix the toy Liberty for LC, regenerate OSS goldens, and enable DC option B.
- Browser click-through of every view on the dogfood.
**How to see it:**
```
cd ~/svkit-work/dogfood
svkit runs ls                  # sims, matrix, sweep points
svkit serve                    # laptop: ssh -L 8470:localhost:8470 <caen host>, open http://localhost:8470
# Synopsys tests (CAEN): source the ASAP7 env block from docs/CAEN.md §2, then
.venv/bin/pytest -m synopsys
```

## Iteration 4 — 2026-11-04 — Laptop-friendly: OSS fallback, plain-language UI, unit-test suites
**Status:** done
**What works now:**
- **No Synopsys on the host? svkit falls back by itself.** A VCS Makefile flow runs the user's own build with a `vcs` stand-in that compiles with Verilator (DPI C, `$vcdplus*` rewritten in private copies, `-vpd2vcd` handled). DC falls back to builtin Yosys + OpenSTA. The header chip names the tool actually used ("sim: verilator (vcs not found)"). lab7 runs 146/146 on a laptop.
- **Dashboard speaks plain language.** "Worst setup slack" instead of WNS, "Fastest passing clock" instead of T_min, and so on. A small (i) button explains each term (hover/focus, click to pin), with the abbreviation inside. svkit logo in the header.
- **Unit tests are their own suite.** With `[flows.sim.unit] testbench = "test/{test}_test.sv"`, every unit testbench is a test, shown on the Tests page under **Unit tests**, apart from **System tests**. Runs have the same waveforms, logs, history and run page. svkit dumps a waveform of the whole testbench without editing it. A failing unit test opens its waveform at the time of the first `ERROR` line, with the testbench's signals already added. `svkit init` detects standalone testbenches and writes the suite. On lab7, `queue` fails at 111 ns (`full=0, expected 1`), which is visible straight from the Tests page.
- Job errors show a one-line summary plus the full message (tool hint, build-log tail). Build/infra failures are rerun instead of cached. Cache hits point at the original run and show its log.
**Decisions made (and why):**
- Fallback order and labelling: PRODUCT §13 (2026-11-03), CONTEXT #21. Unit suites: PRODUCT §13 (2026-11-04), CONTEXT #22.
- Suites are derived from flows (`suite`, else unit/system by `testbench`), not a new top-level table: an existing flow already has everything a suite needs (tests, simulator, pass rules). Fallback swaps flows only within a suite, and `svkit sim` with no `--flow` takes the first system flow, so adding a unit flow never changes what existing commands run.
- New `SimFlow.adapter`: the flow key no longer has to be the adapter name (`[flows.sim.unit] adapter = "vcs"`).
- Waveform for unit testbenches: a generated module `bind`-ed into the top calls `$dumpvars` on the whole design into `+svkit_waves=<run_dir>/waves.vcd`. This works the same on VCS and Verilator, keeps the user's files untouched, and does not change the build hash per run. It is skipped when the testbench dumps waves itself.
- The first error comes from the log, since unit testbenches rarely have golden files: `Divergence.source = "log"`, first line matching `error_regex` or a generic ERROR/FAIL/mismatch pattern. The time is read from `[t]`, `@t` or `time=t`. `%t` prints in time-precision units, which match the VCD timescale (checked on Verilator: ps).
- Run DB v2 adds `runs.flow`, migrated in place with `ALTER TABLE`. Older runs (NULL) belong to the default suite.
- `init` picks the system testbench by "named in the Makefile" before size, so an equally sized unit testbench is not mistaken for it.
- Waves opened from a first-error or first-mismatch link add the top scope's own signals (up to 32) via `auto=top` (owner feedback 2026-11-03: "opens with no signals").
**Known gaps / risks:**
- **The VCS path of unit suites is not run for real yet** (no VCS on this host). `tests/test_unit_suite.py::test_unit_suite_on_vcs` (`-m synopsys`) must pass on CAEN.
- On Verilator, unit tests are 2-state like every Verilator run: X bugs in a unit DUT need VCS (or the open Icarus decision).
- The generic first-error pattern can pick a line that merely mentions "error" (e.g. a phase banner). Set `error_regex` for such testbenches.
- Owner items from iteration 3 still stand (OpenSTA on CAEN, Icarus, golden redistribution, remote/license).
**Next iteration plan:**
- On CAEN: `pytest -m synopsys` (including the unit suite on VCS); dogfood: add unit testbenches if the project has any.
- Waves: open long runs at a readable zoom; keep the "could not be mapped" message readable.
- Design view: make the Gate cone tab usable without picking a run first.
- Remaining web API proposals; browser click-through of every view.
**How to see it:**
```
cd lab7 && svkit serve           # restart it: the backend changed
# open http://localhost:8470/#/tests/unit -> queue (failed) -> "Waves at first error"
svkit sim --all --flow unit      # same from the CLI
.venv/bin/pytest tests/test_unit_suite.py
```

## Iteration 5 — 2026-11-04 — Compact waveform archive (FST by default, reversible)
**Status:** done
**What works now:**
- `.svkit` no longer grows by the full size of every VCD. Each run's waveform copy is stored as FST, and the waves view, X tracer and first-error/first-mismatch jumps read it as before. On a copy of lab7's run history: 291 waveforms, **607 MB → 17 MB** (`.svkit` 601 → 39 MB) in 8 s. Per run, conversion adds about 0.2–0.5 s.
- Reversible: `[waves] archive = "vcd"` stores new runs as VCD again, and `svkit waves archive --to vcd` (or `--to fst`) converts recorded runs, with `--dry-run` to preview.
**Decisions made (and why):**
- Product decision: PRODUCT §13 (2026-11-04, waveform archive), CONTEXT #23.
- Conversion happens once, in the runner after the adapter returns, not in each adapter. Every sim adapter (VCS, Verilator, the shim, plugins) gets it for free, and everything computed from the VCD (`sim_time`) is already done.
- Accept-only-if-verified: the FST must open and end at the VCD's last timestamp, or the VCD is kept with an info warning. A run never loses its waveform to a bad conversion.
- Cache hits point at the original run's files (`../r_x/waves.*`). `waves archive` rewrites those references as well, so reused runs keep their waves.
- External tools, no new Python dependency: `vcd2fst`/`fst2vcd` from GTKWave / OSS CAD Suite (located like the other OSS tools; `[tools].vcd2fst` overrides).
**Known gaps / risks:**
- Hosts without `vcd2fst` (possibly CAEN) keep VCDs. It works, but without the savings; the run log says so.
- No "download as VCD" button yet for taking an archived FST into Verdi; use `fst2vcd` or `svkit waves archive --to vcd`.
- No retention policy yet (keep failed + latest waves, size budget, `svkit gc`). With FST it is far less urgent.
- Follow-up (2026-11-04): a failed synthesis or STA run's message now quotes the tool's first error line with a project-relative path, e.g. `yosys failed (exit 1): verilog/queue.sv:39: ERROR: syntax error, unexpected ';'`. Before, it said only "see artifact synth.log". Tool failures no longer carry a Python exception name (`RuntimeError:`); unexpected svkit errors still do.
**Next iteration plan:**
- As in iteration 4, plus a retention / `svkit gc` command if `.svkit` still grows too much.
**How to see it:**
```
svkit waves archive --to fst --dry-run    # what an existing project would save
svkit waves archive --to fst              # shrink it (a running svkit serve picks it up)
```

## Iteration 6 — 2026-11-04 — Schematic diagrams: netlistsvg for Blocks and Gates
**Status:** done
**What works now:**
- Design → **Blocks** and **Gates** are drawn as real schematics by netlistsvg. Gates get gate symbols (AND/NAND/OR/NOR up to 4 inputs, XOR/XNOR, inverter, buffer, 2:1 mux, flip-flop with or without reset, latch). Module instances, glue logic and unrecognised library cells are boxes with their port names. Module ports are pin shapes, buses are thick wires, and splits/joins are drawn where a bus is sliced.
- The critical path is red, cells and wires, with each cell's delay under it. Hover any cell for its instance name, cell type or module, and delay. The "all instance names" toggle shows every gate's name (by default only the path's).
- Blocks: click an instance (or Tab to it and press Enter) to descend. Fit / 100 % / ± zoom. Light and dark themes.
- The layout runs in a Web Worker: a 970-cell cone (pipe5, depth 40) lays out in about 6 s without freezing the page. Leaving the view or changing the cone cancels a running layout.
- On the pipe5 OSS run (svkit_tiny Liberty), 28 of the 42 cells in the depth-4 critical cone are drawn as symbols; the 14 boxes are AOI21/OAI21.
- Follow-up: the **Gates** tab is always clickable. Opened directly, it shows the newest finished synthesis run's worst path, or explains how to get a synthesis run if there is none. A run picker in the cone toolbar switches to another run.
- Follow-up: a **theme switch** in the header cycles Auto (follow the OS) → Light → Dark. It is remembered per browser (localStorage) and applied before the first paint; charts and the wave canvas follow it.
- Follow-up: **Ctrl/⌘ + scroll (or trackpad pinch) zooms the diagram** around the cursor instead of zooming the whole page. A plain scroll still scrolls.
**Decisions made (and why):**
- Owner request: netlistsvg draws both diagrams, using elkjs 0.3.0 (its own dependency), our original skin (`web/src/lib/schematic-skin.svg`), DOMPurify on its output, and a Web Worker (`schematic.worker.ts`). This replaces the hand-drawn ELK 0.11 diagrams (`web/src/lib/graph.ts`, removed, with the `elkjs` 0.11 dependency). The stack line "ELK.js for diagrams" still holds: netlistsvg lays out with ELK.
- The server sends netlistsvg's input format (one Yosys-JSON module), so the browser has a single drawing path. New endpoints are `/api/design/schematic/{block,cone}` (ARCHITECTURE §6). The old `/api/design/{block,cone}` stay unchanged as the analysis contract.
- Gate symbols are found by the cell's logic, not its name. svkit evaluates the truth table of the Liberty `function` and matches it to and/nand/or/nor/xor/xnor/buf/not/mux, and reads `ff`/`latch` groups for flops and latches. This works for any library. Without Liberty it falls back to the name convention (`nand3`, `MUX21`, `DFF`, sky130 `__` prefixes). Anything ambiguous is a box, never a wrong symbol. Yosys internal gates (`$_AND_` …) are drawn as they are.
- Box width comes from the longest port label. The skin has five widths (`$svkit_box_100`…`380`) because netlistsvg sizes boxes per template.
- Three Node-only modules that netlistsvg/elkjs require get small browser shims (`web/shims/`: `assert`, `stream`, `webworker-threads`). `schematic.guard.ts` gives elkjs 0.3.0 a placeholder `document`; otherwise, inside a worker, it takes over `onmessage` as if it were ELK's own worker, and `new ELK()` fails.
- The skin carries no colours. The page styles its classes from the theme tokens, and DOMPurify drops `<style>`, scripts, links and foreign content.
**Known gaps / risks:**
- netlistsvg highlights whole nets: when a path net also fans out to cells off the path, those branches are red too. Path cells (red outline + delay) are exact.
- Deep cones are slow to lay out (about 6 s at 1000 cells) and dense to read. There is no drag-to-pan or minimap yet; scroll and zoom.
- Complex cells (AOI/OAI, full adders, scan flops, flops with QN outputs) are boxes, with no dedicated symbols yet.
- Licences of the bundled UI: netlistsvg is MIT, DOMPurify is MPL-2.0/Apache-2.0, and elkjs is EPL-2.0 (as before). Ship third-party notices with the built assets when the repo licence is chosen (owner item 4). `npm audit` flags old dependencies of netlistsvg's CLI (`yargs`, `json5`), which are not in the browser bundle.
**Next iteration plan:**
- Drag-to-pan and zoom-to-cursor in the Design view; a "focus path" mode that hides off-path cells in big cones.
- AOI/OAI symbols, if the boxes read badly in practice.
- Items from iterations 4–5 (CAEN `-m synopsys` run, retention).
**How to see it:**
```
svkit serve                          # restart it: new API endpoints and UI
# Design -> Blocks (click an instance); Synthesis -> a run -> a timing path -> "Show gates"
.venv/bin/pytest tests/test_schematic.py && (cd web && npx vitest run src/lib/schematic.test.ts)
```
