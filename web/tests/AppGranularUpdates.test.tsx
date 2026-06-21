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

  it("does not overwrite job.status from progress events", async () => {
    // The server forwards yt-dlp's raw progress status (e.g. "downloading")
    // which is NOT a queue JobStatus. Writing it into job.status would flip
    // the row to a non-running state and hide the progress UI.
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({
          event: "progress",
          job_id: "abc",
          status: "downloading", // raw yt-dlp status, not JobStatus
          downloaded_bytes: 500_000,
          total_bytes: 1_000_000,
        }),
      } as MessageEvent);
    });

    // Progress UI stays visible (it requires status === "running"). The
    // status pill ALSO renders the value uppercased — we just need to
    // assert progress UI is still there.
    await waitFor(() => expect(screen.getByText("50%")).toBeInTheDocument());
    // The status pill should still show "running" (uppercased) — NOT
    // "downloading".
    expect(screen.queryByText(/DOWNLOADING/i)).not.toBeInTheDocument();
  });

  it("triggers a full /jobs refresh on snapshot events (reconnect resync)", async () => {
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

    await waitFor(() => expect(jobsCallCount).toBe(initialJobs + 1));
    expect(getJobCallCount).toBe(initialGet);
  });

  it("triggers a full /jobs refresh on playlist expansion", async () => {
    // The children created during expansion are not announced on the
    // bus individually — we have to resync via /jobs to learn about
    // them, otherwise they stay invisible.
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobs = jobsCallCount;
    const initialGet = getJobCallCount;
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({
          event: "expanded",
          job_id: "parent-1",
          child_count: 25,
        }),
      } as MessageEvent);
    });

    await waitFor(() => expect(jobsCallCount).toBe(initialJobs + 1));
    expect(getJobCallCount).toBe(initialGet);
  });

  it("drops stale lifecycle responses via per-row generation guard", async () => {
    // Two lifecycle events for the same job fire in quick succession.
    // The first fetch resolves AFTER the second. The guard must drop the
    // first response so the row reflects the latest state.
    let resolveFirst: (value: Response) => void = () => {};
    let firstFetchSeen = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
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
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.match(/^\/jobs\/[^/]+$/)) {
        getJobCallCount++;
        if (!firstFetchSeen) {
          firstFetchSeen = true;
          // Hold the first response open — we'll resolve it AFTER the
          // second fetch has already landed.
          return new Promise<Response>((resolve) => {
            resolveFirst = resolve;
          });
        }
        // Second fetch returns "done" immediately.
        return new Response(
          JSON.stringify(
            singleJob({ status: "done", finished_at: Date.now() }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const es = esInstances[0];

    // Fire two lifecycle events for the same job in quick succession.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "started", job_id: "abc" }),
      } as MessageEvent);
      es.onmessage?.({
        data: JSON.stringify({ event: "finished", job_id: "abc" }),
      } as MessageEvent);
    });

    // Wait for the second fetch to resolve and the row to show "done".
    await waitFor(() => expect(screen.getByText(/finished/i)).toBeInTheDocument());

    // Now resolve the first (stale) fetch with an OLD "running" snapshot.
    // The guard should refuse to write it.
    await act(async () => {
      resolveFirst(
        new Response(
          JSON.stringify(singleJob({ status: "running" })),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
      await Promise.resolve();
    });

    // Row stays on "finished" — the stale response was dropped.
    expect(screen.getByText(/finished/i)).toBeInTheDocument();
    expect(screen.queryByText(/RUNNING/i)).not.toBeInTheDocument();
  });
});
