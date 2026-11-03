# first 10 Fibonacci numbers (mod 256) into M[0x00..0x09]
        li   r1, 0
        li   r2, 1
        li   r3, 0           # index / address
        li   r4, 10
loop:   st   r1, 0(r3)
        add  r5, r1, r2
        add  r1, r2, r0
        add  r2, r5, r0
        addi r3, r3, 1
        sub  r6, r4, r3
        bne  r6, r0, loop
        ld   r7, 9(r0)
        addi r7, r7, 1       # load-use stall
        halt
