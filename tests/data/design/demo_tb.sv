module demo_tb;
  logic clk = 0, rst_n = 0, we = 0;
  logic [7:0] din = 0;
  logic [2:0] idx = 0;
  logic [7:0] dout;
  demo_pkg::state_t st;
  demo_top dut (.clk(clk), .rst_n(rst_n), .din(din), .idx(idx), .we(we), .dout(dout), .state_o(st));
  always #5 clk = ~clk;
endmodule
