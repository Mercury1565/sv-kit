`timescale 1ns/1ps
// Registers a configuration word and mixes it with the data path.
module xi_mixer (
  input  logic       clk,
  input  logic       rst,
  input  logic [3:0] cfg,
  input  logic [7:0] data,
  output logic [7:0] mixed
);
  logic [3:0] cfg_q;
  logic [7:0] mix;

  always_ff @(posedge clk) begin
    if (rst) cfg_q <= '0;
    else     cfg_q <= cfg;
  end

  always_comb begin
    mix = data ^ {cfg_q, cfg_q};
    if (data[7])
      mix = data;
  end

  assign mixed = mix;
endmodule
