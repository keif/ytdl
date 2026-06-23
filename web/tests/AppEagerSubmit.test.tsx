import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import App from "../src/App";

/**
 * Eager submit: the Queue button (and Enter inside the URL input) commits a
 * job the moment the URL passes the http(s) shape check — no waiting on
 * /preview. The preview keeps fetching in parallel as informational context.
 *
 * Regression coverage:
 *   - /preview is deliberately slow (5s) so any test that asserts a POST
 *     before the preview resolves proves we don't block on it.
 *   - The auto-submit countdown's cancel-on-manual-submit (PR #44) is
 *     re-verified through the new Queue path so we can't accidentally
 *     double-fire when the user clicks Queue while the banner is ticking.
 */
describe("App eager submit (Queue button + Enter)", () => {
  let originalFetch: typeof globalThis.fetch;
  let postedBodies: Array<Record<string, unknown>>;
  let postTimestamps: number[];
  let previewResolveDelayMs: number;
  let statusResponder: () => Response;

  function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }

  function installFetchMock() {
    postedBodies = [];
    postTimestamps = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        postTimestamps.push(Date.now());
        return jsonResponse({ id: `j-${postedBodies.length}`, status: "pending" });
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
        // Deliberately slow so any test that submits "before preview" is
        // proving the new fast path, not just a faster network mock.
        if (previewResolveDelayMs > 0) {
          await new Promise((r) => setTimeout(r, previewResolveDelayMs));
        }
        return jsonResponse({
          kind: "video",
          title: null,
          entries: [
            { url: "https://yt/x", id: "x", title: "Single Vid", position: 1 },
          ],
        });
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
    previewResolveDelayMs = 5000;
    statusResponder = () =>
      jsonResponse({
        cookies_browser: null,
        cookies_source: "none",
        deno: { present: true, path: null },
        ffmpeg: { present: true, path: null },
        subtitles_default: false,
        output_dir: "/tmp/out",
        autosubmit_delay_s: 5,
        probe_timeout_s: 30,
      });
    installFetchMock();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it("Queue button submits before /preview resolves", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    // Queue is enabled immediately — no /preview wait.
    const queueBtn = screen.getByRole("button", { name: /^Queue$/ });
    expect((queueBtn as HTMLButtonElement).disabled).toBe(false);

    // Click well within the preview's 5s delay window so we prove the
    // submit isn't gated on it. Don't advance timers past the debounce —
    // the preview request hasn't even been kicked off yet.
    await act(async () => {
      fireEvent.click(queueBtn);
      await Promise.resolve();
    });

    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({ url: "https://yt/x" });

    // No timers advanced past the preview-resolve threshold — the POST
    // happened on the synchronous click path, not after a 5s wait.
  });

  it("Enter key submits before /preview resolves", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    // Pressing Enter in a form's input bubbles a submit event to the
    // form. fireEvent.submit on the form simulates exactly that.
    await act(async () => {
      fireEvent.submit(input.closest("form")!);
      await Promise.resolve();
    });

    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({ url: "https://yt/x" });
  });

  it("Queue passes audio_only and output_dir overrides through to /jobs", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    // Toggle audio-only after pasting (typing-shape "fresh paste" should
    // preserve it across the immediate Queue click).
    const audioOnly = screen.getByRole("checkbox", { name: /audio only/i });
    await act(async () => {
      fireEvent.click(audioOnly);
    });

    const queueBtn = screen.getByRole("button", { name: /^Queue$/ });
    await act(async () => {
      fireEvent.click(queueBtn);
      await Promise.resolve();
    });

    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({
      url: "https://yt/x",
      format_pref: "audio_only",
    });
  });

  it("Queue click while auto-submit countdown is active doesn't double-fire", async () => {
    // For this test we want the preview to resolve fast so the countdown
    // actually starts — eager-submit's job is to skip the wait, but the
    // regression we're guarding against is a Queue click DURING the
    // countdown firing twice.
    previewResolveDelayMs = 0;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    vi.useFakeTimers();
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    // Run the debounce + enrich microtasks so the preview resolves and the
    // countdown banner mounts.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // Banner is up at 5s.
    expect(screen.getByRole("status")).toHaveTextContent(/Downloading in\s*5s/);

    // Click Queue while the banner is ticking. submitSingle() calls
    // cancelAutoSubmit() at the top, so the timer should not fire a
    // second POST.
    const queueBtn = screen.getByRole("button", { name: /^Queue$/ });
    await act(async () => {
      fireEvent.click(queueBtn);
      await Promise.resolve();
    });

    expect(postedBodies.length).toBe(1);

    // Advance well past where the countdown would have fired — exactly
    // one POST, banner gone.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(postedBodies.length).toBe(1);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears the URL input synchronously on Queue click — POST runs in background", async () => {
    // The user's rapid-queue UX needs the input cleared the moment they
    // click Queue, NOT when the POST resolves. Otherwise the input is
    // held captive by a slow /jobs round-trip and they can't paste the
    // next URL.
    let resolvePost: ((value: Response) => void) | null = null;
    const slowPost = new Promise<Response>((r) => {
      resolvePost = r;
    });

    const originalMock = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        return slowPost;
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path === "/status") return statusResponder();
      if (path === "/preview") {
        // Don't matter for this test — fail fast so the preview useEffect
        // doesn't sit holding any state we care about.
        return jsonResponse({ detail: "not relevant" }, 400);
      }
      if (path === "/preview/enrich") return jsonResponse({ entries: [] });
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    expect(input.value).toBe("https://yt/x");

    const queueBtn = screen.getByRole("button", { name: /^Queue$/ });
    await act(async () => {
      fireEvent.click(queueBtn);
      await Promise.resolve();
    });

    // CRITICAL: input is clear right now, even though the POST is still pending.
    expect(input.value).toBe("");
    expect(postedBodies.length).toBe(1);
    expect(postedBodies[0]).toMatchObject({ url: "https://yt/x" });

    // The user can paste the next URL immediately, without waiting on the
    // first POST.
    fireEvent.change(input, { target: { value: "https://yt/y" } });
    expect(input.value).toBe("https://yt/y");

    // Resolve the first POST so the second one (when triggered) is observable
    // as a distinct call. We don't need to assert the second POST here —
    // this test's contract is "input clears synchronously."
    await act(async () => {
      resolvePost!(jsonResponse({ id: "j-1", status: "pending" }));
      await Promise.resolve();
    });

    globalThis.fetch = originalMock;
  });

  it("restores URL on POST failure when the user hasn't moved on", async () => {
    // Codex review: invalid output_dir or backend hiccup returns 4xx/5xx.
    // The user needs the URL back to correct and retry without retyping.
    const originalMock = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        return jsonResponse({ detail: "output_dir must be a writable directory" }, 400);
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path === "/status") return statusResponder();
      if (path === "/preview") return jsonResponse({ detail: "shrug" }, 400);
      if (path === "/preview/enrich") return jsonResponse({ entries: [] });
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://yt/x" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
      await Promise.resolve();
      await Promise.resolve();
    });

    // After the failure, the URL must come back so the user can correct.
    await waitFor(() => {
      expect(input.value).toBe("https://yt/x");
    });

    globalThis.fetch = originalMock;
  });

  it("preserves user's newer paste when an earlier submit fails after they moved on", async () => {
    // If the user has already pasted the next URL while the first POST was
    // still in flight, restoring the failed URL would clobber their typing.
    // The restore must be "best effort" — only when the field is still
    // empty.
    let resolveFirstPost: ((value: Response) => void) | null = null;
    const slowFailingPost = new Promise<Response>((r) => {
      resolveFirstPost = r;
    });
    let postCount = 0;
    const originalMock = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postCount += 1;
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        if (postCount === 1) return slowFailingPost;
        return jsonResponse({ id: `j-${postCount}`, status: "pending" });
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path === "/status") return statusResponder();
      if (path === "/preview") return jsonResponse({ detail: "shrug" }, 400);
      if (path === "/preview/enrich") return jsonResponse({ entries: [] });
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;

    // First URL: paste + Queue. POST hangs.
    fireEvent.change(input, { target: { value: "https://yt/first" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
      await Promise.resolve();
    });
    expect(input.value).toBe("");

    // User moves on: pastes the next URL while the first POST is still
    // in flight.
    fireEvent.change(input, { target: { value: "https://yt/second" } });
    expect(input.value).toBe("https://yt/second");

    // Now the first POST resolves as a failure.
    await act(async () => {
      resolveFirstPost!(jsonResponse({ detail: "boom" }, 500));
      await Promise.resolve();
      await Promise.resolve();
    });

    // The user's newer paste survives — the restore was a no-op because
    // the field wasn't empty.
    expect(input.value).toBe("https://yt/second");

    globalThis.fetch = originalMock;
  });

  it("does not restore the URL when only refreshAll fails after a successful POST", async () => {
    // Codex review: separating the POST from refreshAll prevents a
    // double-enqueue. The POST succeeds → job is on the server. If the
    // listing refresh then fails (network glitch), we must NOT restore
    // the URL — that would let the user retry from the form and create
    // a duplicate.
    let refreshShouldFail = true;
    const originalMock = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        return jsonResponse({ id: "j-1", status: "pending" });
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        if (refreshShouldFail) {
          refreshShouldFail = false;
          return new Response("boom", { status: 500 });
        }
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path === "/status") return statusResponder();
      if (path === "/preview") return jsonResponse({ detail: "shrug" }, 400);
      if (path === "/preview/enrich") return jsonResponse({ entries: [] });
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
      // Two ticks to flush both the POST resolution and the refreshAll
      // rejection.
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    // The job WAS queued (POST succeeded). The URL must stay empty so
    // the user doesn't retry from the form.
    expect(postedBodies.length).toBe(1);
    expect(input.value).toBe("");

    globalThis.fetch = originalMock;
  });

  it("preserves the submit error message after restoring the URL", async () => {
    // Codex review: the preview useEffect used to unconditionally clear
    // submitError, which wiped the error the moment we restored the URL.
    // The error message must persist so the user can see what went wrong.
    const originalMock = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (path === "/jobs" && init?.method === "POST") {
        postedBodies.push(JSON.parse((init.body as string) ?? "{}"));
        return jsonResponse({ detail: "output_dir must be a writable directory" }, 400);
      }
      if (path === "/jobs" || path.startsWith("/jobs?")) {
        return jsonResponse({ jobs: [], total: 0 });
      }
      if (path === "/status") return statusResponder();
      if (path === "/preview") return jsonResponse({ detail: "shrug" }, 400);
      if (path === "/preview/enrich") return jsonResponse({ entries: [] });
      if (path.startsWith("/jobs/clear/preview")) {
        return jsonResponse({ clearable: 0, older_than_days: 7 });
      }
      return new Response("not found", { status: 404 });
    }) as unknown as typeof globalThis.fetch;

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
      await Promise.resolve();
      await Promise.resolve();
    });

    // URL restored AND the error message is still visible (one would be
    // useless without the other).
    await waitFor(() => {
      expect(input.value).toBe("https://yt/x");
    });
    // The api.ts layer surfaces detail.detail when it's a plain string,
    // so the user sees the actual reason their job was rejected.
    await waitFor(() => {
      expect(screen.queryByText(/writable directory/i)).toBeInTheDocument();
    });

    globalThis.fetch = originalMock;
  });

  it("Queue button stays disabled until the URL passes the shape check", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Paste a YouTube URL/i)).toBeInTheDocument();
    });

    const queueBtn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    // Empty input.
    expect(queueBtn.disabled).toBe(true);

    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    // Partial URL still failing the shape check.
    fireEvent.change(input, { target: { value: "htt" } });
    expect(queueBtn.disabled).toBe(true);

    // Full https URL — Queue lights up immediately.
    fireEvent.change(input, { target: { value: "https://yt/x" } });
    expect(queueBtn.disabled).toBe(false);
  });
});
