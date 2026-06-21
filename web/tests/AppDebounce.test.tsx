import { render, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

// Mock fetch so listJobs() / createJob() / cancelJob() resolve.
function mockJobsListResponse(jobs: unknown[] = []) {
  return new Response(JSON.stringify({ jobs, total: jobs.length }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

interface FakeEventSource {
  onmessage?: (e: MessageEvent) => void;
  onopen?: () => void;
  onerror?: () => void;
  close: () => void;
}

describe("App SSE refresh debounce", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;
  let originalFetch: typeof globalThis.fetch;
  let esInstances: FakeEventSource[] = [];

  beforeEach(() => {
    esInstances = [];
    // Stub EventSource so we can drive onmessage manually.
    (globalThis as unknown as { EventSource: unknown }).EventSource = class {
      onmessage?: (e: MessageEvent) => void;
      onopen?: () => void;
      onerror?: () => void;
      constructor(_url: string) {
        esInstances.push(this as unknown as FakeEventSource);
      }
      close() {}
    };

    originalFetch = globalThis.fetch;
    // The /status mount-time fetch needs its real shape so App renders
    // without crashing on the runtime-binary chips. Everything else
    // (including /jobs) returns the empty list mock.
    fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/status") {
        return new Response(
          JSON.stringify({
            cookies_browser: "chrome",
            cookies_source: "autodetect",
            deno: { present: true, path: "/usr/bin/deno" },
            ffmpeg: { present: true, path: "/usr/bin/ffmpeg" },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return mockJobsListResponse([]);
    });
    globalThis.fetch = fetchSpy as unknown as typeof globalThis.fetch;

    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
  });

  // Only the /jobs refreshes matter for this debounce assertion. Other
  // mount-time fetches (e.g. /status) would otherwise inflate the count.
  function jobsCallCount(): number {
    return fetchSpy.mock.calls.filter((c) => {
      const url = c[0];
      return typeof url === "string" && (url === "/jobs" || url.startsWith("/jobs?"));
    }).length;
  }

  it("coalesces a burst of SSE events into one refresh", async () => {
    render(<App />);
    // initial refresh from mount — flush microtasks so listJobs resolves.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(jobsCallCount()).toBe(1);

    const es = esInstances[0];
    expect(es).toBeDefined();
    // Fire 50 SSE events in rapid succession.
    act(() => {
      for (let i = 0; i < 50; i++) {
        es.onmessage?.({
          data: JSON.stringify({ event: "enqueued", job_id: `j${i}` }),
        } as MessageEvent);
      }
    });
    // No additional jobs fetch yet — still inside the debounce window.
    expect(jobsCallCount()).toBe(1);
    // Advance past the debounce window. The debounce callback fires and
    // calls fetch synchronously inside refresh().
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    // Exactly ONE additional refresh, regardless of the 50 events.
    expect(jobsCallCount()).toBe(2);
  });
});
