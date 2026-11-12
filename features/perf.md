# Performance (CPI / TPI)

For processor designs: cycles per instruction (CPI) for every test program, and time per instruction
(TPI), which combines CPI with the clock period synthesis can actually reach.

![Performance view with CPI per program and the stall breakdown](images/perf.png)

## What it shows

- **A chart per program**, with the mean. Switch it between **cycles per instruction** (CPI),
  **instructions per cycle** (IPC = 1 / CPI) and **time per instruction** (TPI; available once a clock
  period is known). Bars are sorted worst first.
- A table per program: cycles, instructions, CPI, clock period, **TPI = CPI × clock period**, and stall
  counts by cause (for example load-use stalls and branch flushes).
- **Where the clock period comes from**: the `clock_period` knob's `default` in `svkit.toml`, or, when
  that is not set, the latest synthesis run (linked so you can check it).

## One test across runs

Click a program in the table (or pick it from *See one test across runs…*) to see how its numbers
changed over every run of it: two bar charts, **cycles per instruction** and **time per instruction**,
one bar per run, oldest on the left. Each run has its own color, the same in both charts, and is
labelled with its batch name (or run name, or id) and date. A table below repeats the numbers with the
change from the previous run (red when it got worse, green when it got better). Click a bar to open
that run.

- Eight runs are shown at a time; *‹ Older* and *Newer ›* page through the rest.
- Time per instruction uses the clock period from `svkit.toml` for every run. Without one, it uses the
  synthesis period svkit knew **when that run was made**, so a faster clock from a later design
  revision shows up in TPI.
- Each chart has its own *Save image (PNG)*.

## Export

- **Save image (PNG)** saves the chart as it is switched (CPI, IPC or TPI), with every program in it
  even when the chart is zoomed, at twice screen resolution, titled with the batch it shows.
- **Download CSV** saves the table: one row per program with raw numbers (cycles, instructions, CPI,
  IPC, clock period, TPI, stall counts, where the period came from). It opens in any spreadsheet.

File names say what they hold, for example `perf-g_0004-after-the-fix-tpi.png` or `perf-latest.csv`.

## Batches: run the tests you choose, under a name

Each run of several tests is a **batch**. The dropdown at the top lists every batch, newest first,
and shows that batch's numbers on their own; *Latest result per test* mixes the newest result of
every test across all runs.

- **Run tests…** opens a list of your tests with checkboxes (filter, select all, select none). Pick
  the ones you want, give the batch a name if you like (for example `after the branch predictor fix`),
  and run them. The page switches to the new batch when it finishes. Your choice of tests is
  remembered for next time.
- **Rename a batch** with the pencil next to its name (or *+ Add a name*). Names show in the dropdown.
- **Remove a batch** you ran by mistake: pick it in the dropdown, then *Remove batch*. svkit asks once,
  then deletes that batch's runs and their files; other batches are not touched. A batch with tests
  still running is kept until they finish or are cancelled.

From the CLI:

```
svkit sim fib gcd copy --name "after the fix"   # these tests, as one named batch
svkit sim --all --name nightly                  # every test
svkit rename g_0004 "before the fix"            # name (or rename) a batch later
svkit runs rm g_0005                            # remove a batch and its runs (asks first)
```

## Setup

Your testbench already prints its counters; tell svkit where to find them in `svkit.toml`:

```toml
[perf.source]
regex_file = "output/{test}.cpi"
cycles = 'cycles\s*=\s*(\d+)'
instrs = 'instrs\s*=\s*(\d+)'
stalls = { load_use = 'load_use_stalls\s*=\s*(\d+)', branch = 'branch_flushes\s*=\s*(\d+)' }
```

Time per instruction uses the clock period you set in `svkit.toml` (in ps):

```toml
[knobs]
clock_period = { file = "clock_period.txt", unit = "ps", default = 2000 }
```

Clock sweeps don't change it: after a sweep, put the T_min you want to use into `default`.

The numbers update whenever you rerun the tests or edit `svkit.toml` (restart `svkit serve` to pick up the edit).
