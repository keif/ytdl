import { render, screen, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

interface FakeEventSource {
  onmessage?: (e: MessageEvent) => void;
  onopen?: () => void;
  onerror?: () => void;
  close: () => void;
}

function statusResponse() {
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

function jobsListResponse(jobs: unknown[] = []) {
  return new Response(JSON.stringify({ jobs, total: jobs.length }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function singleJob(overrides: Record<string, unknown> = {}) {
  return {
    id: "abc",
    url: "https://yt/abc",
    kind: "video",
    parent_job_id: null,
    status: "running",
    format_pref: "best",
    output_dir: "/out",
    output_path: null,
    title: "My Video",
    video_id: "abc",
    uploader: null,
    duration_s: null,
    filesize_bytes: 1_000_000,
    bytes_done: 100_000,
    speed_bps: null,
    eta_s: null,
    error: null,
    attempts: 1,
    created_at: Date.now() - 5000,
    started_at: Date.now() - 4000,
    finished_at: null,
    ...overrides,
  };
}

describe("App granular SSE updates", () => {
  let originalFetch: typeof globalThis.fetch;
  let esInstances: FakeEventSource[];
  let fetchMock: ReturnType<typeof vi.fn>;
  let clearPreviewCount: number;
  let jobsCallCount: number;
  let getJobCallCount: number;

  beforeEach(() => {
    esInstances = [];
    clearPreviewCount = 0;
    jobsCallCount = 0;
    getJobCallCount = 0;
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
    fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        jobsCallCount++;
        return jobsListResponse([singleJob()]);
      }
      if (url === "/status") return statusResponse();
      if (url.startsWith("/jobs/clear/preview")) {
        clearPreviewCount++;
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.match(/^\/jobs\/[^/]+$/)) {
        getJobCallCount++;
        // Return the job with updated lifecycle data.
        return new Response(
          JSON.stringify(
            singleJob({ status: "done", finished_at: Date.now() }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("patches progress events in place without re-fetching /jobs", async () => {
    render(<App />);
    // Wait for initial mount fetches.
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobsFetches = jobsCallCount;
    const es = esInstances[0];

    // Fire 5 progress events.
    act(() => {
      for (let i = 0; i < 5; i++) {
        es.onmessage?.({
          data: JSON.stringify({
            event: "progress",
            job_id: "abc",
            status: "running",
            downloaded_bytes: (i + 1) * 200_000,
            total_bytes: 1_000_000,
            speed: 1_048_576,
            eta: 4 - i,
          }),
        } as MessageEvent);
      }
    });

    // No additional /jobs fetches.
    expect(jobsCallCount).toBe(initialJobsFetches);
    // No single-job fetches (progress shouldn't trigger them).
    expect(getJobCallCount).toBe(0);
    // Progress bar reflects the last event (100%).
    await waitFor(() => expect(screen.getByText("100%")).toBeInTheDocument());
    expect(screen.getByText("1.0 MB/s")).toBeInTheDocument();
  });

  it("fetches the single row on lifecycle events", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobsFetches = jobsCallCount;
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "finished", job_id: "abc" }),
      } as MessageEvent);
    });

    // One single-job fetch, no additional full /jobs.
    await waitFor(() => expect(getJobCallCount).toBe(1));
    expect(jobsCallCount).toBe(initialJobsFetches);
  });

  it("ignores events with no job_id (e.g., snapshot, keep-alive)", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobs = jobsCallCount;
    const initialGet = getJobCallCount;
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });

    expect(jobsCallCount).toBe(initialJobs);
    expect(getJobCallCount).toBe(initialGet);
  });
});
