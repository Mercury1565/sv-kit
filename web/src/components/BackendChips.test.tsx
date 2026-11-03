import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BackendChips } from "../App";

describe("BackendChips", () => {
  it("shows a chip only for flows that fell back", () => {
    render(<BackendChips backends={{
      sim: { flow: "vcs", runs_on: "verilator", note: "vcs not found on this host", available: true },
      synth: { flow: "yosys", runs_on: "yosys", note: null, available: true },
    }} />);
    expect(screen.getByText(/sim: verilator/)).toBeTruthy();
    expect(screen.getByText(/vcs not found/).closest("[title]")?.getAttribute("title")).toContain("not found on this host");
    expect(screen.queryByText(/synth:/)).toBeNull();
  });

  it("renders nothing when every flow runs natively", () => {
    const { container } = render(<BackendChips backends={{ sim: { flow: "verilator", runs_on: "verilator", available: true } }} />);
    expect(container.textContent).toBe("");
  });
});
