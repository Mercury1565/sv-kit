import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError, httpTransport, setTransport, type Transport } from "../api/client";
import { MockBackend } from "../api/mock";
import type { RunSummary } from "../api/types";
import { RemoveRuns } from "./RemoveRuns";

afterEach(() => setTransport(httpTransport));

const base = { kind: "synth", adapter: "dc", knobs: {}, artifacts: {}, tags: [], created_at: 1 };
const RUNS = [
  { ...base, id: "r_0001", status: "done", name: "first try" },
  { ...base, id: "r_0002", status: "done" },
  { ...base, id: "r_0003", status: "running" },
] as RunSummary[];

function serve(fail: Record<string, string> = {}) {
  const deleted: string[] = [];
  const t: Transport = {
    request: async (method, path) => {
      const id = path.split("/").pop()!;
      if (method !== "DELETE") return {};
      if (fail[id]) throw new ApiError(409, fail[id], path);
      deleted.push(id);
      return { deleted: id, changed: [] };
    },
  };
  setTransport(t, true);
  return deleted;
}

describe("RemoveRuns", () => {
  it("is disabled until runs are ticked", () => {
    render(<RemoveRuns runs={RUNS} selected={new Set()} onRemoved={() => {}} />);
    expect((screen.getByRole("button", { name: "Remove" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("asks first, then removes the ticked runs and skips running ones", async () => {
    const deleted = serve();
    const onRemoved = vi.fn();
    render(<RemoveRuns runs={RUNS} selected={new Set(["r_0001", "r_0002", "r_0003"])} onRemoved={onRemoved} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove (3)" }));
    expect(deleted).toEqual([]);                                   // nothing happens before confirming
    expect(screen.getByRole("alertdialog").textContent).toContain("cannot be undone");
    expect(screen.getByText(/1 still running will be kept/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove 2 runs" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(["r_0001", "r_0002"]));
    expect(deleted).toEqual(["r_0001", "r_0002"]);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("Cancel removes nothing; one run is named", () => {
    const deleted = serve();
    render(<RemoveRuns runs={RUNS} selected={new Set(["r_0001"])} onRemoved={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove (1)" }));
    expect(screen.getByRole("alertdialog").textContent).toContain("r_0001 (“first try”) and its files?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(deleted).toEqual([]);
  });

  it("reports runs the server refused and keeps the dialog open", async () => {
    serve({ r_0002: "run r_0002 is still running: cancel it first" });
    const onRemoved = vi.fn();
    render(<RemoveRuns runs={RUNS} selected={new Set(["r_0001", "r_0002"])} onRemoved={onRemoved} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove (2)" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove 2 runs" }));
    await screen.findByText(/Not removed: r_0002/);
    expect(onRemoved).toHaveBeenCalledWith(["r_0001"]);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
  });

  it("only running runs: nothing to remove", () => {
    serve();
    render(<RemoveRuns runs={RUNS} selected={new Set(["r_0003"])} onRemoved={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove (1)" }));
    expect(screen.getByText(/still running: cancel them first/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Remove \d|^Remove run$/ })).toBeNull();
  });

  it("mock backend removes a run and hands its result to a reuser", async () => {
    setTransport(new MockBackend("demo"), true);
    const all = await api.runs({ kind: "synth", limit: 1000 });
    const orig = all.find((r) => all.some((x) => x.cached_from === r.id && x.status === "done") && r.status === "done");
    const target = orig ?? all.find((r) => r.status === "done")!;
    const res = await api.deleteRun(target.id);
    expect(res.deleted).toBe(target.id);
    const after = await api.runs({ kind: "synth", limit: 1000 });
    expect(after.some((r) => r.id === target.id)).toBe(false);
    expect(after.some((r) => r.cached_from === target.id)).toBe(false);
    await expect(api.deleteRun(target.id)).rejects.toBeInstanceOf(ApiError);
  });
});
