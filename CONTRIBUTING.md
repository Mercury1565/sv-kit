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

## Releasing

Users update with `svkit update`, which installs the latest GitHub release. Cut one for every change
users should get:

```
python3 scripts/release.py minor        # or patch / major, or an exact version like 0.9.0
git push origin main vX.Y.Z             # the tag it printed, on its own
```

Push **one release tag per push**. GitHub starts no workflow for a push that carries more than three
tags, so `--follow-tags` or `--tags` with several new tags creates no release, silently. If a tag
reached GitHub without a release, open **Actions → release → Run workflow** and enter the tag; the
workflow skips tags that already have a release.

`scripts/release.py` checks that you are on `main` with nothing uncommitted. It then sets
`svkit.__version__` (the only place the version is written; `pyproject.toml` reads it), commits
`Release vX.Y.Z`, and tags `vX.Y.Z` with the commits since the previous release as the message.
Pushing the tag runs `.github/workflows/release.yml`. That workflow checks that the tag matches the
version and that the package installs with its web assets, then publishes the GitHub release with
those notes.

Use `patch` for fixes, `minor` for new features, `major` for changes that break `svkit.toml` or the
Run DB. The web assets in `src/svkit/server/static/` must be rebuilt (`cd web && npm run build`) and
committed before releasing a UI change.

