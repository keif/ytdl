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

  it("drops a stale full /jobs refresh that resolves after a lifecycle fetch", async () => {
    // The race: snapshot/expanded triggers refresh() → slow /jobs is in
    // flight; meanwhile a lifecycle event fetches /jobs/{id} and applies
    // newer state. The slow /jobs response must NOT clobber the row.
    let resolveSnapshotRefresh: (value: Response) => void = () => {};
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
          return jobsListResponse([singleJob()]);
        }
        // Subsequent /jobs (the snapshot-triggered refresh): hold open
        // until we explicitly resolve it AFTER the lifecycle fetch lands.
        return new Promise<Response>((resolve) => {
          resolveSnapshotRefresh = resolve;
        });
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

    // Snapshot → starts the slow full refresh.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(2));

    // Lifecycle event → fetches and patches the row to "done" while the
    // snapshot's full refresh is still pending.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "finished", job_id: "abc" }),
      } as MessageEvent);
    });
    await waitFor(() => expect(screen.getByText(/finished/i)).toBeInTheDocument());

    // Now resolve the stale full refresh with the OLD running state.
    await act(async () => {
      resolveSnapshotRefresh(jobsListResponse([singleJob({ status: "running" })]));
      await Promise.resolve();
    });

    // Row should still show "finished" — the stale full refresh was
    // invalidated by the lifecycle write.
    expect(screen.getByText(/finished/i)).toBeInTheDocument();
    expect(screen.queryByText(/RUNNING/i)).not.toBeInTheDocument();
  });

  it("keeps NEW rows from a refresh even when one in-flight row was updated by a lifecycle event", async () => {
    // The expanded-event scenario: a /jobs refresh discovers brand new
    // child rows that aren't announced individually on the bus. If a
    // lifecycle event for the EXISTING parent row fires during the
    // refresh, we must protect the parent's newer state but still let
    // the new children land.
    let resolveExpandedRefresh: (value: Response) => void = () => {};
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
          return jobsListResponse([singleJob({ id: "parent", title: "My Playlist" })]);
        }
        // Subsequent /jobs (the expansion refresh): hold open until we
        // resolve it AFTER the lifecycle fetch for the parent lands.
        return new Promise<Response>((resolve) => {
          resolveExpandedRefresh = resolve;
        });
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
        // Parent row's lifecycle fetch — returns parent as DONE.
        return new Response(
          JSON.stringify(
            singleJob({ id: "parent", title: "My Playlist", status: "done", finished_at: Date.now() }),
          ),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("My Playlist")).toBeInTheDocument());
    const es = esInstances[0];

    // Expanded → starts the slow refresh that will return parent +
    // children.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "expanded", job_id: "parent", child_count: 2 }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(2));

    // Lifecycle event for the parent → fetches and patches parent to
    // "done" while the expanded refresh is still pending.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "finished", job_id: "parent" }),
      } as MessageEvent);
    });
    await waitFor(() => expect(screen.getByText(/finished/i)).toBeInTheDocument());

    // Now resolve the expanded refresh with a STALE parent + new children.
    await act(async () => {
      resolveExpandedRefresh(
        jobsListResponse([
          singleJob({ id: "parent", title: "My Playlist", status: "running" }),
          singleJob({ id: "child-1", title: "Child 1" }),
          singleJob({ id: "child-2", title: "Child 2" }),
        ]),
      );
      await Promise.resolve();
    });

    // Parent stays "finished" (lifecycle write was newer); the new
    // children appear with their refresh-returned "running" status.
    expect(screen.getByText(/finished/i)).toBeInTheDocument();
    expect(screen.getByText("Child 1")).toBeInTheDocument();
    expect(screen.getByText("Child 2")).toBeInTheDocument();
    // Two RUNNING pills for the two children; NOT three (which would
    // mean parent had been clobbered back to running).
    const runningPills = screen.getAllByText(/running/i);
    expect(runningPills).toHaveLength(2);
  });

  it("keeps a lifecycle-inserted row that's missing from a stale refresh", async () => {
    // A lifecycle event INSERTS a new row (the job wasn't in the
    // initial /jobs listing — common when another client enqueued it,
    // or after a queue clear that wiped the row before this tab
    // resync'd). If the stale /jobs response from a concurrent
    // snapshot/expanded refresh doesn't include that row, the merge
    // must still preserve it.
    let resolveStaleRefresh: (value: Response) => void = () => {};
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
        return new Promise<Response>((resolve) => {
          resolveStaleRefresh = resolve;
        });
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
          JSON.stringify(singleJob({ id: "freshly-inserted", title: "Fresh Row" })),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Existing")).toBeInTheDocument());
    const es = esInstances[0];

    // Snapshot → starts a slow refresh.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
    });
    await waitFor(() => expect(jobsCallCount).toBe(2));

    // Lifecycle event for a job that wasn't in the original /jobs
    // listing — the handler will fetch and INSERT it.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "started", job_id: "freshly-inserted" }),
      } as MessageEvent);
    });
    await waitFor(() => expect(screen.getByText("Fresh Row")).toBeInTheDocument());

    // Now resolve the stale refresh with a response that does NOT
    // include the freshly-inserted row.
    await act(async () => {
      resolveStaleRefresh(
        jobsListResponse([singleJob({ id: "existing", title: "Existing" })]),
      );
      await Promise.resolve();
    });

    // Fresh row stays visible — the merge preserved it because its
    // lifecycle gen had incremented during the refresh.
    expect(screen.getByText("Fresh Row")).toBeInTheDocument();
    expect(screen.getByText("Existing")).toBeInTheDocument();
  });

  it("drops older full refreshes that resolve after newer ones", async () => {
    // Two refresh-triggering events arrive close together (e.g.,
    // snapshot then expanded). Both kick off /jobs fetches. If the
    // EARLIER refresh resolves AFTER the LATER one, its older payload
    // would wipe out the rows the newer refresh discovered.
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
          // Hold this response — it will resolve LAST.
          return new Promise<Response>((resolve) => {
            resolveFirstRefresh = resolve;
          });
        }
        // The second refresh returns immediately with the new state
        // (existing + a freshly-discovered playlist child).
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

    // Two full-refresh events in quick succession.
    act(() => {
      es.onmessage?.({
        data: JSON.stringify({ event: "snapshot", jobs: [] }),
      } as MessageEvent);
      es.onmessage?.({
        data: JSON.stringify({ event: "expanded", job_id: "parent", child_count: 1 }),
      } as MessageEvent);
    });

    // The second refresh (expanded) resolves immediately and adds the
    // child row.
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
});
