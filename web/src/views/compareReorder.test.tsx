import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { CompareResult } from "../api/types";
import { computeDelta, moveItem, permuteCompare } from "../lib/compare";
import { CompareTable } from "./Compare";

const raw = { wns: [12, -30, -410, 5], area: [8210, 7640, 8230, 8000] };
const build = (order: number[]): CompareResult => ({
  run_ids: order.map((i) => `r_000${i + 1}`),
  labels: order.map((i) => `exp${i + 1}`),
  metrics: [
    computeDelta("wns", "higher", order.map((i) => raw.wns[i]), "ps"),
    computeDelta("area_total", "lower", order.map((i) => raw.area[i]), "um^2"),
  ],
});

describe("reordering compare columns", () => {
  it("moveItem takes the item out and puts it at the new position", () => {
    expect(moveItem(["a", "b", "c", "d"], 3, 1)).toEqual(["a", "d", "b", "c"]);
    expect(moveItem(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(moveItem(["a", "b"], 1, 5)).toEqual(["a", "b"]);
  });

  it("with the baseline kept first, moving cells locally equals recomputing", () => {
    expect(permuteCompare(build([0, 1, 2, 3]), 3, 1)).toEqual(build([0, 3, 1, 2]));
    expect(permuteCompare(build([0, 1, 2, 3]), 1, 3)).toEqual(build([0, 2, 3, 1]));
  });

  it("‹ › buttons, make baseline and drag-and-drop all report (from, to)", () => {
    const onMove = vi.fn();
    const { container } = render(<CompareTable cmp={build([0, 1, 2, 3])} onMove={onMove} />);
    fireEvent.click(screen.getByRole("button", { name: "Move r_0003 right" }));
    expect(onMove).toHaveBeenLastCalledWith(2, 3);
    fireEvent.click(screen.getByRole("button", { name: "Move r_0002 left (it becomes the baseline)" }));
    expect(onMove).toHaveBeenLastCalledWith(1, 0);
    expect((screen.getByRole("button", { name: "Move r_0001 left" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Move r_0004 right" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "make baseline" })[2]);
    expect(onMove).toHaveBeenLastCalledWith(3, 0);
    const ths = container.querySelectorAll("thead th.movable");
    const dataTransfer = { setData: () => {}, effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(ths[3], { dataTransfer });
    fireEvent.dragOver(ths[1], { dataTransfer });
    expect(ths[1].className).toContain("drop-before");
    fireEvent.drop(ths[1], { dataTransfer });
    expect(onMove).toHaveBeenLastCalledWith(3, 1);
    expect(container.querySelector(".drop-before, .drop-after")).toBeNull();
  });

  it("without onMove the table is read-only", () => {
    const { container } = render(<CompareTable cmp={build([0, 1])} />);
    expect(container.querySelector("th.movable")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Move |make baseline/ })).toBeNull();
  });
});
