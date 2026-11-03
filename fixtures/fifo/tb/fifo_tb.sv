// svkit fixture testbench for `fifo` (original).
// +TEST=fill_drain | interleave | random ; outputs output/<test>.{out,vcd}
`timescale 1ns/1ps
module fifo_tb;
    localparam int WIDTH = 8, DEPTH = 8;
    logic clock = 1'b0;
    logic reset, push, pop;
    logic [WIDTH-1:0] din, dout;
    logic full, empty, overflow, underflow;
    logic [$clog2(DEPTH):0] level;
    logic [15:0] lfsr;
    string test;
    int fd, cycle;

    fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH)) dut (.*);

    always #5 clock = ~clock;

    // log every cycle in which something happens
    always @(posedge clock) begin
        cycle <= cycle + 1;
        if (!reset && (push || pop || overflow || underflow))
            $fdisplay(fd, "@%0d push=%0d din=%02h pop=%0d dout=%02h level=%0d full=%0d empty=%0d ovf=%0d unf=%0d",
                      cycle, push, din, pop, (pop && !empty) ? dout : 8'h00, level, full, empty, overflow, underflow);
    end

    task automatic step(input logic p, input logic [7:0] d, input logic q);
        push = p; din = d; pop = q;
        @(negedge clock);
    endtask

    initial begin
        if (!$value$plusargs("TEST=%s", test)) test = "fill_drain";
        fd = $fopen({"output/", test, ".out"}, "w");
        if (fd == 0) $fatal(1, "cannot open output file");
        $dumpfile({"output/", test, ".vcd"});
        $dumpvars(0, fifo_tb);
        cycle = 0; reset = 1'b1; push = 0; pop = 0; din = 0; lfsr = 16'hACE1;
        repeat (2) @(negedge clock);
        reset = 1'b0;
        if (test == "fill_drain") begin
            for (int i = 0; i < DEPTH + 2; i++) step(1, 8'(8'h10 + i), 0);   // two pushes overflow
            for (int i = 0; i < DEPTH + 2; i++) step(0, 8'h00, 1);           // two pops underflow
        end else if (test == "interleave") begin
            for (int i = 0; i < 12; i++) step(1, 8'(i * 3), i > 2);
            for (int i = 0; i < 6; i++) step(0, 8'h00, 1);
        end else if (test == "random") begin
            for (int i = 0; i < 40; i++) begin
                lfsr = {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
                step(lfsr[0], lfsr[11:4], lfsr[1] & lfsr[2]);
            end
        end else begin
            $fatal(1, "unknown test %s", test);
        end
        step(0, 8'h00, 0);
        $fdisplay(fd, "done");
        $fclose(fd);
        $display("fifo_tb: test %s finished", test);
        $finish;
    end
endmodule
