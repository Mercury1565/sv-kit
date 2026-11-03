# EXPECTED TO FAIL on the shipped pipe5 RTL (planted bug, see rtl/p5_hazard.sv):
# a store immediately after a load of its data register stores the load ADDRESS.
        li   r1, 0x30
        li   r2, 0x5A
        st   r2, 0(r1)       # M[0x30] = 0x5A
        li   r3, 0x31
        li   r4, 0x11
        add  r4, r4, r4
        ld   r5, 0(r1)       # r5 = M[0x30] = 0x5A
        st   r5, 0(r3)       # BUG: stores 0x30 (the address) instead of 0x5A
        ld   r6, 0(r3)
        add  r7, r6, r4
        halt
