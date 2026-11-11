# Performance (CPI / TPI)

For processor designs: cycles per instruction (CPI) for every test program, and time per instruction
(TPI), which combines CPI with the clock period synthesis can actually reach.

![Performance view with CPI per program and the stall breakdown](images/perf.png)

## What it shows

- **CPI per program** as a chart, with the mean.
- A table per program: cycles, instructions, CPI, clock period, **TPI = CPI × clock period**, and stall
  counts by cause (for example load-use stalls and branch flushes).
- **Where the clock period comes from**: the latest clock sweep's T_min (see [sweep.md](sweep.md)) or a
  synthesis run, linked so you can check it.

## Batches: run the tests you choose, under a name

Each run of several tests is a **batch**. The dropdown at the top lists every batch, newest first,
and shows that batch's numbers on their own; *Latest result per test* mixes the newest result of
every test across all runs.

- **Run tests…** opens a list of your tests with checkboxes (filter, select all, select none). Pick
  the ones you want, give the batch a name if you like (for example `after the branch predictor fix`),
  and run them. The page switches to the new batch when it finishes. Your choice of tests is
  remembered for next time.
- **Rename a batch** with the pencil next to its name (or *+ Add a name*). Names show in the dropdown.

From the CLI:

```
svkit sim fib gcd copy --name "after the fix"   # these tests, as one named batch
svkit sim --all --name nightly                  # every test
svkit rename g_0004 "before the fix"            # name (or rename) a batch later
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

The numbers update whenever you rerun the tests or the sweep.
