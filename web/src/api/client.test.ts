import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, httpTransport, setTransport } from "./client";
import { MockBackend } from "./mock";
import { parseEvent } from "./ws";

afterEach(() => {
  vi.unstubAllGlobals();
  setTransport(httpTransport);
});

describe("http transport", () => {
  it("encodes query, parses JSON and maps errors to ApiError", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.startsWith("/api/runs/r_9999")) return new Response(JSON.stringify({ detail: "no run r_9999" }), { status: 404 });
        if (url.startsWith("/api/design")) return new Response("Internal Server Error", { status: 500 });
        return new Response(JSON.stringify([{ id: "r_0001", kind: "sim", tags: [] }]), { status: 200 });
      }),
    );
    const runs = await api.runs({ kind: "sim", status: undefined, limit: 5 });
    expect(runs[0].id).toBe("r_0001");
    expect(calls[0]).toBe("/api/runs?kind=sim&limit=5");

    const e404 = await api.run("r_9999").catch((e) => e);
    expect(e404).toBeInstanceOf(ApiError);
    expect(e404.status).toBe(404);
    expect(e404.detail).toBe("no run r_9999");
    expect(e404.notAvailable).toBe(true);

    const e500 = await api.hierarchy().catch((e) => e);
    expect(e500.status).toBe(500);
    expect(e500.notAvailable).toBe(false);
  });

  it("joins compare ids and posts job bodies", async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.push({ url, init });
        return new Response(JSON.stringify({ job_id: "j_1" }), { status: 200 });
      }),
    );
    await api.compare(["r_1", "r_2"]);
    expect(seen[0].url).toBe("/api/compare?ids=r_1%2Cr_2");
    await api.submitJob("sweep", { tol: 50 });
    expect(seen[1].init?.method).toBe("POST");
    expect(JSON.parse(String(seen[1].init?.body))).toEqual({ kind: "sweep", args: { tol: 50 } });
  });

  it("allRuns pages through 5000-run pages", async () => {
    const be = new MockBackend("worst");
    setTransport(be, true);
    const all = await api.allRuns();
    expect(all.length).toBe(10000);
    expect(all[0]).not.toHaveProperty("metrics");
  });
});

describe("events", () => {
  it("parses known event types and drops junk", () => {
    expect(parseEvent('{"type":"run.status","run_id":"r_1","status":"passed"}')).toMatchObject({ type: "run.status" });
    expect(parseEvent({ type: "job.updated", job: { job_id: "j" } })?.type).toBe("job.updated");
    expect(parseEvent('{"type":"nope"}')).toBeNull();
    expect(parseEvent("not json")).toBeNull();
  });
});

describe("mock backend", () => {
  it("serves NaN metrics as null (like JSON from Python with allow_nan off)", async () => {
    const be = new MockBackend("worst");
    const r = (await be.request("GET", "/api/runs/r_0001")) as any;
    expect(r.metrics.power.total).toBeNull();
  });
  it("downsamples dense signals with '*' markers", async () => {
    const be = new MockBackend("demo");
    const d = (await be.request("POST", "/api/waves/r_0001/data", {}, { signals: ["tb.clk"], t0: 0, t1: 100000, max_changes: 100 })) as any;
    const sig = d.signals["tb.clk"];
    expect(sig.truncated).toBe(true);
    expect(sig.changes.length).toBeLessThanOrEqual(2 * 100 + 1);
    expect(sig.changes.some((c: [number, string]) => c[1] === "*")).toBe(true);
  });
});
