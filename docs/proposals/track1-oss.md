# Track 1 (open-source flow) — contract proposals

None of these block Track 1; each one is worked around today.

## P1. The sim cache key misses test inputs and goldens
**What:** `Runner.sim` hashes design/tb sources, include headers, `svkit.toml` and the Makefile.
It does not hash the per-test input file (for example `programs/<test>.mem` from `tests.glob`) or
the golden `expected` file. So after you edit a program or a golden, `svkit sim` returns a stale
cached result.
**Diff (core/runner.py, `sim`):**
```python
-        src = P.source_hash(self.cfg, P.flow_files(self.cfg))
+        extra = P.flow_files(self.cfg)
+        if fl.tests.glob:
+            extra += [p for p in self.cfg.root.glob(fl.tests.glob) if p.stem == test]
+        if fl.golden:
+            extra.append(self.cfg.root / P.tmpl(fl.golden.expected, self.cfg, test=test))
+        src = P.source_hash(self.cfg, extra)
```

## P2. Wrap-mode `sim --all` with jobs > 1 races on the user's build
**What:** wrapped commands such as `make sim-verilator TEST=x` or `make x.out` usually depend on
one shared executable (simv / Vsim). When several of them run at once, they rebuild that
executable concurrently, and runs fail at random (seen with `jobs=4`).
**Proposal:** add `SimFlow.build_cmd: Optional[str]`. If it is set, the runner runs it once,
serially, before it fans out the tests. Also add `SimFlow.parallel: bool = True`; with
`parallel = false`, `sim_all` runs with `jobs=1`. Until then, `svkit init` output and the tests
use `jobs=1` for wrap mode. Builtin Verilator is unaffected because it builds once under a lock.

## P3. Adapter build cache location
**What:** builtin Verilator caches its executable in `<root>/.svkit/build/verilator-<hash>/`.
That is outside `workdir`/`run_dir`, which ARCHITECTURE §4 forbids for adapters. It is svkit's
own state directory, not the user's tree.
**Proposal:** add `RunContext.cache_dir` (= `RunDB.state_dir / "build"`), and in §4 allow
adapters to write there.

## P4. DC parity for the `rvt_only` experiment (merge note)
Main now has `SynthFlow.append_before`. When merging, set `append_before = '^compile\b'` in each
fixture's `[flows.synth.dc]`. Otherwise the `rvt_only` experiment's
`tcl_append = "set_dont_use ..."` is appended after `compile`. That is harmless, but it does
nothing. (This branch predates the field, so the fixtures cannot carry it yet without failing
validation.)
