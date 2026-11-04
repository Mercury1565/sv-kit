import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BackendChips } from "../App";

describe("BackendChips", () => {
  it("shows a chip only for flows that fell back", () => {
    render(<BackendChips backends={{
      sim: { flow: "vcs", configured: "vcs", missing: ["vcs"], runs_on: "verilator", note: "vcs not found on this host", available: true },
      synth: { flow: "yosys", runs_on: "yosys", note: null, available: true },
    }} />);
    expect(screen.getByText(/sim: verilator/)).toBeTruthy();
    expect(screen.getByText(/vcs not found/).closest("[title]")?.getAttribute("title")).toContain("not found on this host");
    expect(screen.queryByText(/synth:/)).toBeNull();
  });

  it("names the missing tool, not the stand-in", () => {
    render(<BackendChips backends={{ synth: { flow: "yosys", configured: "dc", missing: ["dc_shell-t"], runs_on: "yosys", note: "dc_shell-t not found", available: true } }} />);
    expect(screen.getByText(/dc_shell-t not found/)).toBeTruthy();
    expect(screen.queryByText(/yosys not found/)).toBeNull();
  });

  it("renders nothing when every flow runs natively", () => {
    const { container } = render(<BackendChips backends={{ sim: { flow: "verilator", runs_on: "verilator", available: true } }} />);
    expect(container.textContent).toBe("");
  });
});

import { Logo } from "./Logo";
describe("Logo", () => {
  it("shows the mark and wordmark and links home", () => {
    const { container } = render(<Logo />);
    const a = container.querySelector("a.brand")!;
    expect(a.getAttribute("href")).toBe("#/");
    expect(a.querySelector("svg.logo-mark")).toBeTruthy();
    expect(a.textContent).toBe("svkit");
  });
});
