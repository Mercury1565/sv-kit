# Track 2 (Synopsys flow) — contract proposals

Nothing here was changed in the shared contracts; each item has a work-around in the Track 2 code.

## P1. Hierarchy separator in `area_by_hier` and `TimingPoint.inst` (ARCHITECTURE §3)
**What:** state that hierarchical instance paths use the tool-native `/` separator
(`u_core/u_execute`), top = `""`.
**Why:** DC and OpenSTA both print `/`; netlist-based cone lookups match on it. `XTraceNode.signal`
is dot-separated (RTL names), which is a different namespace. Today the separator is unspecified.
**Diff (schemas.py docstring only):**
```diff
-    area_by_hier: dict[str, float] = Field(default_factory=dict)   # hier path -> area ('' = top)
+    area_by_hier: dict[str, float] = Field(default_factory=dict)   # '/'-separated instance path -> area ('' = top)
```
and `TimingPoint.inst  # '/'-separated hierarchical instance name`.

## P2. Runner should export `flow.env` (config.py / runner.py)
**What:** `Runner._ctx` merges the flow's `env` table into `ctx.env`.
**Why:** `SimFlow.env` / `SynthFlow.env` exist but nothing applies them. Work-around: the VCS and DC
adapters pass `flow.env` as `extra_env` to `ctx.sh` (other adapters may forget).
```diff
-    def _ctx(self, run, flow_name, workdir, plan):
+    def _ctx(self, run, flow_name, workdir, plan, flow_env=None):
         env = dict(os.environ)
+        env.update(flow_env or {})
         env.update(plan.env)
```

## P3. Anchor for experiment-level `tcl_append` (ARCHITECTURE §2.3)
**What:** a per-flow default anchor, e.g. `SynthFlow.append_before: Optional[str]` (regex), used for
`[experiments] x = { tcl_append = "..." }` entries (which have no anchor today).
**Why:** typical DC scripts end with `compile`, reports and `exit`; text appended at the end of the
script never takes effect (e.g. `set_max_fanout 8`). Work-arounds: the fixture template keeps
compile/report in a separate `syn_run.tcl` so appends land before compile; builtin mode inserts
anchor-less appends before `# svkit:compile`. Real user scripts (dogfood) still need a knob with
`before = "^compile"`.
```diff
 class SynthFlow(_M):
+    append_before: Optional[str] = None   # regex anchor for anchor-less tcl_append (default: end of script)
```

## P4. Library time unit as a config field
**What:** `SynthFlow.time_unit: Optional[Literal["ps","ns"]] = None`.
**Why:** DC reports carry no unit suffix. The parser detects the unit (report_units / report_power
"Time Units"), infers it from the knob period, or uses a magnitude heuristic; the configured fallback
currently lives in `[flows.synth.dc].env.SVKIT_DC_TIME_UNIT`, which also leaks into the tool env.

## P5. Build cache directory in `RunContext`
**What:** `RunContext.cache_dir: Path` (= `.svkit/build/`), documented as the one place adapters may
write outside `workdir`/`run_dir`.
**Why:** builtin VCS compiles once per source hash; the adapter derives `.svkit/build` from
`run_dir.parent.parent` today. Verilator builtin has the same need.

## P6. Host-wide Synopsys seats (ARCHITECTURE §9)
**What:** document that the VCS and DC adapters hold one of `SVKIT_SYNOPSYS_MAX_JOBS` (default 2)
flock()ed seats (`$SVKIT_LOCK_DIR` or `~/.cache/svkit/locks`) for every licensed tool call, across
threads, processes and agents on the host — in addition to the per-flow `license_seats` semaphore.

## P7. `[tools]` names
Document `vpd2vcd`, `fsdb2vcd`, `vcs`, `dc_shell-t` as overridable `[tools]` keys.
