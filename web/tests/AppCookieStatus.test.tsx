import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Header chip surfaces the auto-detected cookies browser so a user can tell
 * at a glance whether yt-dlp will be reading from their browser store.
 */
describe("App cookies status", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let originalFetch: typeof globalThis.fetch;

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
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return new Response(JSON.stringify({ jobs: [], total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (path === "/status") {
        return new Response(
          JSON.stringify({
            cookies_browser: "chrome",
            cookies_source: "autodetect",
            deno: { present: true, path: "/usr/local/bin/deno" },
            ffmpeg: { present: true, path: "/usr/local/bin/ffmpeg" },
            subtitles_default: false,
          }),
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

  it("renders the cookies browser in the header", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/cookies: chrome \(auto\)/)).toBeInTheDocument(),
    );
  });

  it("renders deno + ffmpeg presence chips when both are found", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("deno: ✓")).toBeInTheDocument();
      expect(screen.getByText("ffmpeg: ✓")).toBeInTheDocument();
    });
  });
});

describe("App runtime missing-binary warnings", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = class {
      onmessage?: (e: MessageEvent) => void;
      onopen?: () => void;
      onerror?: () => void;
      constructor(_url: string) {}
      close() {}
    };
    originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return new Response(JSON.stringify({ jobs: [], total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (path === "/status") {
        return new Response(
          JSON.stringify({
            cookies_browser: null,
            cookies_source: "none",
            deno: { present: false, path: null },
            ffmpeg: { present: false, path: null },
            subtitles_default: false,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("renders 'missing' chips when binaries are absent", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("deno: missing")).toBeInTheDocument();
      expect(screen.getByText("ffmpeg: missing")).toBeInTheDocument();
    });
  });
});
