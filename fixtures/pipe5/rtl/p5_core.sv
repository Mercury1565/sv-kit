// svkit fixture "pipe5": a toy 5-stage in-order pipeline (IF ID EX MEM WB) for the
// invented T8 ISA (see p5_pkg.sv). Original design written for svkit tests.
//
// * full forwarding EX/MEM -> EX and MEM/WB -> EX, write-through register file
// * load-use hazards stall one cycle (p5_hazard)
// * branches/jumps resolve in EX; taken -> flush IF/ID and ID/EX (2 bubbles)
// * HALT stops fetch when decoded; `halted` rises when it retires
// Instruction and data memories are outside the core (in the testbench).
import p5_pkg::*;

module p5_core (
    input  logic        clock,
    input  logic        reset,          // synchronous, active high
    // instruction memory
    output logic [7:0]  imem_addr,
    input  logic [15:0] imem_data,
    // data memory (combinational read, write on clock edge)
    output logic [7:0]  dmem_addr,
    output logic [7:0]  dmem_wdata,
    output logic        dmem_we,
    input  logic [7:0]  dmem_rdata,
    // retirement / status
    output logic        wb_valid,
    output logic        wb_we,
    output logic [2:0]  wb_rd,
    output logic [7:0]  wb_data,
    output logic        halted,
    output logic        stall_load_use,
    output logic        flush_branch
);
    // ------------------------------------------------------------ IF
    logic [7:0]  pc;
    logic        fetch_stop;
    logic        ifid_valid;
    logic [15:0] ifid_instr;
    logic [7:0]  ifid_pc;

    // ------------------------------------------------------------ ID
    p5_pkg::op_t id_op;
    logic [2:0] id_rd, id_rs1, id_rs2, id_rb;
    logic       id_uses_ra, id_uses_rb, id_writes, id_rb_is_rd;
    logic [7:0] id_da, id_db;
    logic       stall;

    assign id_op       = p5_pkg::op_t'(ifid_instr[15:12]);
    assign id_rd       = ifid_instr[11:9];
    assign id_rs1      = ifid_instr[8:6];
    assign id_rs2      = ifid_instr[5:3];
    assign id_rb_is_rd = (id_op == OP_ST) || (id_op == OP_BEQ) || (id_op == OP_BNE);
    assign id_rb       = id_rb_is_rd ? id_rd : id_rs2;
    logic id_alu_rr;   // register-register ALU op
    assign id_alu_rr   = (id_op == OP_ADD) || (id_op == OP_SUB) || (id_op == OP_AND) ||
                         (id_op == OP_OR)  || (id_op == OP_XOR) || (id_op == OP_SLT);
    assign id_uses_ra  = id_alu_rr || (id_op == OP_ADDI) || (id_op == OP_LD) || id_rb_is_rd;
    assign id_uses_rb  = id_alu_rr || id_rb_is_rd;
    assign id_writes   = (id_alu_rr || (id_op == OP_LI) || (id_op == OP_ADDI) || (id_op == OP_LD))
                         && (id_rd != 3'd0);

    // ------------------------------------------------------------ pipeline registers
    logic       idex_valid, idex_writes;
    p5_pkg::op_t idex_op;
    logic [2:0] idex_rd, idex_ra, idex_rb;
    logic [7:0] idex_a, idex_b, idex_imm, idex_pc;

    logic       exmem_valid, exmem_writes;
    p5_pkg::op_t exmem_op;
    logic [2:0] exmem_rd;
    logic [7:0] exmem_alu, exmem_sdata;

    logic       memwb_valid, memwb_writes, memwb_halt;
    logic [2:0] memwb_rd;
    logic [7:0] memwb_data;

    // ------------------------------------------------------------ register file (written in WB)
    p5_regfile u_rf (
        .clock(clock), .reset(reset),
        .ra(id_rs1), .rb(id_rb), .da(id_da), .db(id_db),
        .we(memwb_valid && memwb_writes), .wa(memwb_rd), .wd(memwb_data)
    );

    p5_hazard u_hz (
        .id_valid(ifid_valid), .id_op(id_op), .id_ra(id_rs1), .id_rb(id_rb),
        .id_uses_ra(id_uses_ra), .id_uses_rb(id_uses_rb),
        .ex_valid(idex_valid), .ex_op(idex_op), .ex_rd(idex_rd), .stall(stall)
    );

    // ------------------------------------------------------------ EX (forwarding + ALU)
    logic [7:0] fa, fb, ex_y, ex_target;
    logic       ex_take, taken;

    always_comb begin
        fa = idex_a;
        if (exmem_valid && exmem_writes && exmem_rd == idex_ra)      fa = exmem_alu;
        else if (memwb_valid && memwb_writes && memwb_rd == idex_ra) fa = memwb_data;
        fb = idex_b;
        if (exmem_valid && exmem_writes && exmem_rd == idex_rb)      fb = exmem_alu;
        else if (memwb_valid && memwb_writes && memwb_rd == idex_rb) fb = memwb_data;
    end

    p5_alu u_alu (.op(idex_op), .a(fa), .b(fb), .imm8(idex_imm), .y(ex_y), .take(ex_take));

    assign taken     = idex_valid && ex_take;
    assign ex_target = (idex_op == OP_JMP) ? idex_imm
                                           : idex_pc + 8'd1 + {{2{idex_imm[5]}}, idex_imm[5:0]};

    // ------------------------------------------------------------ sequential
    assign imem_addr = pc;

    always_ff @(posedge clock) begin
        if (reset) begin
            pc          <= 8'h00;
            fetch_stop  <= 1'b0;
            ifid_valid  <= 1'b0;
            ifid_instr  <= 16'h0000;
            ifid_pc     <= 8'h00;
            idex_valid  <= 1'b0;
            exmem_valid <= 1'b0;
            memwb_valid <= 1'b0;
            memwb_halt  <= 1'b0;
            halted      <= 1'b0;
        end else begin
            // IF / IF-ID
            if (taken) begin
                pc         <= ex_target;
                ifid_valid <= 1'b0;
                fetch_stop <= 1'b0;
            end else if (!stall) begin
                if (ifid_valid && id_op == OP_HALT) fetch_stop <= 1'b1;
                if (!fetch_stop && !(ifid_valid && id_op == OP_HALT)) begin
                    pc         <= pc + 8'd1;
                    ifid_valid <= 1'b1;
                    ifid_instr <= imem_data;
                    ifid_pc    <= pc;
                end else begin
                    ifid_valid <= 1'b0;
                end
            end
            // ID-EX
            idex_valid  <= ifid_valid && !stall && !taken;
            idex_op     <= id_op;
            idex_rd     <= id_rd;
            idex_ra     <= id_rs1;
            idex_rb     <= id_rb;
            idex_writes <= id_writes;
            idex_a      <= id_da;
            idex_b      <= id_db;
            idex_imm    <= ifid_instr[7:0];
            idex_pc     <= ifid_pc;
            // EX-MEM
            exmem_valid  <= idex_valid;
            exmem_op     <= idex_op;
            exmem_rd     <= idex_rd;
            exmem_writes <= idex_writes;
            exmem_alu    <= ex_y;
            exmem_sdata  <= fb;
            // MEM-WB
            memwb_valid  <= exmem_valid;
            memwb_writes <= exmem_writes;
            memwb_rd     <= exmem_rd;
            memwb_data   <= (exmem_op == OP_LD) ? dmem_rdata : exmem_alu;
            memwb_halt   <= exmem_valid && exmem_op == OP_HALT;
            // WB
            if (memwb_valid && memwb_halt) halted <= 1'b1;
        end
    end

    // ------------------------------------------------------------ MEM
    assign dmem_addr  = exmem_alu;
    assign dmem_wdata = exmem_sdata;
    assign dmem_we    = exmem_valid && (exmem_op == OP_ST);

    // ------------------------------------------------------------ status
    assign wb_valid       = memwb_valid;
    assign wb_we          = memwb_valid && memwb_writes;
    assign wb_rd          = memwb_rd;
    assign wb_data        = memwb_data;
    assign stall_load_use = stall && !taken;
    assign flush_branch   = taken;
endmodule
