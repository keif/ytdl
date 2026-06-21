import { act, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { JobRow } from "../src/components/JobRow";
import type { Job } from "../src/api";

const baseJob: Job = {
  id: "abc",
  url: "https://youtu.be/abc",
  kind: "video",
  parent_job_id: null,
  status: "running",
  format_pref: "best",
  output_dir: "/out",
  output_path: null,
  title: "My Video",
  video_id: "abc",
  uploader: "Channel",
  duration_s: 60,
  filesize_bytes: 1000,
  bytes_done: 500,
  speed_bps: 100,
  eta_s: 5,
  error: null,
  attempts: 1,
  created_at: 0,
  started_at: 0,
  finished_at: null,
};

describe("JobRow", () => {
  it("shows the title when present", () => {
    render(<JobRow job={baseJob} onCancel={() => {}} onRetry={() => {}} />);
    expect(screen.getByText("My Video")).toBeInTheDocument();
  });

  it("renders a progress percentage for running jobs", () => {
    render(<JobRow job={baseJob} onCancel={() => {}} onRetry={() => {}} />);
    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  it("renders error text when failed", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "failed", error: "[auth_required] sign in" }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText(/sign in/)).toBeInTheDocument();
  });

  it("never renders untrusted titles as HTML", () => {
    const xss = "<img src=x onerror=alert(1)>";
    render(
      <JobRow
        job={{ ...baseJob, title: xss }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    // The literal text should appear; no img element should be rendered.
    expect(screen.getByText(xss)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders a Retry button for failed jobs", () => {
    const onRetry = vi.fn();
    render(
      <JobRow
        job={{ ...baseJob, status: "failed", error: "[forbidden] cookies needed" }}
        onCancel={() => {}}
        onRetry={onRetry}
      />
    );
    const btn = screen.getByRole("button", { name: /retry/i });
    expect(btn).toBeInTheDocument();
  });

  it("does not render Retry button for running jobs", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "running" }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("calls onRetry when retry is clicked", async () => {
    const onRetry = vi.fn();
    render(
      <JobRow
        job={{ ...baseJob, status: "canceled" }}
        onCancel={() => {}}
        onRetry={onRetry}
      />
    );
    const btn = screen.getByRole("button", { name: /retry/i });
    await act(async () => {
      btn.click();
    });
    expect(onRetry).toHaveBeenCalledWith(baseJob.id);
  });

  it("renders Retry button for done jobs", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "done", output_path: "/o/x.mp4" }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows a relative timestamp for done jobs", () => {
    const oneHourAgo = Date.now() - 60 * 60 * 1000;
    render(
      <JobRow
        job={{ ...baseJob, status: "done", finished_at: oneHourAgo, output_path: "/x.mp4" }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText(/finished 1h ago/i)).toBeInTheDocument();
  });

  it("shows attempt count when greater than 1", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "failed", attempts: 3, finished_at: Date.now() - 5000 }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText(/3 attempts/i)).toBeInTheDocument();
  });

  it("shows speed and ETA for running jobs when both fields are set", () => {
    render(
      <JobRow
        job={{
          ...baseJob,
          status: "running",
          speed_bps: 5_242_880, // 5 MB/s
          eta_s: 125,            // 2m 05s
          filesize_bytes: 1_000_000,
          bytes_done: 500_000,
        }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("5.0 MB/s")).toBeInTheDocument();
    expect(screen.getByText("· ETA 2m 05s")).toBeInTheDocument();
  });

  it("shows only speed when ETA is missing", () => {
    render(
      <JobRow
        job={{
          ...baseJob,
          status: "running",
          speed_bps: 512_000,    // 500 KB/s
          eta_s: null,
        }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.getByText("500.0 KB/s")).toBeInTheDocument();
    expect(screen.queryByText(/ETA/i)).not.toBeInTheDocument();
  });

  it("does not render speed/ETA strip when neither value is present", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "running", speed_bps: null, eta_s: null }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.queryByText(/MB\/s/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ETA/i)).not.toBeInTheDocument();
  });

  it("does not render speed/ETA for non-running jobs", () => {
    render(
      <JobRow
        job={{
          ...baseJob,
          status: "done",
          speed_bps: 5_000_000,
          eta_s: 60,
          finished_at: Date.now() - 1000,
        }}
        onCancel={() => {}}
        onRetry={() => {}}
      />
    );
    expect(screen.queryByText(/MB\/s/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ETA/i)).not.toBeInTheDocument();
  });
});
