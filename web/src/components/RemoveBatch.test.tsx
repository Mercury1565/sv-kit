import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError, httpTransport, setTransport } from "../api/client";
import type { Group } from "../api/types";
import { RemoveBatchConfirm } from "./RemoveBatch";

afterEach(() => setTransport(httpTransport));

const G = { id: "g_0007", kind: "sim-all", created_at: 1, status: "done", params: {}, result: null, name: "oops, ran twice" } as Group;

describe("RemoveBatchConfirm", () => {
  it("names the batch and removes it on confirm", async () => {
    const calls: string[] = [];
    setTransport({ request: async (method, path) => (calls.push(`${method} ${path}`), { deleted: "g_0007", runs: [], changed: [] }) }, true);
    const onRemoved = vi.fn();
    render(<RemoveBatchConfirm group={G} runs={12} onRemoved={onRemoved} onCancel={() => {}} />);
    expect(screen.getByRole("alertdialog").textContent).toContain("oops, ran twice");
    expect(screen.getByRole("alertdialog").textContent).toContain("its 12 runs");
    fireEvent.click(screen.getByRole("button", { name: "Remove batch" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    expect(calls).toEqual(["DELETE /api/groups/g_0007"]);
  });

  it("shows why the server refused and keeps the dialog", async () => {
    setTransport({ request: async (_m, path) => { throw new ApiError(409, "batch g_0007 still has 1 queued or running run", path); } }, true);
    const onRemoved = vi.fn();
    render(<RemoveBatchConfirm group={G} onRemoved={onRemoved} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove batch" }));
    await screen.findByText(/Not removed: batch g_0007 still has 1 queued/);
    expect(onRemoved).not.toHaveBeenCalled();
  });
});
