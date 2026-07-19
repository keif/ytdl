import { render, screen, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

describe("App queue status filter", () => {
  let originalFetch: typeof globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = class {
      onmessage?: (e: MessageEvent) => void;
      onopen?: () => void;
      onerror?: () => void;
      constructor(_url: string) {}
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
        return new Response(JSON.stringify({ jobs: [], total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/status") {
        return new Response(
          JSON.stringify({
            cookies_browser: null,
            cookies_source: "none",
            cookies_file: null,
            pot_provider_url: null,
            deno: { present: true, path: "/d" },
            ffmpeg: { present: true, path: "/f" },
            subtitles_default: false,
            probe_timeout_s: 60,
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
  });

  it("fetches only 'done' jobs when the Done tab is clicked", async () => {
    render(<App />);
    const doneTab = await screen.findByRole("button", { name: "Done" });
    await act(async () => {
      doneTab.click();
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          /\/jobs\?.*status=done/.test(String(input)),
        ),
      ).toBe(true),
    );
  });

  it("default 'All' fetch carries no status param", async () => {
    render(<App />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const s = String(input);
          return s.startsWith("/jobs?") && !s.includes("status=");
        }),
      ).toBe(true),
    );
  });
});
