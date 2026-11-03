// svkit fixture: synchronous FIFO (original design).
// DEPTH must be a power of two. `full`/`empty` are registered-pointer based;
// a push while full or a pop while empty is ignored and flagged on `overflow`/`underflow`.
module fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 8
) (
    input  logic             clock,
    input  logic             reset,     // synchronous, active high
    input  logic             push,
    input  logic [WIDTH-1:0] din,
    input  logic             pop,
    output logic [WIDTH-1:0] dout,
    output logic             full,
    output logic             empty,
    output logic             overflow,
    output logic             underflow,
    output logic [$clog2(DEPTH):0] level
);
    localparam int AW = $clog2(DEPTH);

    logic [WIDTH-1:0] mem [DEPTH];
    logic [AW:0] wptr, rptr;          // one extra bit distinguishes full from empty
    logic do_push, do_pop;

    assign full  = (wptr[AW] != rptr[AW]) && (wptr[AW-1:0] == rptr[AW-1:0]);
    assign empty = (wptr == rptr);
    assign level = wptr - rptr;
    assign dout  = mem[rptr[AW-1:0]];

    assign do_push = push && (!full || pop);
    assign do_pop  = pop && !empty;

    always_ff @(posedge clock) begin
        if (reset) begin
            wptr      <= '0;
            rptr      <= '0;
            overflow  <= 1'b0;
            underflow <= 1'b0;
        end else begin
            if (do_push) wptr <= wptr + 1'b1;
            if (do_pop)  rptr <= rptr + 1'b1;
            overflow  <= push && full && !pop;
            underflow <= pop && empty;
        end
    end

    // storage has no reset (as in most real FIFOs)
    always_ff @(posedge clock) begin
        if (do_push) mem[wptr[AW-1:0]] <= din;
    end
endmodule
