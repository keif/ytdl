import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { PreviewVideo } from "../src/components/PreviewVideo";
import type { PreviewEntry } from "../src/api";

const baseEntry: PreviewEntry = {
  url: "https://yt/abc",
  id: "abc",
  title: "My Video",
  position: 1,
};

describe("PreviewVideo", () => {
  it("renders title + url + format chip even with no enrichment", () => {
    render(
      <PreviewVideo
        entry={baseEntry}
        format="1080p"
        onDownload={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByText("My Video")).toBeInTheDocument();
    expect(screen.getByText("https://yt/abc")).toBeInTheDocument();
    expect(screen.getByText(/1080p/i)).toBeInTheDocument();
  });

  it("shows uploader and duration when enriched", () => {
    render(
      <PreviewVideo
        entry={baseEntry}
        enriched={{
          url: baseEntry.url,
          title: "My Video (enriched)",
          uploader: "Cool Channel",
          duration_s: 125,
          thumbnail_url: "https://yt/thumb.jpg",
          error: null,
        }}
        format="best"
        onDownload={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByText("My Video (enriched)")).toBeInTheDocument();
    expect(screen.getByText("Cool Channel")).toBeInTheDocument();
    expect(screen.getByText("2:05")).toBeInTheDocument();
    // alt="" gives the <img> role="presentation", so query by tag instead.
    const img = document.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "https://yt/thumb.jpg");
  });

  it("calls onDownload when the button is clicked", async () => {
    const onDownload = vi.fn(async () => {});
    render(
      <PreviewVideo
        entry={baseEntry}
        format="best"
        onDownload={onDownload}
        busy={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /Download/i });
    await act(async () => {
      btn.click();
    });
    expect(onDownload).toHaveBeenCalled();
  });

  it("disables the button while busy", () => {
    render(
      <PreviewVideo
        entry={baseEntry}
        format="best"
        onDownload={async () => {}}
        busy={true}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
  });

  it("falls back to the entry url when no title is available", () => {
    render(
      <PreviewVideo
        entry={{ ...baseEntry, title: null }}
        format="best"
        onDownload={async () => {}}
        busy={false}
      />,
    );
    // Both the heading and the url subtitle render the url string.
    const matches = screen.getAllByText("https://yt/abc");
    expect(matches.length).toBeGreaterThan(0);
  });

  it("renders the already-downloaded warning banner with the path", () => {
    render(
      <PreviewVideo
        entry={{
          ...baseEntry,
          already_downloaded: {
            path: "/data/out/My Video [abc12345678].mp4",
            title: "My Video",
          },
        }}
        format="best"
        onDownload={async () => {}}
        busy={false}
      />,
    );
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(/Already downloaded/i);
    expect(banner).toHaveTextContent(
      "/data/out/My Video [abc12345678].mp4",
    );
  });

  it("swaps Download for Force re-download when already downloaded", () => {
    render(
      <PreviewVideo
        entry={{
          ...baseEntry,
          already_downloaded: {
            path: "/data/out/x.mp4",
            title: null,
          },
        }}
        format="best"
        onDownload={async () => {}}
        busy={false}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Force re-download/i }),
    ).toBeInTheDocument();
    // The plain Download button label must NOT appear as an exact match
    // when the duplicate state is active.
    expect(
      screen.queryByRole("button", { name: /^Download$/ }),
    ).not.toBeInTheDocument();
  });

  it("calls onDownload when Force re-download is clicked", async () => {
    const onDownload = vi.fn(async () => {});
    render(
      <PreviewVideo
        entry={{
          ...baseEntry,
          already_downloaded: {
            path: "/data/out/x.mp4",
            title: null,
          },
        }}
        format="best"
        onDownload={onDownload}
        busy={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /Force re-download/i });
    await act(async () => {
      btn.click();
    });
    expect(onDownload).toHaveBeenCalled();
  });
});
