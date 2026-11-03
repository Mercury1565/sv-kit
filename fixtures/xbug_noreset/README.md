# xbug_noreset — X-trace class 1

Original fixture design for `svkit xtrace`.

| Planted bug | Where | Expected root cause |
|---|---|---|
| `stage1_q` has no reset and is loaded only when `in_valid` is high | `rtl/xn_scaler.sv` | `no_reset` on `tb_xn.dut.u_scaler.stage1_q` |
| `mode_q` is declared but never assigned | `rtl/xn_top.sv` | `no_reset` ("never assigned") on `tb_xn.dut.mode_q` |

Symptoms (see `fixtures/golden/xtrace/xbug_noreset/expected.json`):

* `tb_xn.out_data` goes X after reset: `out_q` ← `scaled` (crosses the `u_scaler` boundary) ←
  `stage2_q` (reset, but samples X) ← `stage1_q` (no reset). Two register stages, one module boundary.
* `tb_xn.out_flag` goes X while `in_valid` is high: `flag_q` ← `flag` ← `mode_q` (never assigned).

The X only shows in a 4-state simulator (VCS, iverilog). Verilator is 2-state.
Regenerate the golden waveform with `make sim-iverilog` (or `tests/test_xtrace_fixtures.py`).
