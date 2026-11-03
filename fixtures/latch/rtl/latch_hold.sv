// svkit fixture: a small datapath with an INFERRED level-sensitive latch (original design).
// `held` is only assigned when `load` is high inside a combinational block, so synthesis
// and lint tools infer a latch (intentional: target for lint / X features).
module latch_hold (
    input  logic       clock,
    input  logic       reset,
    input  logic       load,
    input  logic [7:0] data,
    output logic [7:0] q
);
    logic [7:0] held;

    // intentional incomplete assignment -> latch (transparent while load = 1)
    always @(*) begin
        if (load) held = data;
    end

    always_ff @(posedge clock) begin
        if (reset) q <= 8'h00;
        else       q <= held ^ {held[6:0], held[7]};
    end
endmodule
