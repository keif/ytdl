import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { PreviewPanel } from "../src/components/PreviewPanel";
import type { PreviewEntry } from "../src/api";

const entries: PreviewEntry[] = [
  { url: "https://x/a", id: "a", title: "Alpha", position: 1 },
  { url: "https://x/b", id: "b", title: "Bravo", position: 2 },
  { url: "https://x/c", id: "c", title: "Charlie", position: 3 },
];

describe("PreviewPanel", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    // Default: enrichment returns empty (panel still renders from flat data).
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ entries: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders one row per entry, all selected by default", () => {
    render(
      <PreviewPanel
        title="My Playlist"
        entries={entries}
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText("My Playlist")).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Bravo")).toBeInTheDocument();
    expect(screen.getByText("Charlie")).toBeInTheDocument();
    expect(screen.getByText(/3 entries — 3 selected/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /Download 3 selected/ });
    expect(button).toBeEnabled();
  });

  it("toggling a checkbox updates the selected count", async () => {
    render(
      <PreviewPanel
        title="My Playlist"
        entries={entries}
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    const checkbox = screen.getByLabelText("select Alpha");
    fireEvent.click(checkbox);
    expect(screen.getByText(/3 entries — 2 selected/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Download 2 selected/ }),
    ).toBeInTheDocument();
  });

  it("'select none' disables the submit and 'select all' re-enables it", () => {
    render(
      <PreviewPanel
        title="My Playlist"
        entries={entries}
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /select none/ }));
    expect(screen.getByRole("button", { name: /Download 0 selected/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /select all/ }));
    expect(screen.getByRole("button", { name: /Download 3 selected/ })).toBeEnabled();
  });

  it("onConfirm receives URLs in original playlist order", async () => {
    const seen = vi.fn(async () => {});
    render(
      <PreviewPanel
        title="P"
        entries={entries}
        onConfirm={seen}
        onCancel={() => {}}
      />,
    );
    // Deselect Bravo (middle), keep Alpha + Charlie.
    fireEvent.click(screen.getByLabelText("select Bravo"));
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Download 2 selected/ }),
      );
    });
    expect(seen).toHaveBeenCalledWith(["https://x/a", "https://x/c"]);
  });

  it("renders enriched details when the enrich endpoint returns them", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          entries: [
            {
              url: "https://x/a",
              title: "Alpha (enriched)",
              duration_s: 65,
              uploader: "Channel A",
              thumbnail_url: null,
              error: null,
            },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    ) as unknown as typeof globalThis.fetch;

    await act(async () => {
      render(
        <PreviewPanel
          title="P"
          entries={entries}
          onConfirm={async () => {}}
          onCancel={() => {}}
        />,
      );
    });
    expect(await screen.findByText("Alpha (enriched)")).toBeInTheDocument();
    expect(screen.getByText(/Channel A/)).toBeInTheDocument();
    // 65s -> "1:05"
    expect(screen.getByText(/1:05/)).toBeInTheDocument();
  });
});
