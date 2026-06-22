import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  clearDoneJobs,
  createJob,
  createJobsFromPick,
  enrichUrls,
  fetchStatus,
  listJobs,
  previewClear,
  previewUrl,
  retryJob,
  type EnrichedEntry,
  type Job,
  type PreviewResponse,
  type StatusResponse,
} from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { PreviewVideo } from "./components/PreviewVideo";
import { PreviewPanel } from "./components/PreviewPanel";
import { JobList } from "./components/JobList";
import { useJobsStream } from "./hooks/useJobsStream";

const PREVIEW_DEBOUNCE_MS = 500;

/**
 * Preview lifecycle for the URL the user is currently typing/pasting.
 *   idle    -> input is empty (or never resolved)
 *   loading -> debounce elapsed, /preview is in flight
 *   ready   -> /preview returned successfully; sourceUrl is the URL that
 *              produced the payload (so we can ignore stale enrichment)
 *   error   -> request failed (bad URL shape, 4xx/5xx, or network)
 */
type PreviewState =
  | { kind: "idle" }
  | { kind: "loading"; url: string }
  | { kind: "ready"; preview: PreviewResponse; sourceUrl: string }
  | { kind: "error"; sourceUrl: string; message: string };

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [format, setFormat] = useState("best");
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [singleEnriched, setSingleEnriched] = useState<EnrichedEntry | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [clearable, setClearable] = useState(0);

  const previewDebounce = useRef<number | null>(null);
  const previewAbort = useRef<AbortController | null>(null);

  // Monotonic counter for full /jobs refreshes. Each refresh() captures
  // the next value before its fetch; only the response whose value still
  // matches the latest at resolution gets to write. Closes the race
  // where two refreshes (e.g., snapshot + expanded in quick succession)
  // resolve out of order and the older one clobbers the newer one's
  // discovery of new rows.
  const refreshSeq = useRef(0);

  async function refresh() {
    const seq = refreshSeq.current + 1;
    refreshSeq.current = seq;
    let list: { jobs: Job[]; total: number };
    try {
      list = await listJobs();
    } catch (e) {
      // Roll back our claim on the seq so an earlier successful refresh
      // that's still in flight can still write — its response is the
      // only data the user is going to see.
      if (refreshSeq.current === seq) {
        refreshSeq.current = seq - 1;
      }
      throw e;
    }
    if (refreshSeq.current !== seq) {
      // A newer refresh() was kicked off and is responsible for the
      // post-state. Drop this older response so we don't roll back the
      // newer one's results.
      return;
    }
    setJobs((prev) => {
      // For running rows, preserve the more-advanced progress fields
      // that may have come from live SSE events while /jobs was
      // fetching. The /jobs payload surfaces SQLite values that workers
      // write throttled (1Hz); the SSE bus is unthrottled.
      //
      // bytes_done and filesize_bytes are a coupled pair (the percentage
      // is bytes/total). When we keep one side's bytes_done, we MUST
      // keep the same side's filesize_bytes too, otherwise the
      // percentage misreports.
      const prevById = new Map(prev.map((j) => [j.id, j]));
      return list.jobs.map((row) => {
        if (row.status !== "running") return row;
        const live = prevById.get(row.id);
        if (!live || live.status !== "running") return row;
        const liveDone = live.bytes_done ?? 0;
        const rowDone = row.bytes_done ?? 0;
        if (liveDone > rowDone) {
          return {
            ...row,
            bytes_done: live.bytes_done,
            filesize_bytes: live.filesize_bytes ?? row.filesize_bytes,
            speed_bps: live.speed_bps ?? row.speed_bps,
            eta_s: live.eta_s ?? row.eta_s,
          };
        }
        return row;
      });
    });
  }

  async function refreshAll() {
    await Promise.all([
      refresh(),
      previewClear()
        .then((r) => setClearable(r.clearable))
        .catch(() => setClearable(0)),
    ]);
  }

  // ---- Preview fetch on URL change ----
  // Debounces the URL into a single /preview call. Any in-flight request is
  // aborted when the user keeps typing, so the UI only ever reflects the
  // newest URL the user actually settled on.
  useEffect(() => {
    if (previewDebounce.current !== null) {
      window.clearTimeout(previewDebounce.current);
      previewDebounce.current = null;
    }
    if (previewAbort.current) {
      previewAbort.current.abort();
      previewAbort.current = null;
    }
    setSingleEnriched(null);
    setSubmitError(null);

    const trimmed = url.trim();
    if (!trimmed) {
      setPreview({ kind: "idle" });
      return;
    }
    // Shape check before hitting the backend — avoids 422 noise on partial
    // input ("htt", "youtu") while the user is still typing.
    if (!/^https?:\/\//i.test(trimmed)) {
      setPreview({
        kind: "error",
        sourceUrl: trimmed,
        message: "URL must start with http:// or https://",
      });
      return;
    }

    // Synchronously enter loading state so any previously-rendered preview
    // (and its Download button) is replaced immediately. Without this, a
    // user retyping a URL would see the OLD preview's Download button stay
    // clickable for up to 500ms — clicking it would enqueue the wrong URL.
    setPreview({ kind: "loading", url: trimmed });

    previewDebounce.current = window.setTimeout(() => {
      previewDebounce.current = null;
      const ac = new AbortController();
      previewAbort.current = ac;
      (async () => {
        try {
          const p = await previewUrl(trimmed, { signal: ac.signal });
          if (ac.signal.aborted) return;
          setPreview({ kind: "ready", preview: p, sourceUrl: trimmed });

          // For a single-video preview, fan out to /preview/enrich so the
          // card upgrades with title/thumbnail/duration/uploader. Best
          // effort — failures leave the card on the flat payload.
          if (p.kind === "video" && p.entries.length === 1) {
            enrichUrls([p.entries[0].url])
              .then((res) => {
                if (ac.signal.aborted) return;
                if (res.entries[0]) setSingleEnriched(res.entries[0]);
              })
              .catch(() => {});
          }
        } catch (e) {
          if (ac.signal.aborted) return;
          // AbortError surfaces here in some envs as a DOMException — treat
          // anything aborted as a no-op.
          if (e instanceof DOMException && e.name === "AbortError") return;
          const msg = e instanceof Error ? e.message : "preview failed";
          setPreview({ kind: "error", sourceUrl: trimmed, message: msg });
        }
      })();
    }, PREVIEW_DEBOUNCE_MS);

    return () => {
      if (previewDebounce.current !== null) {
        window.clearTimeout(previewDebounce.current);
        previewDebounce.current = null;
      }
      if (previewAbort.current) {
        previewAbort.current.abort();
      }
    };
  }, [url]);

  // ---- Initial refresh + SSE wiring ----
  useEffect(() => {
    refreshAll().catch(() => {});
  }, []);

  // Fetch cookies status once at mount so the header can show what yt-dlp
  // will read at job time. Best effort — a 4xx/5xx leaves the chip empty.
  useEffect(() => {
    fetchStatus().then(setStatus).catch(() => {});
  }, []);

  const sseState = useJobsStream((event) => {
    if (!event.event) return;

    // High-frequency: progress events patch the matching row in place
    // from event data, no fetch. The bar updates as fast as the bus
    // fires (no longer capped at 5/s by the previous 200ms debounce).
    //
    // Important: the server forwards yt-dlp's raw progress status here
    // (e.g. "downloading", "finished") — NOT the queue's JobStatus
    // values. Don't overwrite job.status from progress events or the
    // row would flip to a non-running status and JobRow would hide the
    // progress UI. Status only ever changes via the refresh() path.
    if (event.event === "progress" && event.job_id) {
      const jobId = event.job_id;
      setJobs((prev) =>
        prev.map((j) =>
          j.id === jobId
            ? {
                ...j,
                bytes_done: event.downloaded_bytes ?? j.bytes_done,
                filesize_bytes: event.total_bytes ?? j.filesize_bytes,
                speed_bps: event.speed ?? j.speed_bps,
                eta_s: event.eta ?? j.eta_s,
              }
            : j,
        ),
      );
      return;
    }

    // Everything non-progress (snapshot, expanded, started, finished,
    // failed, canceled): a single refresh() is the canonical path. The
    // previous approach used getJob() per lifecycle event for "minimal"
    // fetches, but the resulting race surface (multiple fetches in
    // flight, out-of-order resolution against refresh()) wasn't worth
    // it — lifecycle events are low frequency and progress is where
    // the real perf win lives.
    refresh().catch(() => {});
  });

  // ---- Submit handlers ----
  async function submitSingle(entryUrl: string) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await createJob(entryUrl, format);
      setUrl("");
      setPreview({ kind: "idle" });
      setSingleEnriched(null);
      await refreshAll();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitPickedUrls(urls: string[]) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await createJobsFromPick(urls, format);
      setUrl("");
      setPreview({ kind: "idle" });
      await refreshAll();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  // ---- Render ----
  const ready = preview.kind === "ready" ? preview.preview : null;
  const singleEntry =
    ready && ready.kind === "video" && ready.entries.length === 1
      ? ready.entries[0]
      : null;
  const playlistEntries =
    ready && ready.kind === "playlist" && ready.entries.length > 0
      ? ready.entries
      : null;

  return (
    <div className="min-h-screen p-6 max-w-4xl mx-auto flex flex-col gap-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">ytdl</h1>
          <p className="text-sm text-neutral-400">Self-hosted yt-dlp queue</p>
        </div>
        <div className="text-xs text-neutral-500 flex items-center gap-2 flex-wrap justify-end">
          {status && (
            <>
              <span
                title={
                  status.cookies_source === "autodetect"
                    ? "browser auto-detected at startup"
                    : status.cookies_source === "explicit"
                      ? "from YTDL_COOKIES_BROWSER / config.toml"
                      : "no browser cookie store found"
                }
              >
                {status.cookies_browser
                  ? `cookies: ${status.cookies_browser}${
                      status.cookies_source === "autodetect" ? " (auto)" : ""
                    }`
                  : "cookies: none"}
              </span>
              <span
                className={status.deno.present ? "" : "text-amber-400"}
                title={
                  status.deno.present
                    ? `deno on PATH at ${status.deno.path}`
                    : "deno not found — install for YouTube n-challenge support"
                }
              >
                deno: {status.deno.present ? "✓" : "missing"}
              </span>
              <span
                className={status.ffmpeg.present ? "" : "text-red-400"}
                title={
                  status.ffmpeg.present
                    ? `ffmpeg on PATH at ${status.ffmpeg.path}`
                    : "ffmpeg not found — separate audio+video streams can't be merged"
                }
              >
                ffmpeg: {status.ffmpeg.present ? "✓" : "missing"}
              </span>
            </>
          )}
          <span>{sseState}</span>
        </div>
      </header>

      <SubmitForm
        url={url}
        onUrlChange={setUrl}
        format={format}
        onFormatChange={setFormat}
      />

      {preview.kind === "loading" && (
        <p className="text-xs text-neutral-500">Fetching preview…</p>
      )}

      {preview.kind === "error" && (
        <p className="text-xs text-red-400">
          Could not preview: {preview.message}
        </p>
      )}

      {singleEntry && (
        <PreviewVideo
          entry={singleEntry}
          enriched={singleEnriched ?? undefined}
          format={format}
          onDownload={() => submitSingle(singleEntry.url)}
          busy={submitting}
        />
      )}

      {playlistEntries && ready && (
        <PreviewPanel
          title={ready.title}
          entries={playlistEntries}
          onConfirm={(urls) => submitPickedUrls(urls)}
          onCancel={() => {
            setUrl("");
            setPreview({ kind: "idle" });
          }}
        />
      )}

      {submitError && <p className="text-xs text-red-400">{submitError}</p>}

      {clearable > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            className="text-xs text-neutral-400 hover:text-neutral-200 border border-neutral-800 rounded px-2 py-1"
            onClick={async () => {
              if (!window.confirm(`Delete ${clearable} done jobs older than 7 days?`)) return;
              await clearDoneJobs();
              await refreshAll();
            }}
          >
            Clear {clearable} done job{clearable > 1 ? "s" : ""}
          </button>
        </div>
      )}

      <JobList
        jobs={jobs}
        onCancel={async (id) => {
          await cancelJob(id);
          await refreshAll();
        }}
        onRetry={async (id) => {
          await retryJob(id);
          await refreshAll();
        }}
      />
    </div>
  );
}
