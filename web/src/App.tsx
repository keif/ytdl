import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  clearDoneJobs,
  createJob,
  createJobsFromPick,
  enrichUrls,
  fetchStatus,
  getJob,
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
// Lifecycle events that map cleanly to a single row update — fetch the
// affected row and patch in state. `expanded` is intentionally not in
// this set because the new children created during playlist expansion
// are not separately announced on the bus; a full refresh is the only
// way to learn about them.
const LIFECYCLE_EVENTS = new Set([
  "started",
  "finished",
  "failed",
  "canceled",
]);

// Events that require a full /jobs refresh because we can't reconstruct
// the new state granularly:
//   - snapshot: server sent on connect/reconnect. We may have missed
//     transitions while disconnected; the snapshot itself lists
//     non-terminal jobs but doesn't carry full row data, so we re-sync.
//   - expanded: a playlist parent just enqueued N new child rows. The
//     child enqueues are not published to the bus, so a full refresh
//     is the only way to see them.
const FULL_REFRESH_EVENTS = new Set(["snapshot", "expanded"]);

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

  // Per-row generation counter for issued lifecycle fetches. Each event
  // bumps it; only the response whose value still matches at resolution
  // gets to write. Closes the race where a slower fetch lands after a
  // newer one for the same row.
  const lifecycleGen = useRef<Map<string, number>>(new Map());

  // Per-row counter for lifecycle responses that have ACTUALLY committed
  // to state. The refresh merge consults this — not lifecycleGen — so a
  // refresh only protects rows whose lifecycle WRITE finished during the
  // refresh, not rows whose lifecycle fetch is merely in flight.
  const lifecycleWritten = useRef<Map<string, number>>(new Map());

  // Snapshot of committed lifecycle write-counts taken when refresh()
  // starts; used to detect which rows had a lifecycle write COMMIT during
  // the refresh. Those rows are preserved from current state instead of
  // being replaced by the (older) /jobs response.
  function snapshotLifecycleWrites(): Map<string, number> {
    return new Map(lifecycleWritten.current);
  }

  // Monotonic counter for full /jobs refreshes. Bumped only when a
  // refresh SUCCESSFULLY commits to state. Closes two related races:
  //   - Two refreshes resolve out of order: the older one sees a higher
  //     committed seq and drops its response.
  //   - A lifecycle fetch checks this at issue and at resolve; if a
  //     successful refresh happened in between, the lifecycle response
  //     is dropped (refresh data is newer for that row).
  // Crucially, refreshes that FAIL don't bump this — so a lifecycle
  // fetch is never dropped on behalf of a refresh that never wrote
  // anything.
  const refreshSeq = useRef(0);
  // Pending refresh sequence: how many refreshes have STARTED. The
  // refresh function uses this to detect that a newer refresh is in
  // flight and drop its own (older) write.
  const refreshIssuedSeq = useRef(0);

  async function refresh() {
    const issued = refreshIssuedSeq.current + 1;
    refreshIssuedSeq.current = issued;
    const before = snapshotLifecycleWrites();
    const list = await listJobs();
    if (refreshIssuedSeq.current !== issued) {
      // A newer refresh() was kicked off and is responsible for the
      // post-state. Drop this older response so we don't roll back the
      // newer one's results.
      return;
    }
    setJobs((prev) => {
      const prevById = new Map(prev.map((j) => [j.id, j]));
      const refreshIds = new Set(list.jobs.map((j) => j.id));

      // 1. Walk the refresh payload, preferring in-state rows whose
      //    lifecycle write COMMITTED during the refresh (i.e., the
      //    in-state row is newer than what /jobs gave us).
      const merged = list.jobs.map((row) => {
        const beforeWrites = before.get(row.id) ?? 0;
        const nowWrites = lifecycleWritten.current.get(row.id) ?? 0;
        if (nowWrites > beforeWrites) {
          const live = prevById.get(row.id);
          if (live) return live;
        }
        return row;
      });

      // 2. Carry over any in-state rows that the refresh response is
      //    MISSING — but only if their lifecycle generation incremented
      //    during the refresh. Otherwise the row was legitimately
      //    deleted server-side (e.g., queue clear) and shouldn't be
      //    resurrected. Inserted at the top to match the lifecycle
      //    handler's prepend behavior.
      for (const live of prev) {
        if (refreshIds.has(live.id)) continue;
        const beforeWrites = before.get(live.id) ?? 0;
        const nowWrites = lifecycleWritten.current.get(live.id) ?? 0;
        if (nowWrites > beforeWrites) merged.unshift(live);
      }

      return merged;
    });
    // Refresh committed successfully — bump refreshSeq so any in-flight
    // lifecycle fetch that captured a lower seq at issue knows it's
    // stale.
    refreshSeq.current += 1;
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
    // progress UI. Status only ever changes via lifecycle events.
    if (event.event === "progress" && event.job_id) {
      const jobId = event.job_id;
      setJobs((prev) =>
        prev.map((j) =>
          j.id === jobId
            ? {
                ...j,
                // Only overwrite when the event carries a non-null value;
                // a null field means "downloader didn't report this one
                // this tick" — keep the prior value.
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

    // Snapshot (initial connect / reconnect) and expanded (playlist
    // children just appeared, no per-child bus events): the granular
    // path can't reconstruct the new state, so fall back to a full
    // refresh.
    if (FULL_REFRESH_EVENTS.has(event.event)) {
      refresh().catch(() => {});
      return;
    }

    // Lifecycle: fetch the single row and merge into state. Insert if
    // it's not in state yet (e.g., a freshly-enqueued job that wasn't
    // in the initial /jobs listing).
    //
    // Guard against stale responses overwriting newer state by tracking
    // a per-row generation: bump before the fetch, only apply the
    // response if the generation still matches when the promise
    // resolves.
    if (LIFECYCLE_EVENTS.has(event.event) && event.job_id) {
      const jobId = event.job_id;
      const gen = (lifecycleGen.current.get(jobId) ?? 0) + 1;
      lifecycleGen.current.set(jobId, gen);
      // Snapshot the refresh sequence at fetch issue. If a refresh
      // completes (resolves successfully) between now and when this
      // lifecycle fetch resolves, our row data is stale — drop it.
      const refreshSeqAtIssue = refreshSeq.current;
      getJob(jobId)
        .then((updated) => {
          if (lifecycleGen.current.get(jobId) !== gen) {
            // A newer lifecycle event has already fired (and may have
            // already landed). Drop this response — it's stale.
            return;
          }
          if (refreshSeq.current !== refreshSeqAtIssue) {
            // A refresh started AFTER our fetch began and may have
            // already written newer state for this row. Drop our
            // response to avoid clobbering it.
            return;
          }
          // Record that a lifecycle write committed for this row. The
          // refresh merge consults this to decide whether to keep the
          // in-state row over its own /jobs payload.
          const writes = lifecycleWritten.current.get(jobId) ?? 0;
          lifecycleWritten.current.set(jobId, writes + 1);
          setJobs((prev) => {
            const idx = prev.findIndex((j) => j.id === updated.id);
            if (idx === -1) return [updated, ...prev];
            const copy = [...prev];
            // Preserve in-flight progress fields when applying a
            // lifecycle row that's still in a non-terminal status. The
            // /jobs/{id} response read from the DB can be behind the
            // live event stream because workers write progress
            // throttled (1Hz) to SQLite — the SSE bus is unthrottled.
            // For terminal states we trust the lifecycle row entirely
            // (it'll have final bytes_done/filesize_bytes).
            const live = copy[idx];
            const lifecycleIsRunning = updated.status === "running";
            if (lifecycleIsRunning) {
              copy[idx] = {
                ...updated,
                bytes_done:
                  (live.bytes_done ?? 0) > (updated.bytes_done ?? 0)
                    ? live.bytes_done
                    : updated.bytes_done,
                filesize_bytes: live.filesize_bytes ?? updated.filesize_bytes,
                speed_bps: live.speed_bps ?? updated.speed_bps,
                eta_s: live.eta_s ?? updated.eta_s,
              };
            } else {
              copy[idx] = updated;
            }
            return copy;
          });
        })
        .catch(() => {
          // Single-job fetch failed (race with delete, network blip).
          // Fall back to the full refresh as a safety net.
          refresh().catch(() => {});
        });
    }
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
