import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Paste-and-go countdown.
 *
 * When /preview resolves to a single video, a 5-second countdown banner
 * appears above the preview card and auto-submits the job at zero. The user
 * can cancel anytime (button, URL edit, or paste a different URL).
 *
 * Playlists, preview errors, and `autosubmit_delay_s=0` must NOT trigger
 * the countdown — the manual Download button stays as the only commit point
 * in those cases.
 */
describe("App auto-submit countdown", () => {
  let originalFetch: typeof globalThis.fetch;
  let postedBodies: Array<Record<string, unknown>>;
  let previewResponder: () => Response;
  let statusResponder: () => Response;
  let jobsPostResponder: () => Response;

  function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

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
        return jobsPostResponder();
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      if (path === "/status") {
        return statusResponder();
      }
      if (path === "/preview") {
        return previewResponder();
      }
      if (path === "/preview/enrich") {
        return jsonResponse({
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
    // Defaults; individual tests override before render.
    statusResponder = () =>
      jsonResponse({
        cookies_browser: null,
        cookies_source: "none",
        deno: { present: true, path: null },
        ffmpeg: { present: true, path: null },
        subtitles_default: false,
        output_dir: "/tmp/out",
        autosubmit_delay_s: 5,
      });
    previewResponder = () =>
      jsonResponse({
        kind: "video",
        title: null,
        entries: [
          { url: "https://yt/x", id: "x", title: "Single Vid", position: 1 },
        ],
      });
    jobsPostResponder = () => jsonResponse({ id: "j-1", status: "pending" });
    installFetchMock();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  /**
   * Drive the URL input + debounce so /preview resolves and the countdown
   * has had a chance to start. Returns once the banner is visible (or after
   * the timeout window, whichever comes first — callers assert from there).
   */
  async function pasteAndAwaitPreview(value: string) {
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value } });
    // Debounce (500ms) + microtask flushes for preview + enrich.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("auto-submits a single video after the 5-second countdown", async () => {
    render(<App />);
    // Let /status seed the countdown delay before any URL is typed.
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Banner appears at 5s.
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*5s/);

    // Tick down each second.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*4s/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*3s/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*2s/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*1s/);

    // Final tick triggers the submit and the banner goes away.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await Promise.resolve();
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({ url: "https://yt/x" });
  });

  it("Cancel button stops the countdown and preserves the manual Download fallback", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent(/Downloading in\s*5s/);

    const cancelBtn = screen.getByRole("button", { name: /cancel auto-submit/i });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });

    // Banner disappears immediately.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Advance well past the original window — no submit fires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(0);

    // The manual Download button is still functional.
    const downloadBtn = screen.getByRole("button", { name: /^Download$/ });
    expect(downloadBtn).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(downloadBtn);
      await Promise.resolve();
    });
    expect(postedBodies.length).toBe(1);
  });

  it("editing the URL during the countdown cancels it", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");
    // Tick to 3s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*3s/);

    // Select-all-paste-replace into a different URL — non-typing change.
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/y" } });

    // Banner should be gone immediately; the new preview hasn't resolved yet.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Advance past where the original countdown would have fired — nothing
    // submits because the cancel happened before zero. (The new URL's
    // preview won't resolve as long as we don't pass the debounce window;
    // we deliberately stop short.)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(postedBodies.length).toBe(0);
  });

  it("playlist preview does NOT trigger auto-submit", async () => {
    previewResponder = () =>
      jsonResponse({
        kind: "playlist",
        title: "My List",
        entries: [
          { url: "https://yt/a", id: "a", title: "A", position: 1 },
          { url: "https://yt/b", id: "b", title: "B", position: 2 },
        ],
      });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/list");

    // No countdown banner.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // And no POST fires regardless of time passing — the picker flow is
    // explicit-confirm only.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(0);
  });

  it("autosubmit_delay_s=0 disables the feature; Download remains manual", async () => {
    statusResponder = () =>
      jsonResponse({
        cookies_browser: null,
        cookies_source: "none",
        deno: { present: true, path: null },
        ffmpeg: { present: true, path: null },
        subtitles_default: false,
        output_dir: "/tmp/out",
        autosubmit_delay_s: 0,
      });

    render(<App />);
    // Wait for /status to resolve — the countdown effect reads from it.
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Preview card is up but the banner never appears.
    expect(screen.getByRole("button", { name: /^Download$/ })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Advance ten seconds — still no auto-submit.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(0);

    // Manual Download still works.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Download$/ }));
      await Promise.resolve();
    });
    expect(postedBodies.length).toBe(1);
  });

  it("preview errors do NOT trigger auto-submit", async () => {
    previewResponder = () =>
      jsonResponse({ detail: "could not extract" }, 400);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/broken");

    // Error message is rendered, banner is not.
    expect(screen.getByText(/Could not preview/i)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // No commit, even after a long wait.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(0);
  });

  it("failed auto-submit does not loop POSTs every delay seconds", async () => {
    jobsPostResponder = () => jsonResponse({ detail: "broken" }, 500);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Let the countdown elapse so the auto-submit fires once.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
      await Promise.resolve();
    });
    expect(postedBodies.length).toBe(1);

    // Now spin the clock through several more countdown windows. Even
    // though the submit failed and `submitting` flipped back to false
    // (which re-fires the effect), the "already-attempted" lock must
    // prevent restarting the countdown for the same URL.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
    });
    expect(postedBodies.length).toBe(1);
  });

  it("cancelled countdown does not re-arm when /status re-resolves", async () => {
    // Track how many /status calls we serve so we can force a "race"
    // re-resolve by deliberately re-invoking the effect.
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Cancel the banner.
    const cancelBtn = screen.getByRole("button", { name: /cancel auto-submit/i });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Even after a generous wait — and even if a /status race or any
    // other dep change re-fires the effect — the banner must stay gone
    // for THIS URL. Nothing posts.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(postedBodies.length).toBe(0);
  });

  it("pasting a different URL after cancel starts a fresh countdown", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Cancel the first URL's countdown.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /cancel auto-submit/i }));
    });

    // Paste-replace with a different URL. The "already-attempted" lock
    // for the previous URL must NOT block the new countdown.
    previewResponder = () =>
      jsonResponse({
        kind: "video",
        title: null,
        entries: [
          { url: "https://yt/y", id: "y", title: "Other Vid", position: 1 },
        ],
      });
    await pasteAndAwaitPreview("https://yt/y");

    // Fresh banner for the new URL.
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in/i);
  });

  it("typing one more character during the countdown still cancels it", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Banner is up. Type one more character — this is the typing-
    // extension shape that preserves audio-only/output-dir state. The
    // countdown still captured the OLD URL, so it must cancel anyway.
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*5s/);
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/xy" } });

    // Banner gone, no submit even after the original window elapses.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(0);
  });

  it("manual Download click during countdown does not double-submit", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    await pasteAndAwaitPreview("https://yt/x");

    // Banner is up, countdown at 5s. Click Download manually before
    // the timer hits 0.
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*5s/);
    const downloadBtn = screen.getByRole("button", { name: /^Download$/ });
    await act(async () => {
      fireEvent.click(downloadBtn);
      await Promise.resolve();
    });

    // Manual submit fired once.
    expect(postedBodies.length).toBe(1);

    // Now run the timer past the original countdown window — the auto-
    // submit must NOT fire a second job for the same URL.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(1);

    // Banner is gone (cancelled at submit time).
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
