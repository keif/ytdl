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
  let jobsCallCount: number;
  let getJobCallCount: number;

  beforeEach(() => {
    esInstances = [];
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
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.match(/^\/jobs\/[^/]+$/)) {
        getJobCallCount++;
        return new Response(
          JSON.stringify(singleJob({ status: "done", finished_at: Date.now() })),
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
    // No single-job fetches either (we removed that path).
    expect(getJobCallCount).toBe(0);
    // Progress bar reflects the last event (100%).
    await waitFor(() => expect(screen.getByText("100%")).toBeInTheDocument());
    expect(screen.getByText("1.0 MB/s")).toBeInTheDocument();
  });

  it("does not overwrite job.status from progress events", async () => {
    // The server forwards yt-dlp's raw progress status (e.g. "downloading")
    // which is NOT a queue JobStatus. Writing it would flip the row to a
    // non-running state and hide the progress UI.
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

    await waitFor(() => expect(screen.getByText("50%")).toBeInTheDocument());
    expect(screen.queryByText(/DOWNLOADING/i)).not.toBeInTheDocument();
  });

  it("triggers a full /jobs refresh on lifecycle events", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobs = jobsCallCount;
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "finished", job_id: "abc" }),
      } as MessageEvent);
    });

    await waitFor(() => expect(jobsCallCount).toBe(initialJobs + 1));
    // No single-row fetch — we removed the getJob path.
    expect(getJobCallCount).toBe(0);
  });

  it("triggers a full /jobs refresh on snapshot events (reconnect resync)", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobs = jobsCallCount;
    const es = esInstances[0];

    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });

    await waitFor(() => expect(jobsCallCount).toBe(initialJobs + 1));
  });

  it("triggers a full /jobs refresh on playlist expansion", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("My Video")).toBeInTheDocument());
    const initialJobs = jobsCallCount;
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
  });

  it("drops older full refreshes that resolve after newer ones", async () => {
    let resolveFirstRefresh: (value: Response) => void = () => {};
    let firstRefreshSeen = false;
    let initialJobsDone = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        jobsCallCount++;
        if (!initialJobsDone) {
          initialJobsDone = true;
          return jobsListResponse([singleJob({ id: "existing", title: "Existing" })]);
        }
        if (!firstRefreshSeen) {
          firstRefreshSeen = true;
          return new Promise<Response>((resolve) => {
            resolveFirstRefresh = resolve;
          });
        }
        return jobsListResponse([
          singleJob({ id: "existing", title: "Existing" }),
          singleJob({ id: "child", title: "Discovered Child" }),
        ]);
      }
      if (url === "/status") return statusResponse();
      if (url.startsWith("/jobs/clear/preview")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Existing")).toBeInTheDocument());
    const es = esInstances[0];

    // Two refresh-triggering events in succession.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
      es.onmessage?.({
        data: JSON.stringify({ event: "expanded", job_id: "parent", child_count: 1 }),
      } as MessageEvent);
    });

    // The second refresh resolves immediately and adds the child row.
    await waitFor(() => expect(screen.getByText("Discovered Child")).toBeInTheDocument());

    // Now resolve the FIRST refresh with the older payload (no child).
    await act(async () => {
      resolveFirstRefresh(jobsListResponse([singleJob({ id: "existing", title: "Existing" })]));
      await Promise.resolve();
    });

    // Discovered child stays visible — the older refresh was dropped.
    expect(screen.getByText("Discovered Child")).toBeInTheDocument();
    expect(screen.getByText("Existing")).toBeInTheDocument();
  });

  it("preserves in-flight progress when a full refresh applies a running row", async () => {
    // Progress patches advance the row to 80%; meanwhile a refresh lands
    // with the same row at the older 10% because /jobs read SQLite
    // before the latest progress was flushed.
    let resolveSlowRefresh: (value: Response) => void = () => {};
    let initialJobsDone = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        jobsCallCount++;
        if (!initialJobsDone) {
          initialJobsDone = true;
          return jobsListResponse([
            singleJob({
              id: "abc",
              title: "Live",
              status: "running",
              bytes_done: 100_000,
              filesize_bytes: 1_000_000,
            }),
          ]);
        }
        return new Promise<Response>((resolve) => {
          resolveSlowRefresh = resolve;
        });
      }
      if (url === "/status") return statusResponse();
      if (url.startsWith("/jobs/clear/preview")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument());
    const es = esInstances[0];

    // Snapshot → starts slow refresh.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(2));

    // Progress events advance to 80%.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({
          event: "progress",
          job_id: "abc",
          downloaded_bytes: 800_000,
          total_bytes: 1_000_000,
        }),
      } as MessageEvent);
    });
    await waitFor(() => expect(screen.getByText("80%")).toBeInTheDocument());

    // Refresh lands with STALE 10% progress.
    await act(async () => {
      resolveSlowRefresh(
        jobsListResponse([
          singleJob({
            id: "abc",
            title: "Live",
            status: "running",
            bytes_done: 100_000,
            filesize_bytes: 1_000_000,
          }),
        ]),
      );
      await Promise.resolve();
    });

    // Progress should still be at 80%, not rolled back.
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.queryByText("10%")).not.toBeInTheDocument();
  });

  it("does not suppress an earlier successful refresh when a later refresh fails", async () => {
    // Two refresh-triggering events overlap. The LATER /jobs request
    // fails. The earlier successful one must still write — otherwise
    // the UI gets stuck on the pre-refresh state.
    let resolveFirstRefresh: (value: Response) => void = () => {};
    let rejectSecondRefresh: (reason: Error) => void = () => {};
    let initialJobsDone = false;
    let firstRefreshSeen = false;
    let secondRefreshSeen = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        jobsCallCount++;
        if (!initialJobsDone) {
          initialJobsDone = true;
          return jobsListResponse([singleJob({ id: "existing", title: "Existing" })]);
        }
        if (!firstRefreshSeen) {
          firstRefreshSeen = true;
          return new Promise<Response>((resolve) => {
            resolveFirstRefresh = resolve;
          });
        }
        if (!secondRefreshSeen) {
          secondRefreshSeen = true;
          return new Promise<Response>((_, reject) => {
            rejectSecondRefresh = reject;
          });
        }
        return jobsListResponse([]);
      }
      if (url === "/status") return statusResponse();
      if (url.startsWith("/jobs/clear/preview")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Existing")).toBeInTheDocument());
    const es = esInstances[0];

    // Two refresh-triggering events in succession.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
      es.onmessage?.({
        data: JSON.stringify({ event: "expanded", job_id: "parent", child_count: 1 }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(3));

    // Reject the LATER refresh first.
    await act(async () => {
      rejectSecondRefresh(new Error("network failure"));
      await Promise.resolve();
      await Promise.resolve();
    });

    // Now resolve the EARLIER refresh with discovered new rows.
    await act(async () => {
      resolveFirstRefresh(
        jobsListResponse([
          singleJob({ id: "existing", title: "Existing" }),
          singleJob({ id: "discovered", title: "Discovered Child" }),
        ]),
      );
      await Promise.resolve();
    });

    // The earlier success should have applied — discovered child visible.
    await waitFor(() =>
      expect(screen.getByText("Discovered Child")).toBeInTheDocument(),
    );
    expect(screen.getByText("Existing")).toBeInTheDocument();
  });

  it("keeps bytes_done and filesize_bytes paired when merging progress on refresh", async () => {
    // If live state has 800/1000 (80%) and refresh returns 200/2000
    // (10%, because total grew from estimate), the merge must keep
    // 800/1000 (live wins) — NOT 800/2000 (40%, mismatched pair).
    let resolveRefresh: (value: Response) => void = () => {};
    let initialJobsDone = false;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        jobsCallCount++;
        if (!initialJobsDone) {
          initialJobsDone = true;
          return jobsListResponse([
            singleJob({
              id: "abc",
              title: "Live",
              status: "running",
              bytes_done: 0,
              filesize_bytes: 1_000_000,
            }),
          ]);
        }
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      if (url === "/status") return statusResponse();
      if (url.startsWith("/jobs/clear/preview")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument());
    const es = esInstances[0];

    // Snapshot → starts slow refresh.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(2));

    // Progress event advances live to 800/1000 (80%).
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({
          event: "progress",
          job_id: "abc",
          downloaded_bytes: 800_000,
          total_bytes: 1_000_000,
        }),
      } as MessageEvent);
    });
    await waitFor(() => expect(screen.getByText("80%")).toBeInTheDocument());

    // Refresh lands with smaller bytes_done but a LARGER total
    // (200/2000 = 10%).
    await act(async () => {
      resolveRefresh(
        jobsListResponse([
          singleJob({
            id: "abc",
            title: "Live",
            status: "running",
            bytes_done: 200_000,
            filesize_bytes: 2_000_000,
          }),
        ]),
      );
      await Promise.resolve();
    });

    // Should be 80% — live's bytes (800k) over live's total (1M).
    // NOT 40% — live's bytes (800k) over refresh's total (2M).
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.queryByText("40%")).not.toBeInTheDocument();
  });
});
