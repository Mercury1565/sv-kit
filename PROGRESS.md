# svkit progress

## Owner summary
- **Phase:** Phases 0–3 are implemented and pass on the open-source flow. **~65 % to MVP.** Remaining: real VCS/DC validation, a dogfood run, an X-trace sim path for the OSS flow, hardening and README.
- **Continuing on CAEN:** step-by-step handoff in [`docs/CAEN.md`](docs/CAEN.md) (transfer, env, Synopsys tests, real goldens, dogfood).
- **Demoable (OSS flow, any fixture copy):** `svkit sim --all`, `svkit synth --matrix` (compare table), `svkit sweep clock` (T_min), `svkit xtrace`, `svkit serve` (Runs, Tests, Synthesis, Compare, Sweep, Waves, Perf, Design, X-Trace views).
- **Needs owner:**
  1. **Dev host is not CAEN.** No Synopsys tools or dogfood repo here; the VCS/DC adapters are tested only against hand-written report samples and fake tools. Real VCS/DC runs and the dogfood checkpoint need a CAEN session.
  2. **X-trace parity on the OSS flow:** Verilator is 2-state, so it never shows X. Recommend pulling an Icarus sim adapter into the MVP (scope change vs decision #18).
  3. Synopsys-generated goldens (none committed yet) need a redistribution check before the repo goes public.
  4. Git hosting, remote and license are still open (#7, #12).
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
