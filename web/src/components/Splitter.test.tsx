import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Splitter } from "./Splitter";

function Harness() {
  const [w, setW] = useState(200);
  return (
    <div>
      <Splitter label="Names width" value={w} min={120} max={() => 400} onChange={setW} onReset={() => setW(300)} />
      <output>{w}</output>
    </div>
  );
}

describe("column splitter", () => {
  it("resizes by keyboard within its bounds and resets on double-click", () => {
    render(<Harness />);
    const sep = screen.getByRole("separator", { name: "Names width" });

    const w = () => Number(document.querySelector("output")!.textContent);
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(w()).toBe(216);
    fireEvent.keyDown(sep, { key: "ArrowLeft", shiftKey: true });
    expect(w()).toBe(152);
    fireEvent.keyDown(sep, { key: "End" });
    expect(w()).toBe(400);
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(w()).toBe(400); // clamped
    fireEvent.keyDown(sep, { key: "Home" });
    expect(w()).toBe(120);
    fireEvent.doubleClick(sep);
    expect(w()).toBe(300);

  });
  // dragging is exercised in a real browser (jsdom drops pointer coordinates)
});
