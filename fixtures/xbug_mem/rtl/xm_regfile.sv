`timescale 1ns/1ps
// 8-entry register file. PLANTED BUG (class 2a): the array has no reset and no
// initial value, so reading an entry before it is written returns X.
module xm_regfile #(parameter int W = 8, parameter int N = 8) (
  input  logic                 clk,
  input  logic                 we,
  input  logic [$clog2(N)-1:0] waddr,
  input  logic [W-1:0]         wdata,
  input  logic [$clog2(N)-1:0] raddr,
  output logic [W-1:0]         rdata
);
  logic [W-1:0] regs [0:N-1];

  always_ff @(posedge clk) begin
    if (we)
      regs[waddr] <= wdata;
  end

  assign rdata = regs[raddr];
endmodule
