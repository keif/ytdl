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

  it("treats duplicate URLs as independently selectable", async () => {
    const dupEntries: PreviewEntry[] = [
      { url: "https://yt/a", id: "a", title: "Song 1", position: 1 },
      { url: "https://yt/a", id: "a", title: "Song 1 (dup)", position: 2 },
      { url: "https://yt/b", id: "b", title: "Song 2", position: 3 },
    ];
    const onConfirm = vi.fn(async () => {});
    render(
      <PreviewPanel
        title="Mix"
        entries={dupEntries}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    // All three should be checked by default.
    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes.filter((c) => (c as HTMLInputElement).checked).length).toBe(3);
    // Unchecking the first duplicate must NOT affect the second.
    await act(async () => {
      checkboxes[0].click();
    });
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(false);
    expect((checkboxes[1] as HTMLInputElement).checked).toBe(true);
    expect((checkboxes[2] as HTMLInputElement).checked).toBe(true);
    // Confirm enqueues 2 URLs in original order.
    const confirmBtn = screen.getByRole("button", { name: /Download 2 selected/i });
    await act(async () => {
      confirmBtn.click();
    });
    expect(onConfirm).toHaveBeenCalledWith(["https://yt/a", "https://yt/b"]);
  });

  it("renders a badge for entries flagged already_downloaded", async () => {
    const withDup: PreviewEntry[] = [
      {
        url: "https://x/a",
        id: "a",
        title: "Alpha",
        position: 1,
        already_downloaded: {
          path: "/data/out/Alpha [aaa11111111].mp4",
          title: "Alpha",
        },
      },
      { url: "https://x/b", id: "b", title: "Bravo", position: 2 },
    ];
    render(
      <PreviewPanel
        title="Mix"
        entries={withDup}
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    // The header summary counts duplicates.
    expect(
      screen.getByText(/1 already downloaded/i),
    ).toBeInTheDocument();
    // Duplicate row carries an "already downloaded" aria label.
    expect(
      screen.getByLabelText(/already downloaded/i),
    ).toBeInTheDocument();
  });

  it("Include-already-downloaded toggle is off by default and unchecks duplicates", async () => {
    const withDup: PreviewEntry[] = [
      {
        url: "https://x/a",
        id: "a",
        title: "Alpha",
        position: 1,
        already_downloaded: {
          path: "/data/out/Alpha [aaa11111111].mp4",
          title: "Alpha",
        },
      },
      { url: "https://x/b", id: "b", title: "Bravo", position: 2 },
    ];
    render(
      <PreviewPanel
        title="Mix"
        entries={withDup}
        onConfirm={async () => {}}
        onCancel={() => {}}
      />,
    );
    // The include-duplicates toggle exists and is off by default.
    const toggle = screen.getByLabelText(
      /Include already-downloaded/i,
    ) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    // Only the non-duplicate entry is selected initially. The submit
    // button label surfaces the selection count without ambiguity.
    expect(
      screen.getByRole("button", { name: /Download 1 selected/i }),
    ).toBeInTheDocument();
    // Enable the toggle; both rows become selected.
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(toggle.checked).toBe(true);
    expect(
      screen.getByRole("button", { name: /Download 2 selected/i }),
    ).toBeInTheDocument();
  });

  it("resets selection when entries prop changes", async () => {
    const onConfirm = vi.fn(async () => {});
    const firstEntries: PreviewEntry[] = [
      { url: "https://yt/a", id: "a", title: "A1", position: 1 },
      { url: "https://yt/b", id: "b", title: "A2", position: 2 },
    ];
    const { rerender } = render(
      <PreviewPanel
        title="A"
        entries={firstEntries}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    // Uncheck one row in the first playlist.
    let checkboxes = await screen.findAllByRole("checkbox");
    await act(async () => {
      checkboxes[0].click();
    });
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(false);

    // Re-render with a different playlist.
    const secondEntries: PreviewEntry[] = [
      { url: "https://yt/x", id: "x", title: "B1", position: 1 },
      { url: "https://yt/y", id: "y", title: "B2", position: 2 },
      { url: "https://yt/z", id: "z", title: "B3", position: 3 },
    ];
    rerender(
      <PreviewPanel
        title="B"
        entries={secondEntries}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    checkboxes = await screen.findAllByRole("checkbox");
    // All three rows of the new playlist should be selected.
    expect(checkboxes.filter((c) => (c as HTMLInputElement).checked).length).toBe(3);
  });
});
