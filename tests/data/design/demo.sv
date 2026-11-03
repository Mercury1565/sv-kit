// Original test design for DesignGraph extraction (types, drivers, resets, memories, generate).
package demo_pkg;
  typedef enum logic [1:0] {S_IDLE = 2'd0, S_RUN = 2'd1, S_DONE = 2'd2} state_t;
  typedef struct packed { logic [3:0] tag; state_t st; logic [1:0] lo; } info_t;
  typedef struct packed { info_t inner; logic v; } outer_t;
endpackage

module demo_leaf #(parameter int W = 8) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [W-1:0] d,
  output logic [W-1:0] q
);
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) q <= '0;
    else        q <= d;
endmodule

module demo_top import demo_pkg::*; (
  input  logic       clk,
  input  logic       rst_n,
  input  logic [7:0] din,
  input  logic [2:0] idx,
  input  logic       we,
  output logic [7:0] dout,
  output state_t     state_o
);
  logic [7:0] mem [0:5];
  logic [7:0] a, b, sel_v;
  info_t      info;
  outer_t     outer;
  state_t     state;

  assign a = din + 8'd1;
  assign sel_v = we ? a : din;
  assign info = {din[3:0], state, din[1:0]};
  assign outer = {info, we};

  always_comb begin
    b = a;
    if (state == S_RUN) b = mem[idx];
    case (info.st)
      S_IDLE:  b = '0;
      default: ;
    endcase
  end

  always_ff @(posedge clk) begin
    if (we) mem[idx] <= a;
  end

  always_ff @(posedge clk) begin
    if (!rst_n) state <= S_IDLE;
    else        state <= S_RUN;
  end

  demo_leaf #(.W(8)) u_leaf (.clk(clk), .rst_n(rst_n), .d(b), .q(dout));

  genvar i;
  for (i = 0; i < 2; i++) begin : g_bit
    logic w;
    assign w = din[i];
  end

  demo_leaf #(.W(1)) u_arr [1:0] (.clk(clk), .rst_n(rst_n), .d(din[1:0]), .q());

  assign state_o = state;
endmodule
