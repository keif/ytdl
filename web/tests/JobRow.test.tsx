import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
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
    render(<JobRow job={baseJob} onCancel={() => {}} />);
    expect(screen.getByText("My Video")).toBeInTheDocument();
  });

  it("renders a progress percentage for running jobs", () => {
    render(<JobRow job={baseJob} onCancel={() => {}} />);
    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  it("renders error text when failed", () => {
    render(
      <JobRow
        job={{ ...baseJob, status: "failed", error: "[auth_required] sign in" }}
        onCancel={() => {}}
      />
    );
    expect(screen.getByText(/sign in/)).toBeInTheDocument();
  });

  it("never renders untrusted titles as HTML", () => {
    const xss = "<img src=x onerror=alert(1)>";
    render(<JobRow job={{ ...baseJob, title: xss }} onCancel={() => {}} />);
    // The literal text should appear; no img element should be rendered.
    expect(screen.getByText(xss)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});
