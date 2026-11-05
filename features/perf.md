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
