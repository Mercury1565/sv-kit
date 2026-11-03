// svkit fixture "pipe5": ALU + branch condition.
import p5_pkg::*;

module p5_alu (
    input  p5_pkg::op_t op,
    input  logic [7:0] a,        // rs1 value
    input  logic [7:0] b,        // rs2 value, or rd value for ST/BEQ/BNE
    input  logic [7:0] imm8,
    output logic [7:0] y,
    output logic       take      // branch/jump taken
);
    logic [7:0] simm;
    assign simm = {{2{imm8[5]}}, imm8[5:0]};

    always_comb begin
        y    = 8'h00;
        take = 1'b0;
        case (op)
            OP_ADD:  y = a + b;
            OP_SUB:  y = a - b;
            OP_AND:  y = a & b;
            OP_OR:   y = a | b;
            OP_XOR:  y = a ^ b;
            OP_LI:   y = imm8;
            OP_ADDI, OP_LD, OP_ST: y = a + simm;
            OP_SLT:  y = {7'b0, a < b};
            OP_BEQ:  take = (a == b);
            OP_BNE:  take = (a != b);
            OP_JMP:  take = 1'b1;
            default: y = 8'h00;
        endcase
    end
endmodule
