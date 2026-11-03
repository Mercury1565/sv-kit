`timescale 1ns/1ps
// Two-stage scaler. PLANTED BUG (class 1): stage1_q has no reset and is only
// loaded when in_valid is high, so it is X until the first valid sample.
// stage2_q IS reset, but after reset it samples the X from stage1_q.
module xn_scaler (
  input  logic       clk,
  input  logic       rst,
  input  logic       in_valid,
  input  logic [7:0] in_data,
  input  logic [7:0] bias,
  output logic [7:0] scaled
);
  logic [7:0] stage1_q;
  logic [7:0] stage2_q;

  always_ff @(posedge clk) begin
    if (in_valid)
      stage1_q <= in_data << 1;
  end

  always_ff @(posedge clk) begin
    if (rst)
      stage2_q <= '0;
    else
      stage2_q <= stage1_q + bias;
  end

  assign scaled = stage2_q;
endmodule
