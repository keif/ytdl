export type JobStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "canceling"
  | "canceled";

export interface Job {
  id: string;
  url: string;
  kind: "video" | "playlist";
  parent_job_id: string | null;
  status: JobStatus;
  format_pref: string;
  output_dir: string;
  output_path: string | null;
  title: string | null;
  video_id: string | null;
  uploader: string | null;
  duration_s: number | null;
  filesize_bytes: number | null;
  bytes_done: number | null;
  speed_bps: number | null;
  eta_s: number | null;
  error: string | null;
  attempts: number;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface JobList {
  jobs: Job[];
  total: number;
}

export async function listJobs(): Promise<JobList> {
  const r = await fetch("/jobs");
  if (!r.ok) throw new Error(`listJobs: ${r.status}`);
  return r.json();
}

export async function createJob(url: string, formatPref?: string): Promise<Job> {
  const r = await fetch("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url, format_pref: formatPref }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail?.[0]?.msg ?? `createJob: ${r.status}`);
  }
  return r.json();
}

export async function cancelJob(id: string): Promise<void> {
  const r = await fetch(`/jobs/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`cancelJob: ${r.status}`);
}
