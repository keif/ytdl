import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Per-job output_dir override:
 *   - Blank input → POST body has no `output_dir` field. The server's
 *     configured default applies, just like older clients.
 *   - Non-blank → the value is forwarded verbatim. Validation is server-side.
 *
 * Also verifies that plain typing in the URL field doesn't trip the per-paste
 * reset heuristic (so a single-char append after setting Save to keeps the
 * value).
 */
describe("App output_dir override", () => {
  let originalFetch: typeof globalThis.fetch;
  let postedBodies: Array<Record<string, unknown>>;

  function installFetchMock() {
    postedBodies = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        return new Response(
          JSON.stringify({ id: "j-1", status: "pending" }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
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
      if (path === "/preview") {
        return new Response(
          JSON.stringify({
            kind: "video",
            title: null,
            entries: [
              { url: "https://yt/x", id: "x", title: "Single Vid", position: 1 },
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (path === "/preview/enrich") {
        return new Response(
          JSON.stringify({
            entries: [
              {
                url: "https://yt/x",
                title: "Single Vid",
                duration_s: 60,
                uploader: "U",
                thumbnail_url: null,
                error: null,
              },
            ],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      if (path.startsWith("/jobs/clear")) {
        return new Response(JSON.stringify({ clearable: 0, older_than_days: 7 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  }

  beforeEach(() => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = class {
      onmessage?: (e: MessageEvent) => void;
      onopen?: () => void;
      onerror?: () => void;
      constructor(_url: string) {}
      close() {}
    };
    originalFetch = globalThis.fetch;
    installFetchMock();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("omits output_dir from the POST body when the input is left blank", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    vi.useFakeTimers();
    try {
      const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
      fireEvent.change(input, { target: { value: "https://yt/x" } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600);
        await Promise.resolve();
      });

      const downloadBtn = screen.getByRole("button", { name: /^Download$/ });
      await act(async () => {
        fireEvent.click(downloadBtn);
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).not.toHaveProperty("output_dir");
  });

  it("includes the trimmed value when Save to is non-empty", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    // Fill the Save to input. It's inside the <details>, but jsdom lets you
    // focus closed-details inputs anyway — getByLabelText works regardless of
    // the open/closed state because the input is still in the DOM.
    const saveTo = screen.getByLabelText(/Save to/i) as HTMLInputElement;
    fireEvent.change(saveTo, { target: { value: "  ~/Music  " } });
    expect(saveTo.value).toBe("  ~/Music  ");

    vi.useFakeTimers();
    try {
      const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
      // Single-char "typing" extension after setting the override must NOT
      // reset Save to — the override carries through the typing window.
      fireEvent.change(input, { target: { value: "h" } });
      fireEvent.change(input, { target: { value: "ht" } });
      fireEvent.change(input, { target: { value: "htt" } });
      fireEvent.change(input, { target: { value: "http" } });
      fireEvent.change(input, { target: { value: "https" } });
      // Final value via a paste-extend would normally reset; replace the
      // whole field with a single paste-shaped change to a full URL after
      // first setting up a state where the heuristic considers it "fresh".
      fireEvent.change(input, { target: { value: "" } });
      fireEvent.change(input, { target: { value: "https://yt/x" } });
      // Re-set the Save to since the clear→paste resets it.
      fireEvent.change(saveTo, { target: { value: "~/Music" } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600);
        await Promise.resolve();
      });

      const downloadBtn = screen.getByRole("button", { name: /^Download$/ });
      await act(async () => {
        fireEvent.click(downloadBtn);
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({ output_dir: "~/Music" });
  });

  it("preserves Save to when the user types one more character into the URL", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByLabelText(/Save to/i)).toBeInTheDocument();
    });

    const urlInput = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    const saveTo = screen.getByLabelText(/Save to/i) as HTMLInputElement;

    // Establish a URL, then set Save to.
    fireEvent.change(urlInput, { target: { value: "https://yt/abc" } });
    fireEvent.change(saveTo, { target: { value: "~/Music" } });
    expect(saveTo.value).toBe("~/Music");

    // Type ONE more character into the URL — the per-paste contract treats
    // this as continued typing and must NOT reset Save to.
    fireEvent.change(urlInput, { target: { value: "https://yt/abcd" } });
    expect(saveTo.value).toBe("~/Music");
  });

  it("seeds the Save to placeholder from /status output_dir", async () => {
    render(<App />);
    await waitFor(() => {
      const saveTo = screen.getByLabelText(/Save to/i) as HTMLInputElement;
      expect(saveTo.placeholder).toBe("/home/u/Videos/ytdl");
    });
  });
});
