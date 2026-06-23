import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Codex review caught: if /status is slow and the server has
 * `subtitles_default = true`, an untouched checkbox would still send
 * `subtitles: false`, flipping the server-side default. The submit path
 * must keep `subtitles` undefined until /status resolves OR the user
 * has explicitly toggled it.
 */
describe("App submit subtitles load race", () => {
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
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("omits subtitles from the POST body when status hasn't resolved", async () => {
    // /status hangs forever for this test — simulating a slow backend.
    let statusResolve: () => void = () => {};
    const statusPromise = new Promise<void>((r) => {
      statusResolve = r;
    });

    const postedBodies: unknown[] = [];
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
        await statusPromise;
        return new Response(
          JSON.stringify({
            cookies_browser: null,
            cookies_source: null,
            deno: { present: true, path: null },
            ffmpeg: { present: true, path: null },
            subtitles_default: true,
            probe_timeout_s: 30,
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
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    vi.useFakeTimers();
    try {
      render(<App />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

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

      expect(postedBodies.length).toBe(1);
      // The critical assertion: subtitles must NOT appear in the body, so
      // the server applies its `subtitles_default = true` config.
      expect(postedBodies[0]).not.toHaveProperty("subtitles");
    } finally {
      vi.useRealTimers();
      statusResolve();
    }
  });

  it("sends subtitles from config default once status has loaded", async () => {
    const postedBodies: unknown[] = [];
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
            cookies_source: null,
            deno: { present: true, path: null },
            ffmpeg: { present: true, path: null },
            subtitles_default: true,
            probe_timeout_s: 30,
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
      return new Response("not found", { status: 404 });
    });
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;

    render(<App />);
    // Wait for /status to resolve and seed the checkbox to true.
    await waitFor(() => {
      const checkbox = screen.getByRole("checkbox", { name: /Subtitles/i });
      expect((checkbox as HTMLInputElement).checked).toBe(true);
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
    expect(postedBodies[0]).toMatchObject({ subtitles: true });
  });
});
