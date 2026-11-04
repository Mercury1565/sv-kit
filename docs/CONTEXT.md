# Project context: how svkit started

This file carries over the conversation that created this project (2026-11-02), so work can continue in any later session. The product spec lives in [`../PRODUCT.md`](../PRODUCT.md). The first approved plan is kept for reference in [`2026-11-02-initial-plan.md`](2026-11-02-initial-plan.md).

---

## 1. The original request (author's own words)

> My experience so far with debugging, visualizing, and fixing my verilog is so terrible. It always feels like I have to make a lot more effort to figure out what went wrong and to come up with a fix for that. I am thinking of developing a tool kit which can serve as an aid for the designer. Some of the things I expect from the toolkit are the follows:
> - better way to visualise waveforms
> - if in need, draw the gate level or block diagram of my design
> - better way to find out what is breaking, how "x" values are popping up?
> - better way to find the lowest clock period without causing negative slack
> - better way to see all the essential info found on the .rep file of synthesis and ways to compare them when we apply different optimization like high area effort, mixed threshold, constrained fanout, slow slow corner...
> - what the CPI, TPI situation looks like
> - some more convenience features you may think of.
>
> There has to be a better way to interact with the product besides just seeing log files and terminal outputs on our code editor.

And the follow-up:

> I don't mean this tool to be used by this project, I am thinking a rather generic toolkit that can easily be plugged into any sv project and start using it.

## 2. Decisions so far

| # | Question | Answer | Status |
|---|---|---|---|
| 1 | Main interface | **Local web app + CLI** now; **TUI later** (author likes the TUI idea) | Confirmed |
| 2 | Toolchains | **Synopsys (VCS/Verdi/DC) and open-source (Verilator/Icarus/Yosys/OpenSTA) equally from day one** | Confirmed |
| 3 | MVP features | **All four:** X-propagation tracer, synthesis report dashboard + compare, clock-period sweep, waveform viewer + CPI/TPI | Confirmed |
| 4 | Project location | **`/home/hermong/svkit`** (a separate project, not part of any course repo) | Confirmed |
| 5 | Open-source EDA tools | Author delegated: "use your best judgement". **OSS CAD Suite (2026-11-02 build) installed to `~/tools/oss-cad-suite`** | Done |
| 6 | Name | `svkit`, working title | Open |
| 7 | Git hosting (GitHub? public/private) | not answered yet | Open |
| 8 | Minimum Python | **3.11** (`requires-python >= 3.11`); dev venv uses whatever ≥3.11 the host has (3.12 on the current dev host) | Decided (lead, iter 1) |
| 9 | Frontend build | **Prebuilt static assets committed under `src/svkit/server/static/`**; Node needed only for development | Decided (lead, iter 1) |
| 10 | Synthesis knob mechanics | Isolated work dir (full copy) per synth run; knobs via `file` / `env` / `tcl_var` / `tcl_replace` / `tcl_append` / `define`. `tcl_var` is injected at the top of the work-dir copy of the script and the user's own `set var default` is guarded with `info exists` (a pure `source` wrapper would be overridden by that line). See ARCHITECTURE.md §2.3 | Decided (lead, iter 1) |
| 11 | Wrap vs. generate build scripts | **Wrap is the default** (`mode = "wrap"` runs the user's command). Adapters also have `mode = "builtin"` that invokes the tool directly in an isolated work dir; svkit never writes build files into the user's repo | Decided (lead, iter 1) |
| 12 | License | can wait until public (MIT is the easy default) | Deferred |
| 13 | Surfer embed vs. own wave renderer | decide in Phase 2 after prototyping on `pywellen` | Deferred |
| 14 | Gate-level X tracing with SDF | Phase 3+ | Deferred |
| 15 | MVP scope document | **[`MVP.md`](MVP.md)** in the repo | Confirmed |
| 16 | OpenSTA in the MVP | **Yes**, so the open-source flow has timing for sweep, WNS, and the critical path | Confirmed |
| 17 | Experiments in the MVP | **Yes**, `svkit synth --exp` / `--matrix` | Confirmed |
| 18 | MVP simulators | **VCS + Verilator**; Icarus after the MVP | Confirmed |
| 19 | Golden-diff jump: line → sim time | `golden.time_regex` captures a time (or cycle, `time_unit = "cycle"`) from the first mismatching actual line | Decided (lead, iter 1) |
| 20 | Liberty for the open-source fixtures | An original toy library `fixtures/lib/svkit_tiny.lib` (with `_R`/`_L` Vt variants), so results are deterministic and redistributable | Decided (lead, iter 1) |
| 21 | Missing tools on the host | Automatic open-source fallback (other configured flow → `vcs` stand-in on Verilator → builtin Yosys + OpenSTA), labelled on every run; `[flows].fallback = false` turns it off | Decided (lead, iter 3) |
| 22 | Unit tests | Separate suite per `[flows.sim.<name>].testbench` (one testbench file per test), listed apart from system tests with the same waves / logs / run pages; jump to the first failed check in the log | Decided (lead, iter 3) |

## 3. Development environment findings (CAEN Linux, RHEL 9)

| Item | Finding |
|---|---|
| Python | default `python3` = **3.9.25**; **`/usr/bin/python3.11`** present; `module load python/3.13` also available |
| pip (checked 2026-11-02) | available: `pyslang 12.1.0`, `pywellen 0.25.7`, `fastapi 0.162.0`, `textual 8.2.8`, `yowasp-yosys` |
| Node | only `module load node.js/v10.14.1`, **too old** for a modern React build; install a user-local Node for frontend development or build on a laptop |
| Synopsys | `vcs`, `dc_shell-t` at `/usr/caen/bin`; Verdi not on PATH by default |
| Open source | none system-wide, so OSS CAD Suite is installed at `~/tools/oss-cad-suite` (activate: `source ~/tools/oss-cad-suite/environment`) |
| No root | everything installs to `~` (pip `--user` / venv) |

## 4. First dogfood target: EECS 470 project 3 (5-stage LC2K pipeline)

Path: `/home/hermong/eecs470/470_proj-f26.group4`. svkit must stay generic, but this repo is a realistic first test. Facts `svkit init` should detect:

- **Makefile targets:**
  - `make <prog>.out` (sim on `build/simv`), `.hsyn` / `.syn` (half and full gate-level sim)
  - `make <prog>.check` (diff `.wb` against `output_correct/`), `make cpu.vg` / `stage_*.vg` (synthesis), `make <prog>.verdi`
- **Sim:** VCS with `-sverilog -xprop=tmerge -debug_access+all+reverse -kdb -lca`. The testbench writes `output/<prog>.{out,cpi,wb,rwb,ppln,fmem,vpd}`, and the VPD is converted with `vcs -vpd2vcd +splitpacked`.
- **Clock period:**
  - set in the file `cpu.clock` (currently `40000`, in ps)
  - exported as `CLOCK_PERIOD` to `dc_shell-t`
  - passed to VCS as `+define+CLOCK_PERIOD`
- **Synthesis:** `470synth.tcl` via `dc_shell-t`
  - **Library:**
    - ASAP7 7.5-track RVT, corner `TT` (`set corner TT`); cells named `*_ASAP7_75t_R`
    - SAED32 wire-load model
  - **Constraints:** `compile -map_effort high -area_effort none`, with `compile_ultra` commented out. `set_max_fanout 8` is also commented out.
  - **`.rep` contents** (in order): `report_design`, `report_timing -max_paths 2`, `report_constraints -all_violators`, `report_power`, `report_resources -hier`, `report_area -hierarchy`, `report_reference -hierarchy`
  - **Outputs:** `synth/<module>.{vg,rep,ddc,chk}` and `synth/<module>-synth.out`
- **Tests:** `programs/*.lc2k`, assembled to `.mem`. A reference simulator `lc2k-sim.exe` produces the golden `.wb`.

These show which knobs a real project uses (`corner` TCL var, compile command line, commented-out constraints, a clock-period file plus env var). They are the concrete cases for decision #10.

## 5. Next step

Phase 0 (foundation), per `PRODUCT.md` §10:
1. Python package skeleton.
2. `svkit.toml` loader and `svkit init` auto-detect.
3. Run DB (sqlite).
4. Job runner.
5. Adapter interfaces.
6. Fixture corpus of tiny designs runnable on both VCS/DC and Verilator/Yosys/OpenSTA.
7. Server and web shell.

Confirm decisions 6–11 before or early in Phase 0.
