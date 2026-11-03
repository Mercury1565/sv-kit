`timescale 1ns/1ps
// Top of the xbug_mem fixture.
// PLANTED BUG (class 2b): `ptr` is a 3-bit counter (0..7) used to index the
// 6-entry table `lut`, so entries 6 and 7 are out of range and read as X.
module xm_top (
  input  logic       clk,
  input  logic       rst,
  input  logic       we,
  input  logic [2:0] waddr,
  input  logic [7:0] wdata,
  input  logic [2:0] raddr,
  output logic [7:0] rd_out,
  output logic [7:0] lut_out
);
  logic [7:0] rdata;
  logic [7:0] rd_q;
  logic [7:0] lut [0:5];
  logic [2:0] ptr;
  logic [7:0] lut_q;

  xm_regfile #(.W(8), .N(8)) u_rf (
    .clk(clk), .we(we), .waddr(waddr), .wdata(wdata), .raddr(raddr), .rdata(rdata)
  );

  always_ff @(posedge clk) begin
    if (rst) begin
      rd_q <= '0;
      ptr  <= '0;
      lut_q <= '0;
      for (int i = 0; i < 6; i++)
        lut[i] <= 8'(i * 16 + 1);
    end else begin
      rd_q  <= rdata;
      ptr   <= ptr + 3'd1;
      lut_q <= lut[ptr];
    end
  end

  assign rd_out  = rd_q;
  assign lut_out = lut_q;
endmodule
