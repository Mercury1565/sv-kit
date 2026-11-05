# Contributing to svkit

Design background for contributors: [PRODUCT.md](PRODUCT.md) (spec), [docs/MVP.md](docs/MVP.md)
(v1 scope), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (contracts and APIs) and
[PROGRESS.md](PROGRESS.md) (status and known gaps).

## Adding an adapter

Adapters run a tool and normalize its output to the shared schemas in `src/svkit/schemas.py`
(`SimResult`, `SynthResult`, `TimingPath`, …). The UI and the analyses only ever see those
schemas, so a new adapter needs no UI work.

1. Subclass `SimAdapter`, `SynthAdapter` or `StaAdapter` from `svkit.adapters.base`. Set `name`
   and `tools`, and implement `run()`. A synthesis adapter also implements `parse(files, top=...)`
   so it can be tested against golden report files without the tool.
2. Use the `RunContext`:
   - `ctx.sh(cmd)` runs commands, niced and cancellable, with the output streamed to the live log.
   - `ctx.keep(path, name)` stores artifacts. Keep the waveform as `waves.vcd`/`waves.fst`, logs as
     `sim.log`/`synth.log`, and the netlist as `netlist.v`.
   - Never write outside `ctx.workdir` / `ctx.run_dir`.
3. Sim adapters decide pass/fail with `svkit.core.golden.evaluate()`, so every flow behaves the same.
   Synthesis adapters fill `vt_mix` with `svkit.analysis.vt.vt_mix()`.
4. Register the adapter from your own package:
   ```toml
   [project.entry-points."svkit.adapters"]
   mysim = "my_pkg.mysim:MySimAdapter"
   ```
   It is then usable as `[flows.sim.mysim]`.
5. Add golden-file tests: real tool output plus the expected normalized JSON. See
   `tests/test_dc_reports.py` and `fixtures/golden/`.

## Development setup

```
python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                 # tool-dependent tests skip when the tools are missing
.venv/bin/pytest -m synopsys        # real VCS/DC runs (needs SVKIT_DC_TARGET_LIB or SVKIT_DC_LIBERTY)
.venv/bin/pytest -m oss             # real Verilator/Yosys/OpenSTA runs
```

- **DC library setup** for the fixtures: see `fixtures/synopsys_common/README.md`.
- **Web UI:** React + TypeScript in `web/`. Rebuild with `cd web && npm ci && npm run build`, which
  needs a modern Node and writes to `src/svkit/server/static/`. Commit the built assets.
