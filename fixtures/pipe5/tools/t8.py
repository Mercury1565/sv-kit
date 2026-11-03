#!/usr/bin/env python3
"""Assembler + instruction-set simulator for the toy T8 ISA used by the pipe5 fixture.

Original tool written for svkit tests (see ../rtl/p5_pkg.sv for the encoding).

    python3 tools/t8.py asm programs/sum.s          -> writes programs/sum.mem
    python3 tools/t8.py iss programs/sum.mem        -> prints the architectural trace
                                                       ("r<n>=<hex>" / "M[<addr>]=<hex>")
Assembly syntax: one instruction per line, `label:` prefixes, `#` or `;` comments.
    add rd, rs1, rs2   sub/and/or/xor/slt likewise
    li rd, imm8        addi rd, rs1, imm6     ld rd, imm6(rs1)     st rd, imm6(rs1)
    beq rd, rs1, label bne rd, rs1, label     jmp label            nop  halt
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

OPS = {"nop": 0, "add": 1, "sub": 2, "and": 3, "or": 4, "xor": 5, "li": 6, "addi": 7, "ld": 8, "st": 9,
       "beq": 0xA, "bne": 0xB, "jmp": 0xC, "slt": 0xD, "halt": 0xF}


def _reg(t: str) -> int:
    m = re.fullmatch(r"r([0-7])", t.strip().lower())
    if not m:
        raise ValueError(f"bad register {t!r}")
    return int(m.group(1))


def _int(t: str) -> int:
    return int(t.strip(), 0)


def assemble(text: str) -> list[int]:
    lines, labels = [], {}
    for raw in text.splitlines():
        line = re.split(r"[#;]", raw, maxsplit=1)[0].strip()
        while (m := re.match(r"([A-Za-z_]\w*)\s*:\s*(.*)", line)):
            labels[m.group(1)] = len(lines)
            line = m.group(2).strip()
        if line:
            lines.append(line)
    out = []
    for pc, line in enumerate(lines):
        mn, _, rest = line.partition(" ")
        mn = mn.lower()
        args = [a.strip() for a in rest.split(",")] if rest.strip() else []
        op = OPS[mn]
        w = op << 12
        if mn in ("add", "sub", "and", "or", "xor", "slt"):
            w |= _reg(args[0]) << 9 | _reg(args[1]) << 6 | _reg(args[2]) << 3
        elif mn == "li":
            w |= _reg(args[0]) << 9 | (_int(args[1]) & 0xFF)
        elif mn == "addi":
            w |= _reg(args[0]) << 9 | _reg(args[1]) << 6 | (_int(args[2]) & 0x3F)
        elif mn in ("ld", "st"):
            m = re.fullmatch(r"(-?\w+)\((r[0-7])\)", args[1].replace(" ", ""))
            if not m:
                raise ValueError(f"bad address {args[1]!r} in {line!r}")
            w |= _reg(args[0]) << 9 | _reg(m.group(2)) << 6 | (_int(m.group(1)) & 0x3F)
        elif mn in ("beq", "bne"):
            off = labels[args[2]] - (pc + 1) if args[2] in labels else _int(args[2])
            if not -32 <= off <= 31:
                raise ValueError(f"branch offset {off} out of range in {line!r}")
            w |= _reg(args[0]) << 9 | _reg(args[1]) << 6 | (off & 0x3F)
        elif mn == "jmp":
            w |= (labels[args[0]] if args[0] in labels else _int(args[0])) & 0xFF
        out.append(w)
    return out


def iss(words: list[int], max_steps: int = 10000) -> list[str]:
    imem = words + [0xF000] * (256 - len(words))
    regs, mem, pc, trace = [0] * 8, [0] * 256, 0, []
    sext = lambda v: v - 64 if v & 0x20 else v
    for _ in range(max_steps):
        w = imem[pc]
        op, rd, rs1, rs2, imm8 = w >> 12, (w >> 9) & 7, (w >> 6) & 7, (w >> 3) & 7, w & 0xFF
        a, b, d = regs[rs1], regs[rs2], regs[rd]
        npc = (pc + 1) & 0xFF
        res = None
        if op == 0xF:
            return trace
        if op == 1: res = a + b
        elif op == 2: res = a - b
        elif op == 3: res = a & b
        elif op == 4: res = a | b
        elif op == 5: res = a ^ b
        elif op == 6: res = imm8
        elif op == 7: res = a + sext(imm8 & 0x3F)
        elif op == 8: res = mem[(a + sext(imm8 & 0x3F)) & 0xFF]
        elif op == 9:
            addr = (a + sext(imm8 & 0x3F)) & 0xFF
            mem[addr] = d
            trace.append(f"M[{addr:02x}]={d:02x}")
        elif op == 0xA and d == a: npc = (pc + 1 + sext(imm8 & 0x3F)) & 0xFF
        elif op == 0xB and d != a: npc = (pc + 1 + sext(imm8 & 0x3F)) & 0xFF
        elif op == 0xC: npc = imm8
        elif op == 0xD: res = int(a < b)
        if res is not None and rd != 0:
            regs[rd] = res & 0xFF
            trace.append(f"r{rd}={regs[rd]:02x}")
        pc = npc
    raise RuntimeError("program did not halt")


def read_mem(path: Path) -> list[int]:
    text = "\n".join(line.split("//", 1)[0] for line in path.read_text().splitlines())
    return [int(t, 16) for t in text.split()]


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("asm", "iss"):
        print(__doc__)
        return 2
    p = Path(argv[2])
    if argv[1] == "asm":
        words = assemble(p.read_text())
        p.with_suffix(".mem").write_text(f"// assembled from {p.name} by tools/t8.py\n"
                                         + "".join(f"{w:04x}\n" for w in words))
    else:
        for line in iss(read_mem(p)):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
