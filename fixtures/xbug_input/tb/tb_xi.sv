`timescale 1ns/1ps
module tb_xi;
  logic       clk = 1'b0;
  logic       rst = 1'b1;
  logic       cfg_drive = 1'b0;     // PLANTED BUG (class 4): cfg is left floating at first
  logic [3:0] cfg_val = 4'd5;
  wire  [3:0] cfg;
  logic [7:0] data = 8'h3c;
  logic [7:0] result;

  assign cfg = cfg_drive ? cfg_val : 4'bzzzz;

  xi_top dut (.clk(clk), .rst(rst), .cfg(cfg), .data(data), .result(result));

  always #5 clk = ~clk;

  initial begin
    $dumpfile("waves.vcd");
    $dumpvars(0, tb_xi);
    repeat (2) @(posedge clk);
    #1 rst = 1'b0;
    repeat (4) @(posedge clk);
    #1 cfg_drive = 1'b1;
    repeat (4) @(posedge clk);
    $display("result=%h", result);
    $finish;
  end
endmodule
