import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { JobError } from "./JobButton";

describe("JobError", () => {
  const text = "ConfigError: build_cmd `make build/simv` failed (exit 2)\nflow 'vcs' needs vcs, which is not on PATH\n/bin/sh: Illegal option -o pipefail";

  it("shows the first line and reveals the full text on demand", () => {
    render(<JobError text={text} />);
    expect(screen.getByText(/failed: ConfigError/)).toBeTruthy();
    expect(screen.queryByText(/not on PATH/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "details" }));
    expect(screen.getByRole("status").textContent).toContain("Illegal option -o pipefail");
    fireEvent.click(screen.getByRole("button", { name: "hide" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("has no details toggle for a short one-line error", () => {
    render(<JobError text="busy" />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
