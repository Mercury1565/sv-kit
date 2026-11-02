# svkit progress

## Owner summary
- **Phase:** 0 (Foundation). Iteration 1 done: contracts. **~8 % to MVP.**
- **Demoable:** nothing user-visible yet; `pytest` covers config, knobs, Run DB, golden diff, the API skeleton.
- **Needs owner:**
  1. **Dev host is not CAEN.** This session runs on host `crab` (user `sitotaem`) with no Synopsys tools and no dogfood repo (`/home/hermong/...` does not exist). Synopsys-marked tests skip here, and the DC/VCS parsers are built against hand-written report samples. Real VCS/DC runs and the dogfood checkpoint need a CAEN session.
  2. Synopsys-generated golden reports (`fixtures/golden/synopsys/`) need a redistribution check before the repo goes public.
  3. Git hosting, remote, and license are still open (#7, #12).
  4. **X-trace parity on the open-source flow.** Verilator is 2-state, so it never produces X, and the X tracer has nothing to trace on the OSS flow. A 4-state simulator is needed. Recommendation: pull the Icarus sim adapter (small, already in OSS CAD Suite) into the MVP for X-trace runs. That is an MVP scope change (decision #18), so it is waiting for you. Meanwhile the X fixtures' goldens are generated with iverilog as test data only.

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
