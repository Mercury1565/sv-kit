// Background thread for the Design view: netlistsvg lays out (elkjs 0.3.0) and draws a schematic
// netlist with svkit's skin. Input/output are plain strings and JSON, so a big design never blocks
// the page. One message in, one out: {id, netlist} -> {id, svg} | {id, error}.
import "./schematic.guard";
import { render } from "netlistsvg";
import skin from "./schematic-skin.svg?raw";

interface Req {
  id: number;
  netlist: unknown;
}

self.onmessage = async (e: MessageEvent<Req>) => {
  const { id, netlist } = e.data;
  const t0 = performance.now();
  try {
    // netlistsvg logs and swallows layout errors (resolving to undefined): treat that as a failure
    const svg = await render(skin, netlist as never);
    if (typeof svg !== "string" || !svg.startsWith("<svg")) throw new Error("the layout engine could not place this netlist");
    (self as unknown as Worker).postMessage({ id, svg, ms: Math.round(performance.now() - t0) });
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: err instanceof Error ? err.message : String(err) });
  }
};
