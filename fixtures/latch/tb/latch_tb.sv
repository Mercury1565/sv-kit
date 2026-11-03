// svkit fixture testbench for `latch_hold` (original). +TEST=basic ; output/<test>.{out,vcd}
`timescale 1ns/1ps
module latch_tb;
    logic clock = 1'b0;
    logic reset, load;
    logic [7:0] data, q;
    string test;
    int fd, cycle;

    latch_hold dut (.*);

    always #5 clock = ~clock;

    always @(posedge clock) begin
        cycle <= cycle + 1;
        if (!reset) $fdisplay(fd, "@%0d load=%0d data=%02h q=%02h", cycle, load, data, q);
    end

    initial begin
        if (!$value$plusargs("TEST=%s", test)) test = "basic";
        fd = $fopen({"output/", test, ".out"}, "w");
        if (fd == 0) $fatal(1, "cannot open output file");
        $dumpfile({"output/", test, ".vcd"});
        $dumpvars(0, latch_tb);
        cycle = 0; reset = 1'b1; load = 1'b1; data = 8'h00;
        repeat (2) @(negedge clock);
        reset = 1'b0;
        if (test != "basic") $fatal(1, "unknown test %s", test);
        for (int i = 0; i < 16; i++) begin
            load = (i % 4) == 0;
            data = 8'(8'h21 * i + 3);
            @(negedge clock);
        end
        $fdisplay(fd, "done");
        $fclose(fd);
        $display("latch_tb: test %s finished", test);
        $finish;
    end
endmodule
