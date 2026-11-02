`timescale 1ns/1ps
// Top of the xbug_noreset fixture.
// PLANTED BUG (class 1, never assigned): mode_q is declared but never assigned,
// so the flag path goes X whenever in_valid is high.
module xn_top (
  input  logic       clk,
  input  logic       rst,
  input  logic       in_valid,
  input  logic [7:0] in_data,
  output logic [7:0] out_data,
  output logic       out_flag
);
  localparam logic [7:0] BIAS = 8'd3;

  logic [7:0] scaled;
  logic [3:0] mode_q;
  logic       flag;
  logic       flag_q;
  logic [7:0] out_q;

  xn_scaler u_scaler (
    .clk      (clk),
    .rst      (rst),
    .in_valid (in_valid),
    .in_data  (in_data),
    .bias     (BIAS),
    .scaled   (scaled)
  );

  assign flag = (mode_q == 4'd3) & in_valid;

  always_ff @(posedge clk) begin
    if (rst) begin
      out_q  <= '0;
      flag_q <= 1'b0;
    end else begin
      out_q  <= scaled;
      flag_q <= flag;
    end
  end

  assign out_data = out_q;
  assign out_flag = flag_q;
endmodule
