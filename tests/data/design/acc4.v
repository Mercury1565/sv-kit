// Tiny original design for gate-cone tests: 4-bit accumulator with a parity flag.
module acc4 (
  input  wire       clk,
  input  wire [3:0] a,
  input  wire       en,
  output reg  [3:0] acc,
  output reg        par
);
  wire [3:0] nxt = en ? acc + a : acc;
  always @(posedge clk) begin
    acc <= nxt;
    par <= ^nxt;
  end
endmodule
