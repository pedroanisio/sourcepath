import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup, configure } from "@testing-library/react";
import { setBundle } from "../api";

// Coverage instrumentation + the BundlePicker fetch + view fetch can each
// trigger a re-render; default 1000 ms is tight under v8 coverage.
configure({ asyncUtilTimeout: 3000 });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  // Reset cross-test state: module-level current bundle + localStorage.
  setBundle(null);
  try {
    localStorage.clear();
  } catch {
    // jsdom always provides localStorage; ignore if not.
  }
});

// jsdom doesn't implement ResizeObserver (cytoscape uses it). Stub it.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as any).ResizeObserver = ResizeObserverStub;
