`timescale 1ns/1ps
// Top of the xbug_input fixture. The design is correct; the testbench leaves
// the `cfg` input undriven (Z) for the first cycles after reset.
module xi_top (
  input  logic       clk,
  input  logic       rst,
  input  logic [3:0] cfg,
  input  logic [7:0] data,
  output logic [7:0] result
);
  logic [7:0] mixed;
  logic [7:0] res_q;

  xi_mixer u_mix (.clk(clk), .rst(rst), .cfg(cfg), .data(data), .mixed(mixed));

  always_ff @(posedge clk) begin
    if (rst) res_q <= '0;
    else     res_q <= mixed + 8'd1;
  end

  assign result = res_q;
endmodule
