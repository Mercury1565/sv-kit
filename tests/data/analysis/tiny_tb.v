// Original tiny design for svkit waveform-backend tests (not from any course project).
// Regenerate: iverilog -o tiny.vvp tiny_tb.v && vvp tiny.vvp            (writes tiny.vcd)
//             iverilog -DFST -o tiny.vvp tiny_tb.v && vvp tiny.vvp -fst (writes tiny.fst)
`timescale 1ns/1ps
module blinker (input clock, input reset, output reg [3:0] count, output reg [1:0] mode, output reg [7:0] late);
  always @(posedge clock) begin
    if (reset) begin
      count <= 4'd0;
      mode  <= 2'd0;
    end else begin
      count <= count + 4'd1;
      if (count == 4'd3) mode <= 2'd1;
      if (count == 4'd7) mode <= 2'd2;
      if (count == 4'd9) late <= 8'h5a;   // `late` is never reset: X until cycle 9 after reset
    end
  end
endmodule

module tiny_tb;
  reg clock = 0;
  reg reset = 1;
  wire [3:0] count;
  wire [1:0] mode;
  wire [7:0] late;
  real ratio = 0.0;
  blinker dut (.clock(clock), .reset(reset), .count(count), .mode(mode), .late(late));
  always #5 clock = ~clock;          // period 10 ns; rising edges at 5, 15, 25, ...
  always @(posedge clock) ratio = ratio + 0.25;
  initial begin
`ifdef FST
    $dumpfile("tiny.fst");
`else
    $dumpfile("tiny.vcd");
`endif
    $dumpvars(0, tiny_tb);
    #22 reset = 0;                   // deasserts at 22; first rising edge after: 25
    #200 $finish;
  end
endmodule
