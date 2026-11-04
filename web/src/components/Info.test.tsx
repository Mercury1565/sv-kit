import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Info, Term } from "./Info";
import { GLOSSARY, explainText, metricTerm } from "../lib/glossary";

describe("glossary (i)", () => {
  it("labels use full words; the abbreviation only appears in the explanation", () => {
    render(<Term id="setup_slack" />);
    expect(screen.getByText("Worst setup slack")).toBeTruthy();
    expect(screen.queryByText(/WNS/)).toBeNull();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "What does this mean?" }));
    expect(screen.getByRole("tooltip").textContent).toContain("Also called WNS");
  });

  it("opens on keyboard focus, closes on Escape, and toggles on click for touch", () => {
    render(<Info term="cpi" />);
    const b = screen.getByRole("button");
    fireEvent.focus(b);
    expect(screen.getByRole("tooltip").textContent).toContain("cycles ÷ instructions");
    expect(b.getAttribute("aria-describedby")).toBe(screen.getByRole("tooltip").id);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.click(b);
    fireEvent.mouseLeave(b);
    expect(screen.getByRole("tooltip")).toBeTruthy();       // pinned by the click
  });

  it("every glossary label avoids bare abbreviations", () => {
    for (const [id, t] of Object.entries(GLOSSARY)) {
      expect(t.label, id).not.toMatch(/\b(WNS|TNS|CPI|TPI|T_min|Vt|QoR)\b/);
      expect(explainText(id as keyof typeof GLOSSARY).length, id).toBeGreaterThan(30);
    }
  });

  it("maps compare metrics to explanations", () => {
    expect(metricTerm("wns")).toBe("setup_slack");
    expect(metricTerm("vt_mix.LVT")).toBe("vt_mix");
    expect(metricTerm("cell_count")).toBeNull();
  });
});
