import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Per-paste reset rule for Save to mirrors audio-only: any non-typing URL
 * transition (clear, replace, paste-extend, backspace) clears the override.
 * This guards against an override silently carrying over to the next URL the
 * user pastes in the same session.
 */
describe("App output_dir per-paste reset", () => {
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
            deno: { present: true, path: null },
            ffmpeg: { present: true, path: null },
            subtitles_default: false,
            output_dir: "/home/u/Videos/ytdl",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (path.startsWith("/jobs/clear")) {
        return new Response(
          JSON.stringify({ clearable: 0, older_than_days: 7 }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("clears the Save to value when the URL is paste-replaced", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByLabelText(/Save to/i)).toBeInTheDocument();
    });

    const urlInput = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    const saveTo = screen.getByLabelText(/Save to/i) as HTMLInputElement;

    // Paste a URL, then set Save to.
    fireEvent.change(urlInput, { target: { value: "https://yt/x" } });
    fireEvent.change(saveTo, { target: { value: "~/Music" } });
    expect(saveTo.value).toBe("~/Music");

    // Select-all paste-replace — the new value does not extend the previous,
    // so the per-paste contract treats it as a fresh paste and clears the
    // override.
    fireEvent.change(urlInput, { target: { value: "https://yt/y" } });
    expect(saveTo.value).toBe("");
  });
});
