`timescale 1ns/1ps
module tb_xn;
  logic       clk = 1'b0;
  logic       rst = 1'b1;
  logic       in_valid = 1'b0;
  logic [7:0] in_data = 8'd0;
  logic [7:0] out_data;
  logic       out_flag;

  xn_top dut (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_data(in_data),
    .out_data(out_data), .out_flag(out_flag)
  );

  always #5 clk = ~clk;

  initial begin
    $dumpfile("waves.vcd");
    $dumpvars(0, tb_xn);
    repeat (2) @(posedge clk);
    #1 rst = 1'b0;
    repeat (4) @(posedge clk);   // in_valid stays low: stage1_q is never loaded
    #1 in_valid = 1'b1;
    in_data = 8'd7;
    repeat (4) @(posedge clk);
    #1 in_valid = 1'b0;
    repeat (3) @(posedge clk);
    $display("out_data=%h out_flag=%b", out_data, out_flag);
    $finish;
  end
endmodule
