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
  force_overwrite: boolean;
  subtitles: boolean;
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

export async function getJob(id: string): Promise<Job> {
  const r = await fetch(`/jobs/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`getJob: ${r.status}`);
  return r.json();
}

/** Optional flags accepted by /jobs POST. `force_overwrite` bypasses the
 * server's duplicate-detection 409 and also tells yt-dlp to overwrite an
 * existing output file on disk — the same flag the /redownload endpoint
 * sets on the cloned job row. */
export interface CreateJobOptions {
  force_overwrite?: boolean;
}

export async function createJob(
  url: string,
  formatPref?: string,
  subtitles?: boolean,
  outputDir?: string,
  opts?: CreateJobOptions,
): Promise<Job> {
  const body: Record<string, unknown> = { url, format_pref: formatPref };
  // Only send the field when the caller passed an explicit value — the
  // server treats `undefined`/missing as "use the configured default".
  if (subtitles !== undefined) body.subtitles = subtitles;
  if (outputDir !== undefined) body.output_dir = outputDir;
  if (opts?.force_overwrite) body.force_overwrite = true;
  const r = await fetch("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    // Duplicate detection returns a structured object: surface a friendly
    // message so the caller can display "Already downloaded to /path" and
    // offer a Force re-download button. Everything else falls through to
    // the existing message-extraction path.
    if (r.status === 409 && detail?.detail?.code === "duplicate") {
      const dupPath = detail.detail.path ?? "";
      throw new Error(
        `Already downloaded${dupPath ? ` to ${dupPath}` : ""}. Use Force re-download to override.`,
      );
    }
    throw new Error(detail.detail?.[0]?.msg ?? detail.detail ?? `createJob: ${r.status}`);
  }
  return r.json();
}

export async function createJobsFromPick(
  urls: string[],
  formatPref?: string,
  subtitles?: boolean,
  outputDir?: string,
  opts?: CreateJobOptions,
): Promise<Job> {
  const body: Record<string, unknown> = { urls, format_pref: formatPref };
  if (subtitles !== undefined) body.subtitles = subtitles;
  if (outputDir !== undefined) body.output_dir = outputDir;
  if (opts?.force_overwrite) body.force_overwrite = true;
  const r = await fetch("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    if (r.status === 409 && detail?.detail?.code === "duplicate") {
      const dupPath = detail.detail.path ?? "";
      throw new Error(
        `Already downloaded${dupPath ? ` to ${dupPath}` : ""}. Use Force re-download to override.`,
      );
    }
    throw new Error(detail.detail?.[0]?.msg ?? detail.detail ?? `createJobsFromPick: ${r.status}`);
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

export async function redownloadJob(id: string): Promise<Job> {
  const r = await fetch(`/jobs/${encodeURIComponent(id)}/redownload`, {
    method: "POST",
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail ?? `redownload: ${r.status}`);
  }
  return r.json();
}

/** Populated by /preview when the entry's video_id is in the library
 * index (from a previous ytdl run or a manual copy under a scan dir).
 * The UI renders a banner + swaps Download to Force re-download when
 * present. Missing/null means "not detected as duplicate". */
export interface DuplicateInfo {
  path: string;
  title: string | null;
}

export interface PreviewEntry {
  url: string;
  id: string | null;
  title: string | null;
  position: number | null;
  already_downloaded?: DuplicateInfo | null;
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

export interface ClearPreview { clearable: number; older_than_days: number; }
export interface ClearResult { deleted: number; }

export async function previewClear(olderThanDays = 7): Promise<ClearPreview> {
  const r = await fetch(`/jobs/clear/preview?older_than_days=${olderThanDays}`);
  if (!r.ok) throw new Error(`clear preview: ${r.status}`);
  return r.json();
}

export async function clearDoneJobs(olderThanDays = 7): Promise<ClearResult> {
  const r = await fetch(`/jobs/clear?older_than_days=${olderThanDays}`, { method: "POST" });
  if (!r.ok) throw new Error(`clear: ${r.status}`);
  return r.json();
}

export interface BinaryStatus {
  present: boolean;
  path: string | null;
}

export interface StatusResponse {
  cookies_browser: string | null;
  cookies_source: "explicit" | "autodetect" | "none";
  // Path to an active cookies.txt (yt-dlp's cookiefile), or null. Independent
  // of cookies_browser — either or both may be set. In Docker this is the
  // usual auth path since no host browser is reachable.
  cookies_file: string | null;
  // Base URL of a configured bgutil PO token provider, or null. Present means
  // yt-dlp is wired to mint Proof-of-Origin tokens through it.
  pot_provider_url: string | null;
  deno: BinaryStatus;
  ffmpeg: BinaryStatus;
  subtitles_default: boolean;
  // Server-side default output directory. Surfaced so the "Save to" override
  // in the submit form can show it as a placeholder when blank.
  output_dir: string;
  // Seconds to wait after a single-video preview resolves before
  // auto-submitting. The UI reads this on mount so the countdown banner uses
  // the configured default. A value of 0 disables the auto-submit flow.
  autosubmit_delay_s: number;
  // Upper bound on a single yt-dlp probe (preview or per-URL enrichment).
  // Surfaced so a future PR can show "Probe timeout: 30s" in the settings
  // panel — typed access only for now.
  probe_timeout_s: number;
  // Directories scanned to build the duplicate-detection index. Surfaced
  // for a future settings pane — the current UI just needs to know the
  // feature is on.
  library_scan_dirs: string[];
  // Duplicate-detection feature flag. When false, /preview never sets
  // already_downloaded and /jobs never returns 409 for a duplicate.
  dedup_enabled: boolean;
}

export async function fetchStatus(): Promise<StatusResponse> {
  const r = await fetch("/status");
  if (!r.ok) throw new Error(`status: ${r.status}`);
  return r.json();
}
