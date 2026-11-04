// Imported by schematic.worker.ts BEFORE netlistsvg. elkjs 0.3.0 (netlistsvg's layout engine) sees
// "no document + self" and assumes it *is* the ELK web worker: it takes over `onmessage` and exports
// nothing, so `new ELK()` fails. With a placeholder `document` it runs in-thread, as it does on a page
// (this worker is our background thread). ELK itself never touches the DOM.
const g = self as unknown as { document?: unknown };
if (typeof g.document === "undefined") g.document = {};
export {};
