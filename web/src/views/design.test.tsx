import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import type { RunSummary } from "../api/types";
import DesignView from "./Design";

const run = (id: string, status: string, kind = "synth") =>
  ({ id, kind, adapter: "yosys", status, created_at: 1, knobs: {}, artifacts: {}, tags: [] }) as RunSummary;

afterEach(() => {
  vi.restoreAllMocks();
  location.hash = "";
});

describe("Design: Gates tab without a chosen path", () => {
  it("is clickable and opens the newest finished synthesis run's worst path", async () => {
    vi.spyOn(api, "runs").mockResolvedValue([run("r_0009", "error"), run("r_0007", "done"), run("r_0003", "done")]);
    render(<DesignView query={new URLSearchParams("gates=1")} />);
    expect((screen.getByRole("button", { name: "Gates" }) as HTMLButtonElement).disabled).toBe(false);
    await waitFor(() => expect(location.hash).toBe("#/design?cone=r_0007&path=0"));
  });

  it("says how to get a synthesis run when there is none", async () => {
    vi.spyOn(api, "runs").mockResolvedValue([run("r_0001", "error")]);
    render(<DesignView query={new URLSearchParams("gates=1")} />);
    expect(await screen.findByText("No synthesis run yet")).toBeTruthy();
    expect(screen.getByText(/svkit synth/)).toBeTruthy();
  });
});
