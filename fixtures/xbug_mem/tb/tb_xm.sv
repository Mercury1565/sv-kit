`timescale 1ns/1ps
module tb_xm;
  logic       clk = 1'b0;
  logic       rst = 1'b1;
  logic       we = 1'b0;
  logic [2:0] waddr = 3'd0;
  logic [7:0] wdata = 8'd0;
  logic [2:0] raddr = 3'd0;
  logic [7:0] rd_out;
  logic [7:0] lut_out;

  xm_top dut (
    .clk(clk), .rst(rst), .we(we), .waddr(waddr), .wdata(wdata), .raddr(raddr),
    .rd_out(rd_out), .lut_out(lut_out)
  );

  always #5 clk = ~clk;

  initial begin
    $dumpfile("waves.vcd");
    $dumpvars(0, tb_xm);
    repeat (2) @(posedge clk);
    #1 rst = 1'b0;
    // write entries 0..3
    for (int i = 0; i < 4; i++) begin
      we = 1'b1; waddr = i[2:0]; wdata = 8'h10 + i[7:0];
      @(posedge clk); #1;
    end
    we = 1'b0;
    raddr = 3'd2;                // written: reads fine
    @(posedge clk); #1;
    raddr = 3'd5;                // never written: X
    repeat (2) @(posedge clk); #1;
    raddr = 3'd1;
    repeat (4) @(posedge clk);
    $display("rd_out=%h lut_out=%h", rd_out, lut_out);
    $finish;
  end
endmodule
