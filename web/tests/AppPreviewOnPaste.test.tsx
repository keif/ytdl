import { render, screen, act, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

interface FakeEventSource {
  onmessage?: (e: MessageEvent) => void;
  onopen?: () => void;
  onerror?: () => void;
  close: () => void;
}

/**
 * Integration-style coverage of the paste-then-preview flow: the user types a
 * URL into the controlled input, the debounce elapses, /preview is called,
 * and the resulting card renders with the video title (plus enrichment).
 */
describe("App preview on paste", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let originalFetch: typeof globalThis.fetch;
  let esInstances: FakeEventSource[] = [];

  beforeEach(() => {
    esInstances = [];
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
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.fetch = originalFetch;
  });

  it("fetches preview after debounce when URL is typed", async () => {
    render(<App />);
    // Flush the initial /jobs fetch.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    // Inside the 500ms debounce window — no /preview call yet.
    const previewCallsBefore = fetchMock.mock.calls.filter(
      (c) => c[0] === "/preview",
    ).length;
    expect(previewCallsBefore).toBe(0);

    // Advance past the debounce + flush enrichment fanout.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const previewCalls = fetchMock.mock.calls.filter(
      (c) => c[0] === "/preview",
    );
    expect(previewCalls.length).toBe(1);
    expect(previewCalls[0][1]).toMatchObject({ method: "POST" });

    // Inline card renders the video title from the /preview payload.
    expect(screen.getByText("Single Vid")).toBeInTheDocument();
  });

  it("coalesces rapid typing into a single /preview call", async () => {
    render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    // Rapidly type three increasingly-complete URLs inside the debounce window.
    fireEvent.change(input, { target: { value: "https://yt/" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    fireEvent.change(input, { target: { value: "https://yt/xy" } });

    // Still inside the debounce window — nothing fired yet.
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/preview").length,
    ).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });

    const previewCalls = fetchMock.mock.calls.filter(
      (c) => c[0] === "/preview",
    );
    expect(previewCalls.length).toBe(1);
  });

  it("shows an inline error for malformed URLs without hitting /preview", async () => {
    render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "not-a-url" } });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });

    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/preview").length,
    ).toBe(0);
    expect(screen.getByText(/Could not preview/)).toBeInTheDocument();
  });

  it("clears the previous preview's Download button immediately when a new URL is typed", async () => {
    render(<App />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    // First URL — let the preview render fully.
    fireEvent.change(input, { target: { value: "https://yt/first" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
      await Promise.resolve();
    });
    // Download button is now visible for the first preview.
    expect(screen.getByRole("button", { name: /^Download$/ })).toBeInTheDocument();

    // User retypes — synchronously, before the new debounce fires.
    fireEvent.change(input, { target: { value: "https://yt/second" } });
    // The old preview (and its Download button) must already be gone.
    expect(
      screen.queryByRole("button", { name: /^Download$/ }),
    ).not.toBeInTheDocument();
    // We should be in the loading state right away.
    expect(screen.getByText(/Fetching preview/i)).toBeInTheDocument();
  });
});
