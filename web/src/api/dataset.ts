// Kept separate from mock.ts so the main bundle never includes the mock backend.
export type Dataset = "demo" | "worst" | "empty";

export function mockDataset(): Dataset {
  try {
    const d = new URLSearchParams(window.location.search).get("data");
    if (d === "worst" || d === "empty") return d;
  } catch {
    /* ignore */
  }
  return "demo";
}
