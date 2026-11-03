// svkit fixture "pipe5": load-use hazard detection.
//
// KNOWN BUG (deliberate, documented in fixtures/README.md): when a LD is immediately
// followed by a ST whose *data* register (the rd field) is the loaded register, no stall
// is raised, so the store forwards the load's ADDRESS instead of its data. The program
// `programs/ldst_fwd.s` exercises it and therefore fails its golden check.
// Define P5_FIX_LDST_HAZARD to get the corrected behaviour (used to produce the goldens).
import p5_pkg::*;

module p5_hazard (
    input  logic       id_valid,
    input  p5_pkg::op_t id_op,
    input  logic [2:0] id_ra,
    input  logic [2:0] id_rb,
    input  logic       id_uses_ra,
    input  logic       id_uses_rb,
    input  logic       ex_valid,
    input  p5_pkg::op_t ex_op,
    input  logic [2:0] ex_rd,
    output logic       stall
);
    logic rb_checked;
`ifdef P5_FIX_LDST_HAZARD
    assign rb_checked = id_uses_rb;
`else
    assign rb_checked = id_uses_rb && (id_op != OP_ST);
`endif
    assign stall = id_valid && ex_valid && (ex_op == OP_LD) && (ex_rd != 3'd0) &&
                   ((id_uses_ra && id_ra == ex_rd) || (rb_checked && id_rb == ex_rd));
endmodule
