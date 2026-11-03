// svkit fixture "pipe5": 8x8 register file, r0 hard-wired to zero, write-through bypass.
module p5_regfile (
    input  logic       clock,
    input  logic       reset,
    input  logic [2:0] ra,
    input  logic [2:0] rb,
    output logic [7:0] da,
    output logic [7:0] db,
    input  logic       we,
    input  logic [2:0] wa,
    input  logic [7:0] wd
);
    logic [7:0] regs [8];

    always_ff @(posedge clock) begin
        if (reset) begin
            for (int i = 0; i < 8; i++) regs[i] <= 8'h00;
        end else if (we && wa != 3'd0) begin
            regs[wa] <= wd;
        end
    end

    assign da = (ra == 3'd0) ? 8'h00 : (we && wa == ra) ? wd : regs[ra];
    assign db = (rb == 3'd0) ? 8'h00 : (we && wa == rb) ? wd : regs[rb];
endmodule
