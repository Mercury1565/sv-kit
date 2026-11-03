# fill M[0x20..0x27] with 7*i+1, then copy it to M[0x40..0x47]
        li   r1, 0x20        # src pointer
        li   r2, 0x40        # dst pointer
        li   r3, 1           # value
        li   r4, 8           # count
fill:   st   r3, 0(r1)
        addi r3, r3, 7
        addi r1, r1, 1
        addi r4, r4, -1
        bne  r4, r0, fill
        li   r1, 0x20
        li   r4, 8
copy:   ld   r5, 0(r1)
        addi r1, r1, 1       # independent instruction between ld and st (no hazard)
        st   r5, 0(r2)
        addi r2, r2, 1
        addi r4, r4, -1
        bne  r4, r0, copy
        jmp  end
        li   r6, 0x55        # skipped by the jump
end:    halt
