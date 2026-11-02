# xbug_input — X-trace class 4

Original fixture design for `svkit xtrace`.

| Planted bug | Where | Expected root cause |
|---|---|---|
| The testbench leaves top-level input `cfg` undriven (Z) for 4 cycles after reset | `tb/tb_xi.sv` | `external_input` on `tb_xi.dut.cfg` |

Symptom (see `fixtures/golden/xtrace/xbug_input/expected.json`): `tb_xi.result` goes X:
`res_q` ← `mixed` (crosses `u_mix`) ← `mix` (`always_comb`, data path `data ^ {cfg_q, cfg_q}`) ← `cfg_q` ←
`cfg` port ← top-level input `cfg` = `4'bzzzz`. Combinational and sequential logic, one module boundary.

Only a 4-state simulator shows the X/Z (VCS, iverilog); Verilator is 2-state.
