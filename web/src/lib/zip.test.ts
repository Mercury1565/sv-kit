import { describe, expect, it } from "vitest";
import { crc32, dataUrlBytes, zipBytes } from "./zip";

const u32 = (b: Uint8Array, o: number) => new DataView(b.buffer, b.byteOffset).getUint32(o, true);
const u16 = (b: Uint8Array, o: number) => new DataView(b.buffer, b.byteOffset).getUint16(o, true);

describe("zip", () => {
  it("computes the standard CRC-32", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
  });

  it("decodes a base64 data URL", () => {
    expect(Array.from(dataUrlBytes("data:image/png;base64,AAEC/w=="))).toEqual([0, 1, 2, 255]);
  });

  it("writes a readable stored archive with UTF-8 names and unique entries", () => {
    const z = zipBytes([
      { name: "fib/cpi.png", data: new Uint8Array([1, 2, 3]) },
      { name: "fib/runs.csv", data: "a,b\r\n" },
      { name: "fib/cpi.png", data: new Uint8Array([9]) },
      { name: "ünïcode/x.txt", data: "é" },
    ]);
    expect(u32(z, 0)).toBe(0x04034b50);
    const eocd = z.length - 22;
    expect(u32(z, eocd)).toBe(0x06054b50);
    expect(u16(z, eocd + 10)).toBe(4);
    // walk the central directory: names, sizes, and each local header at its offset
    let o = u32(z, eocd + 16);
    const dec = new TextDecoder();
    const names: string[] = [];
    for (let i = 0; i < 4; i++) {
      expect(u32(z, o)).toBe(0x02014b50);
      expect(u16(z, o + 8) & 0x0800).toBe(0x0800);
      const n = u16(z, o + 28);
      const size = u32(z, o + 20);
      const local = u32(z, o + 42);
      const name = dec.decode(z.slice(o + 46, o + 46 + n));
      names.push(name);
      expect(u32(z, local)).toBe(0x04034b50);
      const data = z.slice(local + 30 + n, local + 30 + n + size);
      expect(crc32(data)).toBe(u32(z, o + 16));
      o += 46 + n;
    }
    expect(names).toEqual(["fib/cpi.png", "fib/runs.csv", "fib/cpi (2).png", "ünïcode/x.txt"]);
  });
});
