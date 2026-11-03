# back-to-back RAW chains, load-use stalls, and a taken/not-taken branch mix
        li   r1, 3
        add  r2, r1, r1      # EX/MEM -> EX forward
        add  r3, r2, r1      # EX/MEM and MEM/WB forward
        sub  r4, r3, r2
        xor  r5, r4, r3
        st   r5, 0(r0)       # M[0] = r5 (r5 produced by the previous instruction)
        st   r3, 1(r0)
        ld   r6, 0(r0)
        add  r7, r6, r1      # load-use: one stall
        ld   r1, 1(r0)
        nop
        add  r2, r1, r7      # load two ahead: MEM/WB forward, no stall
        slt  r3, r1, r2
        beq  r3, r0, skip    # not taken (r3 = 1)
        or   r4, r2, r3
skip:   and  r5, r4, r2
        beq  r0, r0, done    # always taken
        li   r6, 0xEE        # flushed
        li   r7, 0xEE        # flushed
done:   st   r5, 2(r0)
        halt
