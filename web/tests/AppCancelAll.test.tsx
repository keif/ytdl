import { render, screen, waitFor, act } from "@testing-library/react";
import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
  type MockInstance,
} from "vitest";
import App from "../src/App";

function pendingJob() {
  return {
    id: "p1",
    url: "https://youtu.be/pending",
    kind: "video",
    parent_job_id: null,
    status: "pending",
    format_pref: "best",
    output_dir: "/out",
    output_path: null,
    title: "Pending Video",
    video_id: "pending",
    uploader: null,
    duration_s: null,
    thumbnail_url: null,
    filesize_bytes: null,
    bytes_done: null,
    speed_bps: null,
    eta_s: null,
    error: null,
    force_overwrite: false,
    subtitles: false,
    attempts: 0,
    created_at: Date.now() - 1000,
    started_at: null,
    finished_at: null,
  };
}

describe("App cancel-all", () => {
  let originalFetch: typeof globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;
  let confirmSpy: MockInstance<(message?: string) => boolean>;

  beforeEach(() => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = class {
      onmessage?: (e: MessageEvent) => void;
      onopen?: () => void;
      onerror?: () => void;
      constructor(_url: string) {}
      close() {}
    };
    confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    originalFetch = globalThis.fetch;
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (url === "/jobs/cancel-all" && init?.method === "POST") {
        return new Response(JSON.stringify({ canceled: 1, canceling: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/jobs" || url.startsWith("/jobs?")) {
        return new Response(
          JSON.stringify({ jobs: [pendingJob()], total: 1 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url === "/status") {
        return new Response(
          JSON.stringify({
            cookies_browser: null,
            cookies_source: "none",
            cookies_file: null,
            pot_provider_url: null,
            deno: { present: true, path: "/usr/bin/deno" },
            ffmpeg: { present: true, path: "/usr/bin/ffmpeg" },
            subtitles_default: false,
            probe_timeout_s: 30,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (url.startsWith("/jobs/clear/preview")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    confirmSpy.mockRestore();
  });

  it("shows a 'Cancel all' button when in-flight jobs exist and POSTs on confirm", async () => {
    render(<App />);
    const btn = await screen.findByRole("button", { name: /Cancel all 1/ });
    await act(async () => {
      btn.click();
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input) === "/jobs/cancel-all" && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(confirmSpy).toHaveBeenCalled();
  });
});
