import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// the real worker cannot run under jsdom: hand back a fixed drawing
vi.mock("../lib/schematic", async () => {
  const real = await vi.importActual<typeof import("../lib/schematic")>("../lib/schematic");
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><g id="cell_u_a"><rect class="box" width="10" height="10"/></g></svg>';
  return { ...real, draw: () => ({ promise: Promise.resolve({ svg, ms: 1 }), cancel: () => {} }) };
});
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

describe("Design: zooming", () => {
  it("Ctrl + wheel zooms the diagram (not the page); a plain wheel scrolls", async () => {
    vi.spyOn(api, "hierarchy").mockResolvedValue({ top: "t", modules: { t: { ports: [], instances: [] } } });
    vi.spyOn(api, "schematicBlock").mockResolvedValue({
      netlist: { modules: {} },
      cells: { u_a: { kind: "instance", name: "u_a", module: "a" } },
      path_nets: [],
      counts: { instances: 1, ports: 0, nets: 0 },
    });
    const { container } = render(<DesignView query={new URLSearchParams("")} />);
    await waitFor(() => expect(container.querySelector(".schematic svg")).toBeTruthy());
    const host = container.querySelector(".schematic") as HTMLElement;
    const svg = host.querySelector("svg")!;
    const label = () => container.querySelector(".num")!.textContent;
    expect(label()).toBe("70%"); // fit-to-width floor (jsdom has no layout)
    const plain = new WheelEvent("wheel", { deltaY: 100, bubbles: true, cancelable: true });
    host.dispatchEvent(plain);
    expect(plain.defaultPrevented).toBe(false);
    const zoomIn = new WheelEvent("wheel", { deltaY: -100, ctrlKey: true, bubbles: true, cancelable: true });
    fireEvent(host, zoomIn);
    expect(zoomIn.defaultPrevented).toBe(true);
    await waitFor(() => expect(label()).toBe("85%"));
    expect(svg.getAttribute("width")).toBe("342");
    fireEvent(host, new WheelEvent("wheel", { deltaY: 300, metaKey: true, bubbles: true, cancelable: true }));
    await waitFor(() => expect(label()).toBe("47%"));
  });
});
