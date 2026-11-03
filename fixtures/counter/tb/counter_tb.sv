// svkit fixture testbench for `counter` (original).
// Plusargs: +TEST=<name> selects the stimulus; outputs go to output/<name>.{out,vcd}.
`timescale 1ns/1ps
module counter_tb;
    localparam int WIDTH = 4;
    logic clock = 1'b0;
    logic reset, clear, en;
    logic [WIDTH-1:0] count;
    logic wrap;
    string test, outf;
    int fd, cycle;

    counter #(.WIDTH(WIDTH)) dut (.*);

    always #5 clock = ~clock;

    always @(posedge clock) begin
        cycle <= cycle + 1;
        if (!reset) $fdisplay(fd, "@%0d count=%0d wrap=%0d", cycle, count, wrap);
    end

    initial begin
        if (!$value$plusargs("TEST=%s", test)) test = "basic";
        outf = {"output/", test, ".out"};
        fd = $fopen(outf, "w");
        if (fd == 0) $fatal(1, "cannot open %s", outf);
        $dumpfile({"output/", test, ".vcd"});
        $dumpvars(0, counter_tb);
        cycle = 0; reset = 1'b1; clear = 1'b0; en = 1'b0;
        repeat (2) @(negedge clock);
        reset = 1'b0;
        if (test == "basic") begin
            en = 1'b1;
            repeat (20) @(negedge clock);
        end else if (test == "enable") begin
            for (int i = 0; i < 24; i++) begin
                en = (i % 3) != 0;
                @(negedge clock);
            end
        end else if (test == "clear") begin
            en = 1'b1;
            repeat (6) @(negedge clock);
            clear = 1'b1;
            @(negedge clock);
            clear = 1'b0;
            repeat (6) @(negedge clock);
        end else begin
            $fatal(1, "unknown test %s", test);
        end
        $fdisplay(fd, "done");
        $fclose(fd);
        $display("counter_tb: test %s finished", test);
        $finish;
    end
endmodule
