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

export async function createJobsFromPick(
  urls: string[],
  formatPref?: string
): Promise<Job> {
  const r = await fetch("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ urls, format_pref: formatPref }),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail?.[0]?.msg ?? `createJobsFromPick: ${r.status}`);
  }
  return r.json();
}

export async function cancelJob(id: string): Promise<void> {
  const r = await fetch(`/jobs/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`cancelJob: ${r.status}`);
}

export async function retryJob(id: string): Promise<Job> {
  const r = await fetch(`/jobs/${id}/retry`, { method: "POST" });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail ?? `retry: ${r.status}`);
  }
  return r.json();
}

export interface PreviewEntry {
  url: string;
  id: string | null;
  title: string | null;
  position: number | null;
}

export interface PreviewResponse {
  kind: "video" | "playlist";
  title: string | null;
  entries: PreviewEntry[];
}

export async function previewUrl(
  url: string,
  opts?: { signal?: AbortSignal }
): Promise<PreviewResponse> {
  const r = await fetch("/preview", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url }),
    signal: opts?.signal,
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail ?? detail.detail?.[0]?.msg ?? `preview: ${r.status}`);
  }
  return r.json();
}

export interface EnrichedEntry {
  url: string;
  title: string | null;
  duration_s: number | null;
  uploader: string | null;
  thumbnail_url: string | null;
  error: string | null;
}

export interface EnrichResponse {
  entries: EnrichedEntry[];
}

export async function enrichUrls(urls: string[]): Promise<EnrichResponse> {
  const r = await fetch("/preview/enrich", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ urls }),
  });
  if (!r.ok) throw new Error(`enrich: ${r.status}`);
  return r.json();
}
