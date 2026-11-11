# svkit architecture and contracts

> Status: v1 contracts, iteration 1 (2026-11-02). The sections marked **contract** are shared between
> work tracks. Track agents must not change them; propose a change to the lead, who updates this file
> and the code together.

## 1. Layout

```
src/svkit/
  schemas.py          contract: normalized result models (pydantic)           §3
  config.py           contract: svkit.toml schema + loader                    §2
  core/
    rundb.py          contract: Run DB (sqlite) + artifact dirs               §5
    knobs.py          knob values -> KnobPlan; applies plans in work dirs     §2.3
    project.py        sources, filelists, git info, source hash, work dirs
    golden.py         pass/fail: golden diff + log regexes, first divergence
    runner.py         job runner: cache, isolation, license seats, events
    events.py         in-process pub/sub feeding the WebSocket
  adapters/
    base.py           contract: adapter interface                             §4
    registry.py       built-ins + entry points
    sim/  verilator.py vcs.py
    synth/ yosys.py dc.py           (+ report parsers in synth/dc_reports.py etc.)
    sta/  opensta.py
    waves/ wellen.py                VCD/FST access through pywellen
    perf/ regex.py                  [perf].source = regex_file
  design/             pyslang -> DesignGraph (hierarchy, types, drivers); netlist reader
  analysis/           sweep.py compare.py perf.py xtrace.py divergence.py cone.py
  server/
    app.py            contract: REST + WebSocket API                          §6
    static/           built web UI (committed; end users never need Node)
  cli.py              Typer CLI                                               §7
  init.py             `svkit init` project scanner
web/                  React + TS + Vite source; `npm run build` writes into src/svkit/server/static
fixtures/             original designs, one dir per fixture, each a mini "user project"   §8
tests/                pytest; markers `oss`, `synopsys`
```

Data flow: **CLI / server → Runner → Adapter.run() → normalized schema → Run DB → server → UI.**
The UI never sees tool output, only schemas.

## 2. `svkit.toml` (contract — `src/svkit/config.py`)

The full field reference is the pydantic model; the README carries the user-facing reference.
Summary:

| Table | Purpose |
|---|---|
| `[project]` | `name`, `top`, `tb_top`, `sources`/`tb_sources` (`glob`, `filelist`, `exclude`), `include`, `defines`, `clock`, `reset`, `workdir_exclude` |
| `[flows.sim.<name>]` | `mode = "wrap"` (run `cmd`, the user's build) or `"builtin"` (adapter compiles sources). `adapter` (default: the key), `waves`, `log`, `golden`, `tests` (`glob`, `names`, `exclude`), `pass_regex`, `fail_regex`, `isolate`. Unit suites: `testbench` (template with `{test}`: one file per test), `top`, `sources`, `suite`, `error_regex` (§2.1b) |
| `[flows.synth.<adapter>]` | `mode`, `cmd`, `script` (user's TCL/ys), `reports`, `netlist`, `log`, `license_seats`, `sta` (name of an STA flow), `append_before` (anchor regex for experiment `tcl_append`) |
| `[flows.sta.<adapter>]` | `mode`, `sdc`, `max_paths` |
| `[flows]` | `default_sim`, `default_synth`, `fallback` (default `true`: run open-source stand-ins when a flow's tools are missing, §2.1a) |
| `[knobs]` | `name = { <mechanism> = ..., unit?, format?, default?, applies_to? }` |
| `[experiments]` | `name = { knob = value, ..., tcl_append? = "...", env? = {...} }` |
| `[perf]` | `source = { regex_file, cycles, instrs, stalls? }` |
| `[library]` | `vt_suffix = { suffix = class }`, `liberty = [...]`, `corners = { name = [libs] }` |
| `[tools]` | executable overrides: `sta`, `yosys`, `verilator`, `vcs`, `dc_shell-t`, `vpd2vcd`, `fsdb2vcd` |

**Templates.** String fields like `cmd`, `waves`, `golden.actual` accept `{test}`, `{top}`, `{root}`,
`{workdir}`, `{run_dir}`; knob mechanisms accept `{value}`. Unknown placeholders are left intact.

### 2.1 Modes
* **wrap** (preferred): svkit runs the user's own command (`make foo.out`) and reads the files it
  produces. Never edits the Makefile or scripts.
* **builtin**: the adapter invokes the tool directly with `[project]` sources in an isolated work dir
  (useful for projects with no build, and for the fixtures). svkit never writes build scripts into
  the user's repo (decision #11 interpretation: "wrap only" = never generate *the user's* build files).

### 2.1a Missing tools: open-source fallback (`svkit.compat`, decision #21)
`Runner` resolves the flow a job really runs (`resolve_sim` / `resolve_synth`), unless
`[flows].fallback = false`:
1. The flow's tools are on PATH: run it.
2. No `--flow` given: the first other configured flow whose tools are present.
3. A VCS **wrap** flow: run the user's own command with `.svkit/shims/bin/vcs` first on PATH. That
   stand-in (`svkit.compat.vcs_shim`) translates the vcs command line into `verilator --binary`.
   - It handles sources, `-f`, `+define+`, `+incdir+`, `-top`, `-timescale` and `-o`. DPI `.c`
     files are compiled with the C compiler.
   - VCS-only dump tasks (`$vcdplus*`, `$fsdb*`) are rewritten to `$dump*` in private copies of the
     sources.
   - `vcs -vpd2vcd` copies the (already VCD) dump.
   - A VCS builtin flow uses the Verilator adapter.
4. A DC flow: builtin Yosys + OpenSTA on `[library].liberty`, else on the packaged generic demo
   library (`svkit/data/svkit_tiny.lib`).

Every such run:
- records the backend actually used as `Run.adapter`;
- carries an info warning `FALLBACK` (and `SynthResult.extra.fallback`);
- has its own cache key.

`GET /api/project` returns `backends` (what each kind runs on this host). The CLI prints a note.

### 2.1c Waveform archive (`[waves].archive`)

After a sim run, the runner (`svkit.core.wavestore.archive_run`) stores the waveform the adapter
kept (`SimResult.waves`) in the configured format. The default is `"fst"`: `vcd2fst` writes a
temp file, which is accepted only if it opens and ends at the VCD's last timestamp. Then
`waves.vcd` is replaced by `waves.fst` in the artifacts and in `SimResult.waves`. `"vcd"` keeps
VCDs as produced. Failures keep the VCD and add an info warning (`WAVES`); symlinked waves (over
`max_size_mb`) are left alone. `svkit waves archive --to fst|vcd [--dry-run]` converts recorded
runs (`fst2vcd -f in -o out` for the way back) and rewrites the artifact references of cache-hit
runs (`../<orig>/waves.*`). Readers use `wave_path()`, which accepts both formats.

### 2.1b Test suites (unit tests)

Sim flows are grouped into **suites**: `suite` if set, else `"unit"` for a flow with a
`testbench` template and `"system"` otherwise. The Tests page lists each suite separately. A suite
runs on its first flow, and tool fallback (§2.1a) only swaps flows **within** a suite, so unit
tests never run as system tests. `svkit sim` without `--flow` runs the first system flow.

A unit flow's tests are the `{test}` part of every file matching `testbench`, minus `tests.exclude`
and the files of `[project].tb_sources`. In builtin mode (`svkit.core.unit`), each test compiles
`[project].sources` + `sources` + its testbench file. The top is `top`, or the module declared in
the file that nothing in it instantiates. Unless the testbench dumps waves itself
(`$dumpfile`/`$dumpvars`/`$vcdplus*`/`$fsdb*`), svkit compiles a generated `svkit_waves` module,
bound into the top, that runs `$dumpvars` on the whole design into `+svkit_waves=<run_dir>/waves.vcd`.
It lives in `.svkit/build/unit/`, so user files are never edited, and it works the same on VCS
and Verilator. A failed test without a golden divergence gets a log divergence
(`Divergence.source = "log"`): the first log line matching `error_regex` (or a generic
ERROR/FAIL/mismatch pattern), with its time taken from the named group `time` or from a `[123]`,
`@123` or `time=123` token, in waveform ticks.

Runs record their flow (`runs.flow`, DB v2; NULL on older runs = the default flow), so the
dashboard assigns each run to its suite.

Working open-source tools are located on PATH and in common user install dirs (`~/tools/bin`,
`~/tools/oss-cad-suite/bin`, `$SVKIT_TOOL_PATH`, …). For Verilator, version 5+ is required, and a
stale `VERILATOR_ROOT` is ignored. Wrap commands whose Makefile sets
`SHELL := $(SHELL) <bash opts>` get `MAKEFLAGS=SHELL=<bash wrapper>` on hosts where /bin/sh is not
bash.

### 2.2 Isolation
Synthesis runs always execute in `.svkit/work/<run_id>/`, a full **copy** (not hard links) of the
project minus `.git`, `.svkit`, `[project].workdir_exclude`. Sim runs execute in place unless
`isolate = true` or a `file` knob applies. Artifacts are copied to `.svkit/runs/<id>/` with
`RunContext.keep()`; work dirs are deleted afterwards.

### 2.3 Knob mechanics (decision #10)
`knobs.resolve()` merges defaults < experiment < `--set`; `knobs.plan()` turns values into a `KnobPlan`:

| Mechanism | Effect (always inside the work dir) |
|---|---|
| `file = "cpu.clock"` | write the rendered value to that file |
| `env = "CLOCK_PERIOD"` | export the variable |
| `tcl_var = "corner"` | inject `set corner <v>` at the top of the work-dir copy of `script`; the user's own plain `set corner TT` line is guarded with `if {![info exists corner]}` |
| `tcl_replace = "<regex>", with = "...{value}..."` | regex replace in the copy (last resort; errors if nothing matches) |
| `tcl_append = true` (value = TCL) / experiment `tcl_append = "..."` | append to the copy (or before `before` regex anchor) |
| `define = "NAME"` | Verilog define for sim |

Time knobs: values are always **ps** inside svkit; `unit = "ns"` converts when rendering.
The knob named **`clock_period`** is special: the sweep drives it, and builtin flows read
`KnobPlan.period_ps` to constrain the clock.

## 3. Normalized schemas (contract — `src/svkit/schemas.py`)

Units: time/slack/period **ps**, power **mW**, area = library unit (`area_unit`), wave time = ticks of
the wave file's timescale (reported by the wave API).

* `SynthResult`: tool, top, period, corner, wns, tns, hold_wns, hold_tns, area_total, area_by_hier,
  cell_count, cells_by_type, vt_mix, power{dynamic,leakage,total}, paths[TimingPath], warnings[Msg],
  netlist, extra. `.met` = setup WNS ≥ 0 and hold WNS ≥ 0.
* `TimingPath`: startpoint, endpoint, path_group, path_type (max/min), slack, required, arrival,
  points[TimingPoint{inst, pin, cell, incr, arrival, rise_fall, net}].
* `SimResult`: tool, test, passed, reason, sim_time, waves (artifact name), divergence, perf, warnings.
* `Divergence`: line, expected, actual, time?, cycle?.
* `PerfResult`: test, cycles, instrs, cpi, stalls, period, period_source, tpi (ps/instr).
* `XTraceNode`: signal, time, value, driver{kind, file, line, text}, cause (XCause), note, children.
* `CompareResult` / `MetricDelta`, `SweepResult` / `SweepPoint`.
  `SweepResult.criterion` is `setup+hold` by default. It becomes `setup` when the loosest point meets
  setup but still fails hold: hold does not depend on the period, so a looser clock cannot fix it.
  `SweepResult.note` says why the criterion changed, or why the search stopped early (nothing passes
  at 64× the start period).

Hierarchy separator: `area_by_hier` keys and `TimingPoint.inst` use `/` (as DC and OpenSTA print it);
waveform signal paths use `.`.

Vt mix rule: a cell counts as class C if its name ends with a `[library].vt_suffix` key mapping to C
(longest suffix wins); otherwise `"other"`.

## 4. Adapter interface (contract — `src/svkit/adapters/base.py`)

```python
class SimAdapter(Adapter):   name, kind="sim",   tools=(...)
    run(ctx: RunContext, flow: SimFlow, test: str) -> SimResult
class SynthAdapter(Adapter): name, kind="synth", tools=(...)
    run(ctx, flow: SynthFlow) -> SynthResult       # runs flow.sta adapter itself if set
    parse(files: list[Path], *, top) -> SynthResult  # offline, golden-testable
class StaAdapter(Adapter):   name, kind="sta",   tools=(...)
    run(ctx, flow: StaFlow, *, netlist, top, liberty, period_ps) -> SynthResult  # timing fields only
    parse_timing(text) -> list[TimingPath]
```

`RunContext` gives `cfg, root, workdir, run_dir, run_id, plan (KnobPlan), env, log(), cancel,
keep(path, name)` and `sh(cmd, ...)` (streams output to the live log, honours cancel/timeout, runs
`nice`d). Adapters:

* never touch the Run DB or the UI, never write outside `workdir`/`run_dir`;
* keep() the waveform as artifact `waves.vcd`/`waves.fst`, the log as `sim.log`/`synth.log`,
  netlist as `netlist.v`/`netlist.json`, reports under their own names;
* sim adapters use `svkit.core.golden.evaluate()` for pass/fail so behaviour is identical across flows;
* synth adapters fill `vt_mix` with `svkit.analysis.vt.vt_mix(cells_by_type, cfg.library.vt_suffix)`.

Third-party adapters: `[project.entry-points."svkit.adapters"] myname = "pkg.mod:Class"`.

## 5. Run DB (contract — `src/svkit/core/rundb.py`)

`.svkit/runs.sqlite` (WAL) + `.svkit/runs/<id>/`. Tables: `runs`, `tags`, `groups`, `meta`.

* `runs`: id (`r_0001`), kind (`sim`|`synth`|`sweep-point`), adapter, test, status
  (`queued|running|passed|failed|done|error|cancelled`), timestamps, duration_s, git_sha, git_dirty,
  source_hash, knobs (JSON), experiment, cache_key, cached_from, exit_code, metrics (JSON of the
  normalized result), artifacts (JSON name→path relative to the run dir), group_id, message, flow
  (v2), owner (v3), name (v4: the user's own name, one line, ≤ 80 chars, NULL when unnamed).
* `groups`: id (`g_0001`), kind (`sim-all|matrix|sweep`), status, params, result (e.g. SweepResult),
  name (v4). A `sim-all` batch's params hold `tests` (the ones it ran) and `subset` (true when that
  is not every test of the flow).
* Older databases are upgraded in place on open (`ALTER TABLE … ADD COLUMN`).
* Cache key = sha256(kind, adapter, source hash, knob values, test). Source hash covers design + tb
  sources, include-dir headers, `svkit.toml`, Makefile, and the flow script. A cache hit creates a
  new Run with `cached_from` and artifacts pointing at the original run.

## 6. Server API (contract — `src/svkit/server/app.py`)

All JSON. Base `/api`. Errors: `{"detail": "..."}` with 4xx/5xx.

| Method & path | Returns |
|---|---|
| `GET /api/project` | `{name, root, top, git_sha, git_dirty, flows:{sim:[..], synth:[..], sta:[..]}, knobs:[..], experiments:[..], tests:[..], adapters:{name: available}, backends, suites:[{id, label, flow, flows, tests, unit, golden, default}]}` (`tests` = the default suite's) |
| `GET /api/runs?kind=&group=&status=&test=&tag=&limit=&offset=` | `[RunSummary]` (Run without metrics); header `X-Total-Count` = matching runs before limit/offset |
| `GET /api/runs/{id}` | `Run` with `metrics` |
| `GET /api/runs/{id}/artifacts/{name}` | the file (text/binary) |
| `GET /api/runs/{id}/log?tail=N` | `{lines:[...]}` |
| `POST /api/runs/{id}/tags` `{tag}` / `DELETE /api/runs/{id}/tags/{tag}` | `Run` |
| `PATCH /api/runs/{id}` `{name}` | `Run`; empty or null name removes it; emits `run.updated` |
| `GET /api/groups?kind=` / `GET /api/groups/{id}` | `[Group]` / `Group` (+ `runs: [RunSummary]`) |
| `PATCH /api/groups/{id}` `{name}` | `Group`; empty or null name removes it; emits `group.updated` |
| `GET /api/compare?ids=r_1,r_2,...` | `CompareResult` (first id = baseline) |
| `GET /api/sweeps/{group_id}` | `SweepResult` |
| `POST /api/jobs` `{kind: "sim"\|"sim-all"\|"synth"\|"matrix"\|"sweep", args:{...}}` | `{job_id}`; progress via WS. `args.name` names the run (sim, synth) or the batch (sim-all, matrix); `sim-all` takes `args.tests: [name]` to run only those (400 if not a list of strings; an unknown name fails the job) |
| `GET /api/jobs` | `[{job_id, kind, status, run_ids, group_id}]` |
| `GET /api/perf?group=` | `[PerfResult]` (TPI from latest sweep T_min, else latest synth period) |
| `GET /api/waves/{run_id}/meta` | `{file, format, timescale:{factor, unit}, t_start, t_end}` |
| `GET /api/waves/{run_id}/scope?path=` | `{path, scopes:[{name, path}], vars:[{name, path, width, kind, type?}]}` (`path=""` → top) |
| `GET /api/waves/{run_id}/search?q=&limit=` | `[{path, width, kind}]` (substring / glob over full paths) |
| `POST /api/waves/{run_id}/data` `{signals:[path], t0, t1, max_changes?}` | `{signals:{path:{width, init, changes:[[t, value]], truncated}}}`; values are binary strings over `01xz` (or `real`), `init` = value at t0 |
| ↳ downsampling | when a signal has more than `max_changes` transitions in `[t0,t1]`: `truncated = true`, entries are ≥ `ceil((t1−t0)/max_changes)` ticks apart, a burst collapses to `[t_first, "*"]` ("many transitions until the next entry") followed by `[t_end, settled value]` (omitted if `t_end > t1`). Changes are strictly after `t0`. Real signals have `width: null`; unknown signals → 404 |
| `GET /api/waves/{run_id}/next?signal=&t=&what=change\|x&dir=1\|-1` | `{t}` or `{t:null}` |
| `GET /api/waves/{run_id}/decode?signal=` | `{kind:"enum"\|"struct"\|"plain", enum?:{value:name}, fields?:[{name, msb, lsb, enum?}]}` |
| `GET /api/runs/{id}/divergence` | `Divergence` (sim runs) with `time` filled if mappable; 404 if none. `cycle` N maps to the N-th (0-based) rising edge of `project.clock` strictly after `project.reset` deasserts |
| `GET /api/design/hierarchy` | `{top, modules:{name:{ports:[...], instances:[{name, module}], file, line}}}` |
| `GET /api/design/block?module=` | `{nodes:[{id, label, module, ports}], edges:[{src, dst, width, net}]}` (ELK-ready) |
| `GET /api/design/signal?path=<wave path>` | `{path, module, file, line, type, width}`: declaration of a waveform signal (404 if not in the design) |
| `GET /api/design/cone?run=&net=&depth=&path=` | `{nodes:[{id, cell, pins, on_path, delay?}], edges:[...], unresolved:[...]}` |
| `GET /api/design/schematic/block?module=` | `Schematic` for one module level: `{netlist, cells, path_nets:[], counts:{instances, ports, nets}, module, instance}` |
| `GET /api/design/schematic/cone?run=&net=&depth=&path=` | `Schematic` for a gate cone: `{netlist, cells, path_nets, counts:{cells, symbols, on_path}, root_net, path_cells, unresolved, slack, run, netlist_file}` |
| ↳ `Schematic` | `netlist` = one Yosys-JSON module (`{modules:{name:{attributes:{top:1}, ports, cells}}}`) drawn by netlistsvg in the browser; `cells` = per drawn cell/port key `{kind: instance\|logic\|port\|cell, name, module?, type?, symbol?, on_path?, delay?}`; `path_nets` = netlistsvg wire classes (`net_<bits>`) on the critical path. Cell types are Yosys gate names (`$_NAND_`, ...) and `$svkit_*` (`$svkit_nand3`, `$svkit_dffr`, `$svkit_box_<width>`), one template each in `web/src/lib/schematic-skin.svg` |
| `GET /api/xtrace?run=&signal=&time=&depth=` | `XTraceNode` |
| `GET /api/source?file=&line=&context=` | `{file, start, lines:[...]}` (only files inside the project root) |
| `WS /ws/events` | stream of events (`run.created`, `run.status`, `run.updated`, `run.log`, `group.updated`, `job.updated`); `group.updated` is also sent when a batch starts |

Static UI is served from `/` (SPA fallback to `index.html`). The server binds `127.0.0.1` by default;
use `ssh -L 3572:localhost:3572 host`.

**Diagrams (Design view).** Blocks and Gates share one drawing path; only the netlist differs.
`svkit.design.schematic` turns a `DesignGraph.block()` level (instances, glue logic, ports) or an
`analysis.cone` (library cells) into a Yosys-JSON module. A library cell gets a gate symbol when its
Liberty `function` / `ff` / `latch` group (or, without Liberty, its name: `nand3`, `dff`, ...) matches a
skin template; anything else is a box with its pin names. In the browser a Web Worker
(`web/src/lib/schematic.worker.ts`) runs netlistsvg (layout by elkjs 0.3.0, symbols from the skin)
and returns SVG text; the page sanitizes it with DOMPurify (SVG profile, no `<style>`/scripts/links),
styles it from the theme tokens and adds tooltips, click targets and the critical-path highlight.

## 7. CLI (Typer — `src/svkit/cli.py`)

```
svkit init [--force]
svkit sim <test>... | --all [--flow NAME] [--jobs N] [--no-cache] [--name TEXT]   # 2+ tests = one batch
svkit synth [--flow NAME] [--set k=v ...] [--exp NAME | --matrix] [--jobs N] [--no-cache] [--name TEXT]
svkit rename <r_id|g_id> [NAME]
svkit sweep clock [--tol 50] [--jobs N] [--start PS]
svkit xtrace <hier.signal> @<time> [--run ID] [--depth N] [--json]
svkit runs ls|show <id>|diff <a> <b>|tag <id> <tag>
svkit serve [--port 3572] [--host 127.0.0.1]
```
Exit codes: 0 ok; 1 a test failed / timing not met where it matters; 2 config error; 3 tool error.

## 8. Fixtures

Each `fixtures/<name>/` is a self-contained mini user project with its own `svkit.toml`, RTL,
testbench, a `Makefile` with targets for both flows, and a DC TCL script (to exercise wrap mode).
Fixture RTL is original. The open-source flow uses `fixtures/lib/svkit_tiny.lib`, an original toy
Liberty library with `_R`/`_L` Vt variants, so Yosys + OpenSTA results are deterministic and
T_min is known. Golden outputs live under `fixtures/golden/<flow>/<fixture>/`. Synopsys goldens
(`fixtures/golden/synopsys/`) need a redistribution check before the repo goes public.

## 9. Concurrency and resources

* License seats: `license_seats` per synth flow → process-wide semaphore per adapter.
  Hard cap for this server: ≤ 2 concurrent `dc_shell-t`/`vcs` across everything.
* Synopsys tools additionally take a host-wide seat (file locks in `SVKIT_LOCK_DIR`, default
  `~/.cache/svkit/locks`; cap `SVKIT_SYNOPSYS_MAX_JOBS`, default 2) shared across processes and agents.
* All subprocesses run `nice`d in their own session and are killed on cancel/timeout, and when the
  `svkit` CLI gets Ctrl-C / SIGTERM or exits (`adapters.base.kill_children`). Their own session
  means a terminal Ctrl-C never reaches them directly.
