import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { mockRequested, setTransport } from "./api/client";
import { events } from "./api/ws";
import "./styles.css";
import { applyTheme } from "./lib/theme";

applyTheme(); // before the first paint

async function boot() {
  if (mockRequested()) {
    const { MockBackend, mockDataset } = await import("./api/mock");
    setTransport(new MockBackend(mockDataset(), { latency: 60, emit: (ev) => events.emit(ev) }), true);
    events.useMock();
  } else {
    events.connect();
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

boot();
