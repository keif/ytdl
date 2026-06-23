import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * The audio-only checkbox is a first-class submit-time override. When checked
 * it must force `format_pref="audio_only"` in the POST body REGARDLESS of the
 * dropdown's value. When unchecked the dropdown's value is honored.
 */
describe("App audio-only toggle", () => {
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
            cookies_source: null,
            deno: { present: true, path: null },
            ffmpeg: { present: true, path: null },
            subtitles_default: false,
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
      if (path === "/preview/clear" || path.startsWith("/preview/clear")) {
        return new Response(JSON.stringify({ clearable: 0 }), {
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

  it("sends format_pref='best' (dropdown default) when audio-only is unchecked", async () => {
    render(<App />);
    // Let /status resolve so the subtitles checkbox is seeded.
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
    expect(postedBodies[0]).toMatchObject({ format_pref: "best" });
  });

  it("forces format_pref='audio_only' when the checkbox is on, regardless of the dropdown", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    // Tick audio-only — dropdown stays on "best" but should be overridden.
    const audioCheckbox = screen.getByRole("checkbox", { name: /audio only/i });
    await act(async () => {
      audioCheckbox.click();
    });

    // The dropdown should now be disabled.
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    // And still showing "best" — proving the override is purely at submit time.
    expect(select.value).toBe("best");

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
    expect(postedBodies[0]).toMatchObject({ format_pref: "audio_only" });
  });

  it("resets audio-only when one URL is replaced with another (select-all-paste)", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    const audioCheckbox = screen.getByRole("checkbox", { name: /audio only/i }) as HTMLInputElement;
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;

    // Paste a URL, tick audio-only, then replace the URL entirely.
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    await act(async () => {
      audioCheckbox.click();
    });
    expect(audioCheckbox.checked).toBe(true);

    // Select-all paste-replace — new value does NOT extend the previous.
    fireEvent.change(input, { target: { value: "https://yt/y" } });

    expect(audioCheckbox.checked).toBe(false);
  });

  it("preserves audio-only when the user types one more character", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    const audioCheckbox = screen.getByRole("checkbox", { name: /audio only/i }) as HTMLInputElement;
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;

    fireEvent.change(input, { target: { value: "https://yt/abc" } });
    await act(async () => {
      audioCheckbox.click();
    });
    // Type ONE more character — the typing case.
    fireEvent.change(input, { target: { value: "https://yt/abcd" } });

    expect(audioCheckbox.checked).toBe(true);
  });

  it("resets audio-only on multi-character extension (paste-extend, not typing)", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    const audioCheckbox = screen.getByRole("checkbox", { name: /audio only/i }) as HTMLInputElement;
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;

    fireEvent.change(input, { target: { value: "https://yt/" } });
    await act(async () => {
      audioCheckbox.click();
    });
    expect(audioCheckbox.checked).toBe(true);

    // Paste-extend: the new value shares the prefix but adds 3+ chars at
    // once, which is paste-shaped (not typing-shaped).
    fireEvent.change(input, { target: { value: "https://yt/xyz" } });

    expect(audioCheckbox.checked).toBe(false);
  });

  it("resets audio-only when the URL input is manually cleared", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("checkbox", { name: /audio only/i })).toBeInTheDocument();
    });

    // Tick audio-only, then type a URL, then clear the input manually.
    const audioCheckbox = screen.getByRole("checkbox", { name: /audio only/i }) as HTMLInputElement;
    await act(async () => {
      audioCheckbox.click();
    });
    expect(audioCheckbox.checked).toBe(true);

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    // Manual clear — the per-paste contract says audio-only should reset.
    fireEvent.change(input, { target: { value: "" } });

    expect(audioCheckbox.checked).toBe(false);
    // And the dropdown should be enabled again.
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
  });
});
