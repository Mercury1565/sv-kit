# sum 1..10 with a backwards branch loop; store the result at M[0x10]
        li   r1, 10          # counter
        li   r2, 0           # accumulator
loop:   add  r2, r2, r1      # RAW on r2 (EX/MEM forward) every iteration
        addi r1, r1, -1
        bne  r1, r0, loop    # depends on the addi right before it
        li   r3, 0x10
        st   r2, 0(r3)
        halt
