// svkit fixture: parameterized up-counter (original design).
// - asynchronous active-high `reset`
// - synchronous `clear`
// - counts when `en` is high; `wrap` pulses for one cycle when the count rolls over
module counter #(
    parameter int WIDTH = 8
) (
    input  logic             clock,
    input  logic             reset,
    input  logic             clear,
    input  logic             en,
    output logic [WIDTH-1:0] count,
    output logic             wrap
);
    logic [WIDTH-1:0] next_count;

    assign next_count = count + 1'b1;

    always_ff @(posedge clock or posedge reset) begin
        if (reset) begin
            count <= '0;
            wrap  <= 1'b0;
        end else if (clear) begin
            count <= '0;
            wrap  <= 1'b0;
        end else begin
            wrap <= en && (count == {WIDTH{1'b1}});
            if (en) count <= next_count;
        end
    end
endmodule
