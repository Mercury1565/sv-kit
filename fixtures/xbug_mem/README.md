# xbug_mem — X-trace class 2

Original fixture design for `svkit xtrace`.

| Planted bug | Where | Expected root cause |
|---|---|---|
| Register file `regs` has no reset/initial; entry 5 is read before it is ever written | `rtl/xm_regfile.sv` | `uninit_memory` on `tb_xm.dut.u_rf.regs[5]` |
| 3-bit counter `ptr` indexes the 6-entry `lut`; values 6 and 7 are out of range | `rtl/xm_top.sv` | `index_out_of_range` on `tb_xm.dut.lut[6]` / `[7]` |

Symptoms (see `fixtures/golden/xtrace/xbug_mem/expected.json`):

* `tb_xm.rd_out` goes X: `rd_q` ← `rdata` (crosses `u_rf`) ← `regs[raddr]` with `raddr = 5`, never written.
* `tb_xm.lut_out` goes X every 8 cycles: `lut_q` ← `lut[ptr]` with `ptr = 6` or `7`.

The waveform does not contain the memory words (iverilog does not dump arrays); the tracer infers
the uninitialized word from the write history (`we`/`waddr` at each clock edge).
Only a 4-state simulator shows the X (VCS, iverilog); Verilator is 2-state.
