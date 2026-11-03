// svkit fixture testbench for the "pipe5" toy pipeline (original).
//
// Plusargs: +TEST=<name> loads programs/<name>.mem (override with +PROG=<file>).
// Outputs (in output/):
//   <name>.wb   one line per architectural update, in retirement order:
//               "@<cycle> r<n>=<hex>"  register write-back
//               "@<cycle> M[<addr>]=<hex>"  store
//   <name>.cpi  cycles / instrs / stall counters
//   <name>.vcd  waveform
`timescale 1ns/1ps
module p5_tb;
    localparam int MAX_CYCLES = 5000;

    logic        clock = 1'b0;
    logic        reset;
    logic [7:0]  imem_addr, dmem_addr, dmem_wdata, dmem_rdata, wb_data;
    logic [15:0] imem_data;
    logic        dmem_we, wb_valid, wb_we, halted, stall_load_use, flush_branch;
    logic [2:0]  wb_rd;

    logic [15:0] imem [256];
    logic [7:0]  dmem [256];

    string test, prog;
    int    wb_fd, cycle, instrs, stalls, flushes;

    p5_core dut (.*);

    assign imem_data  = imem[imem_addr];
    assign dmem_rdata = dmem[dmem_addr];

    always #5 clock = ~clock;

    always @(posedge clock) begin
        if (dmem_we) dmem[dmem_addr] <= dmem_wdata;
        if (!reset) begin
            cycle <= cycle + 1;
            if (wb_valid) instrs <= instrs + 1;
            if (stall_load_use) stalls <= stalls + 1;
            if (flush_branch) flushes <= flushes + 1;
            if (wb_we) $fdisplay(wb_fd, "@%0d r%0d=%02h", cycle, wb_rd, wb_data);
            if (dmem_we) $fdisplay(wb_fd, "@%0d M[%02h]=%02h", cycle, dmem_addr, dmem_wdata);
        end
    end

    initial begin
        int cpi_fd;
        if (!$value$plusargs("TEST=%s", test)) test = "sum";
        if (!$value$plusargs("PROG=%s", prog)) prog = {"programs/", test, ".mem"};
        for (int i = 0; i < 256; i++) begin
            imem[i] = 16'hF000;   // HALT everywhere by default
            dmem[i] = 8'h00;
        end
        $readmemh(prog, imem);
        wb_fd = $fopen({"output/", test, ".wb"}, "w");
        if (wb_fd == 0) $fatal(1, "cannot open output/%s.wb", test);
        $dumpfile({"output/", test, ".vcd"});
        $dumpvars(0, p5_tb);
        cycle = 0; instrs = 0; stalls = 0; flushes = 0;
        reset = 1'b1;
        repeat (2) @(negedge clock);
        reset = 1'b0;
        while (!halted && cycle < MAX_CYCLES) @(negedge clock);
        $fclose(wb_fd);
        cpi_fd = $fopen({"output/", test, ".cpi"}, "w");
        $fdisplay(cpi_fd, "cycles = %0d", cycle);
        $fdisplay(cpi_fd, "instrs = %0d", instrs);
        $fdisplay(cpi_fd, "load_use_stalls = %0d", stalls);
        $fdisplay(cpi_fd, "branch_flushes = %0d", flushes);
        $fclose(cpi_fd);
        if (!halted) $fatal(1, "p5_tb: %s did not halt within %0d cycles", test, MAX_CYCLES);
        $display("p5_tb: %s halted after %0d cycles, %0d instructions", test, cycle, instrs);
        $finish;
    end
endmodule
