// svkit fixture "pipe5": package for the toy T8 ISA (original, invented for svkit tests).
//
// 16-bit instructions, 8 registers x 8 bits (r0 reads as zero), word-addressed 8-bit PC.
//   [15:12] op   [11:9] rd   [8:6] rs1   [5:3] rs2   [5:0] imm6 (signed)   [7:0] imm8
package p5_pkg;
    typedef enum logic [3:0] {
        OP_NOP  = 4'h0,   // no operation
        OP_ADD  = 4'h1,   // rd = rs1 + rs2
        OP_SUB  = 4'h2,   // rd = rs1 - rs2
        OP_AND  = 4'h3,   // rd = rs1 & rs2
        OP_OR   = 4'h4,   // rd = rs1 | rs2
        OP_XOR  = 4'h5,   // rd = rs1 ^ rs2
        OP_LI   = 4'h6,   // rd = imm8
        OP_ADDI = 4'h7,   // rd = rs1 + imm6
        OP_LD   = 4'h8,   // rd = M[rs1 + imm6]
        OP_ST   = 4'h9,   // M[rs1 + imm6] = rd
        OP_BEQ  = 4'hA,   // if (rd == rs1) pc = pc + 1 + imm6
        OP_BNE  = 4'hB,   // if (rd != rs1) pc = pc + 1 + imm6
        OP_JMP  = 4'hC,   // pc = imm8
        OP_SLT  = 4'hD,   // rd = (rs1 < rs2) unsigned
        OP_HALT = 4'hF    // stop
    } op_t;
endpackage
