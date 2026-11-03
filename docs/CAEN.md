# Continuing svkit on CAEN

This is the handoff for continuing development on a CAEN host, where VCS, `dc_shell-t` and the dogfood repo are available.
Before starting, read `PROGRESS.md` (owner summary and latest iteration) and `.claude/CLAUDE.md`.

Status at handoff (2026-11-02, tag `iter-2`): about 65 % to MVP.
- **Open-source flow:** complete and tested for real.
- **Synopsys flow:** written, but tested only with fake tools and hand-written DC report samples.
- **Never run here:** real VCS/DC, the dogfood checkpoint, the `pip install` test, the README.

## 1. Move the repo to CAEN (no remote: decision #7/#12 is still open)

On the dev host:
```
cd ~/Desktop/sv-kit && git bundle create /tmp/svkit.bundle --all
scp /tmp/svkit.bundle <uniqname>@login.engin.umich.edu:~/
```
On CAEN:
```
git clone ~/svkit.bundle ~/svkit && cd ~/svkit && git log --oneline -1   # expect "Iteration 2: ..." / tag iter-2
```
`git clone` from a bundle records the bundle as `origin`. Run `git remote remove origin` so the repo stays remote-free, as the rules require.

## 2. Environment (no sudo; everything under `~`)

```
cd ~/svkit
python3.11 -m venv .venv          # CAEN's python3 is 3.9: never use it
.venv/bin/pip install -e '.[dev]'
```
- **Node:** the system Node is v10. Node is needed only to rebuild `web/`, because the built assets are committed. If you do rebuild, install Node LTS into `~/tools/node` and run `cd web && npm ci && npm run build`. The build writes to `src/svkit/server/static/`.
- **OSS flow (optional on CAEN):**
  - Run `source ~/tools/oss-cad-suite/environment`.
  - OpenSTA is not in OSS CAD Suite. It was installed with conda (`litex-hub::openroad`) into `~/tools/sta-env`, then symlinked as `~/tools/bin/sta`.
  - `tests/conftest.py` adds `~/tools/bin` and `~/tools/oss-cad-suite/bin` to PATH automatically.
  - Unset `VERILATOR_ROOT` if a stale one is set.
- **Synopsys:** `vcs` and `dc_shell-t` are on PATH by default. Verdi is not.
- **License cap: at most 2 concurrent `vcs` / `dc_shell-t` jobs in total.**
  - svkit enforces this host-wide with file locks in `~/.cache/svkit/locks`. `SVKIT_SYNOPSYS_MAX_JOBS` sets the cap; the default is 2. Never raise it.
  - Run long jobs under `nice`.
  - Kill everything you start.

### DC library variables (never commit library paths)

Set these in your shell only. Pick one of the two options:

- **Option A: a real `.db` library.**
  - Set `SVKIT_DC_TARGET_LIB=/abs/path/lib_tt.db`.
  - Optionally set `SVKIT_DC_TARGET_LIB_SS` for the `corner=SS` knob, plus `SVKIT_DC_LINK_LIB` and `SVKIT_DC_SEARCH_PATH`.
  - Set `SVKIT_DC_TIME_UNIT=ps` if the library uses ps.
- **Option B: the original toy library.**
  - Set `SVKIT_DC_LIBERTY=$PWD/fixtures/lib/svkit_tiny.lib`.
  - Synthesis converts it to `.db` with `read_lib`/`write_lib`, which needs a Library Compiler license.

Details are in `fixtures/synopsys_common/README.md`.

## 3. Work items on CAEN, in order

### 3a. Baseline
```
.venv/bin/pytest -q                       # expect 196 passed, 4 skipped (no OSS tools: more oss skips)
```

### 3b. Real Synopsys runs
```
nice .venv/bin/pytest -m synopsys -v      # tests/test_vcs_e2e.py, tests/test_dc_e2e.py (counter, pipe5)
```
Fix the adapters until these pass:
- `src/svkit/adapters/sim/vcs.py`
- `src/svkit/adapters/synth/dc.py` and `dc_reports.py`
- `src/svkit/adapters/waves/vpd.py`

Fix the generic cause, never a fixture-specific one.

Things to check against real tool output:
- **DC report layouts** (`report_timing`, `qor`, `area -hier`, `power`, `reference`, `constraints`): the samples in `fixtures/golden/synopsys/samples/` were written by hand to imitate them.
- **Time unit detection** (ns vs ps) and power units.
- **VPD→VCD conversion** (`vpd2vcd`), when the TB dumps VPD.
- **The fixture DC flow:** `fixtures/<fx>/synopsys/{syn.tcl,syn_run.tcl,vcs.mk}`, which are copies of `fixtures/synopsys_common/`.
  - If a template needs changing, change `synopsys_common` and every copy.
  - `tests/test_dc_templates.py` checks the copies match.

### 3c. Real DC goldens
1. Copy real reports from a passing run (`<fixture copy>/.svkit/runs/<id>/`) into `fixtures/golden/synopsys/` (a `real/` subdir is fine).
2. Strip absolute install and library paths from them.
3. Regenerate the expected JSON:
   ```
   SVKIT_REGEN_GOLDEN=1 .venv/bin/pytest tests/test_dc_reports.py -q
   ```
4. Review `git diff`.
5. Keep the "Needs owner" item about redistributing Synopsys output in `PROGRESS.md`.

**Never commit:** libraries (including SAED), `.db` files, Synopsys docs or install paths.

### 3d. Synopsys sweep and full loop on a fixture copy
```
mkdir -p ~/svkit-work/fx && cp -r fixtures/pipe5 fixtures/lib ~/svkit-work/fx/ && cd ~/svkit-work/fx/pipe5
svkit sim --all --flow vcs
svkit synth --matrix --flow dc
svkit sweep clock --flow dc --tol 50 --jobs 2   # jobs <= 2 (license cap)
svkit serve                                     # from your laptop: ssh -L 8470:localhost:8470 <caen host>
```
Stop the server (Ctrl-C) before leaving.

### 3e. Dogfood checkpoint (MVP criterion 5)
The original repo `/home/hermong/eecs470/470_proj-f26.group4` is **read-only**: no edits and no git writes.
```
mkdir -p ~/svkit-work && cp -r /home/hermong/eecs470/470_proj-f26.group4 ~/svkit-work/dogfood
cd ~/svkit-work/dogfood && svkit init       # then review the generated svkit.toml
svkit sim --all; svkit synth --matrix; svkit sweep clock --jobs 2; svkit serve
```
- Log every hand edit to `svkit.toml` and every failure in `PROGRESS.md`.
- Fix generic heuristics in `src/svkit/init.py` and the adapters.
- Never copy course source into the svkit repo.

### 3f. Not tied to CAEN (can be done anywhere)
- **README:** setup, the `svkit.toml` reference (from `docs/ARCHITECTURE.md` §2), and how to add an adapter (entry point `svkit.adapters`).
- **Install test:** `pip install` from a built wheel in a clean venv with no Node, then `svkit serve` returns `/api/project`.
- **Web API proposals** (in `docs/proposals/web.md`):
  - `/api/design/signal`
  - the `X-Total-Count` header
  - `run_ids` on early `job.updated` events
- **Browser check:** click through every view in a real browser.

## 4. Waiting on the owner (see "Needs owner" in PROGRESS.md)
1. Whether to add an Icarus (4-state) sim adapter to the MVP, so the OSS flow can trace X. This changes decision #18.
2. Redistribution check for Synopsys-generated goldens.
3. Git hosting, remote and license.

## 5. Closing each iteration
- Tests are green.
- `git commit` with the `Co-Authored-By` line.
- `git tag -a iter-N`.
- A new `PROGRESS.md` entry in the standard format, and the owner summary is updated.
- Never add a remote or push.
