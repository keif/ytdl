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
  redownloadJob,
  retryJob,
  type EnrichedEntry,
  type Job,
  type PreviewResponse,
  type StatusResponse,
} from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { PreviewVideo } from "./components/PreviewVideo";
import { PreviewPanel } from "./components/PreviewPanel";
import { AutoSubmitBanner } from "./components/AutoSubmitBanner";
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
  // Tri-state at the server (None/true/false), but a checkbox can only be
  // on/off. We mirror the server default from /status into local state at
  // mount so the UI matches the user's config without re-reading it on
  // every submit.
  const [subtitles, setSubtitles] = useState(false);
  const subtitlesUserOverride = useRef(false);
  // Per-paste intent — unlike `subtitles` (which mirrors a persistent server
  // default), audio-only resets every time the URL clears. Most paste-it sessions
  // want video; the user opts into audio explicitly for the current URL.
  const [audioOnly, setAudioOnly] = useState(false);
  // Per-paste destination override. Same per-paste contract as audio-only:
  // resets on any non-typing URL change. Empty string means "use the server
  // default" — the api layer omits the field from the POST body when it's
  // blank so the server's resolution path stays identical to a request from
  // a client that doesn't know about the override.
  const [outputDir, setOutputDir] = useState("");
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [singleEnriched, setSingleEnriched] = useState<EnrichedEntry | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [clearable, setClearable] = useState(0);
  // Paste-and-go countdown. `null` = not counting (cancelled, completed, or
  // not applicable for the current preview). `{remaining: N}` = N seconds
  // left before the job auto-submits. The interval handle lives in a ref so
  // any code path (URL edit, Cancel click, playlist transition, unmount) can
  // clear it without going through state.
  const [autoSubmit, setAutoSubmit] = useState<{ remaining: number } | null>(
    null,
  );
  const autoSubmitInterval = useRef<number | null>(null);
  // Tracks the URL whose auto-submit was already attempted (fired, manually
  // submitted, or explicitly cancelled) so a re-render of the effect doesn't
  // restart the countdown for the same preview. Cleared when the URL changes
  // to something different (the natural "user moved on" signal).
  const autoSubmitAttemptedFor = useRef<string | null>(null);
  // Mirror the latest url state so submitSingle's catch can check the
  // CURRENT value (post-clear, post-user-typing) without going through
  // a state updater. Updated via useEffect below so reads from inside
  // event handlers see the freshest value at catch time.
  const urlRef = useRef("");
  // The countdown effect captures submitSingle in its closure at scheduling
  // time. If the user toggles audio-only/subtitles/output-dir during the
  // window, we want the LATEST submit handler, not a stale one. The ref is
  // refreshed on every render below so the timer's tick handler dereferences
  // the freshest function.
  const submitSingleRef = useRef<
    (entryUrl: string, opts?: { force_overwrite?: boolean }) => Promise<void>
  >(async () => {});

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
      //
      // Known limit: with 3+ overlapping refreshes where two NEWER ones
      // fail out of order, the rollback can land on a seq that itself
      // failed, suppressing an even-earlier success. We accept this
      // because (a) it requires three concurrent refreshes, which
      // single-user homelab use almost never produces, and (b) the
      // user-visible failure mode is "UI stays on the pre-refresh
      // listing until the next SSE event triggers another refresh" —
      // not data loss. The next event arrives within seconds in any
      // realistic flow.
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

  /**
   * Tear down any in-progress auto-submit countdown.
   *
   * Safe to call when no countdown is running — the interval ref guards the
   * clear. Used from: the Cancel button, URL-edit reset path, playlist
   * picker mount (defensive — if a single-video countdown happened to be
   * mid-tick when /preview re-resolved as a playlist), and the App unmount
   * cleanup.
   */
  function cancelAutoSubmit() {
    if (autoSubmitInterval.current !== null) {
      window.clearInterval(autoSubmitInterval.current);
      autoSubmitInterval.current = null;
    }
    setAutoSubmit(null);
    // Mark the current preview URL as "already attempted" so the effect
    // doesn't restart the countdown when its deps change (e.g. submitting
    // flips back to false after a failed submit, /status re-resolves).
    // Cleared when the user moves to a different URL.
    if (singleEntry) autoSubmitAttemptedFor.current = singleEntry.url;
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
    // Don't clear submitError here. The eager-submit failure restore path
    // brings the URL back AFTER setSubmitError is set; that retriggers
    // this effect, which would wipe the error and leave the user
    // confused. submitError gets cleared at submit start (next attempt)
    // or replaced by a new failure.

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

  // Keep urlRef in sync with the url state so submitSingle's catch can
  // check the latest value (post-clear, post-user-typing) without going
  // through a state updater. The useEffect is a safety net; everywhere
  // we explicitly call setUrl we also go through updateUrl below, which
  // syncs the ref inline. The effect handles state updates we don't
  // catch (functional updates, etc.).
  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  // Helper: setUrl + synchronous urlRef sync. Use this instead of setUrl
  // directly so every URL change is observable by submitSingle's catch
  // without waiting for the [url] effect to commit. This matters when
  // /jobs rejects synchronously, or when the user types while a POST
  // is in flight — the catch needs the freshest value to decide whether
  // to restore the failed URL or leave the user's new paste alone.
  function updateUrl(next: string) {
    setUrl(next);
    urlRef.current = next;
  }

  // ---- Initial refresh + SSE wiring ----
  useEffect(() => {
    refreshAll().catch(() => {});
  }, []);

  // Fetch cookies status once at mount so the header can show what yt-dlp
  // will read at job time. Best effort — a 4xx/5xx leaves the chip empty.
  useEffect(() => {
    fetchStatus()
      .then((s) => {
        setStatus(s);
        // Seed the subtitles checkbox from the user's config the first
        // time /status resolves. Don't clobber a value the user has
        // explicitly toggled while the request was in flight.
        if (!subtitlesUserOverride.current) {
          setSubtitles(s.subtitles_default ?? false);
        }
      })
      .catch(() => {});
  }, []);

  function handleSubtitlesChange(value: boolean) {
    subtitlesUserOverride.current = true;
    setSubtitles(value);
  }

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
  // When /status hasn't resolved yet AND the user hasn't toggled the checkbox,
  // the local `subtitles=false` is a placeholder rather than an intentional
  // opt-out. Send `undefined` in that window so the server's
  // `subtitles_default` config can apply; once status loads (seeding the
  // checkbox) or the user toggles it, the value becomes meaningful.
  function effectiveSubtitles(): boolean | undefined {
    if (subtitlesUserOverride.current) return subtitles;
    if (status !== null) return subtitles;
    return undefined;
  }

  async function submitSingle(
    entryUrl: string,
    opts?: { force_overwrite?: boolean },
  ) {
    // Stop any in-flight auto-submit countdown the moment a manual
    // submit begins. Without this, a Download click near the end of
    // the window can race the timer tick: both call submitSingle and
    // the same URL gets enqueued twice. Idempotent — clearing an
    // already-cleared interval is a no-op.
    cancelAutoSubmit();
    // Always mark this URL as "manually attempted" so the auto-submit
    // effect won't fire a countdown for it if the URL ends up restored
    // (failure path) and /preview later resolves. cancelAutoSubmit()
    // only sets this when singleEntry exists, but eager submit can fire
    // BEFORE /preview returns, so without this line a failed manual
    // Queue would silently retry 5s later when preview finally arrived.
    autoSubmitAttemptedFor.current = entryUrl;
    setSubmitting(true);
    setSubmitError(null);

    // Capture the values the POST needs BEFORE clearing per-paste state.
    // We clear the input synchronously (below) so the user can immediately
    // paste the next URL — the POST runs in the background. createJob
    // resolves later; we update the queue listing then.
    const effectiveFormat = audioOnly ? "audio_only" : format;
    const effectiveOutputDir = outputDir.trim() || undefined;
    const effectiveSubs = effectiveSubtitles();
    // Snapshot per-paste state for the failure-restore path. If the POST
    // rejects and the user hasn't moved on, we restore ALL of these
    // (URL, audio-only, output-dir) so the form returns to its pre-
    // submit shape. Without this, retrying an output_dir-rejected job
    // would silently use the default directory.
    const snapshotAudioOnly = audioOnly;
    const snapshotOutputDir = outputDir;

    // Synchronous clear: lets the user paste the next URL while the POST
    // is still in flight. The preview useEffect sees url="" and resets
    // preview to idle (aborting any pending /preview), which is exactly
    // what we want for the rapid-queue flow.
    // Cancel any pending preview work BEFORE clearing state. Relying on
    // the [url] useEffect cleanup to do this is too late — its cleanup
    // runs only when the next effect mounts, by which point a
    // mid-flight setTimeout or fetch resolution could have already
    // rendered a "ready" preview card for the URL we just queued.
    if (previewDebounce.current !== null) {
      window.clearTimeout(previewDebounce.current);
      previewDebounce.current = null;
    }
    if (previewAbort.current) {
      previewAbort.current.abort();
      previewAbort.current = null;
    }
    updateUrl("");
    setAudioOnly(false);
    setOutputDir("");
    setPreview({ kind: "idle" });
    setSingleEnriched(null);

    // Only the POST itself drives the restore-on-failure path. A
    // refreshAll() failure is a UI-listing hiccup, NOT a submit failure —
    // the job was already accepted by the server. Restoring the URL in
    // that case would let the user retry from the form and double-enqueue.
    let postFailed = false;
    try {
      await createJob(
        entryUrl,
        effectiveFormat,
        effectiveSubs,
        effectiveOutputDir,
        opts?.force_overwrite ? { force_overwrite: true } : undefined,
      );
    } catch (e) {
      postFailed = true;
      // Only show the error AND restore the form if the user is still
      // looking at the same URL they submitted (no new paste since clear).
      // If they've moved on, the failed URL's error is misleading under
      // their newer input — swallow it silently. They can still inspect
      // the queue row when SSE catches up.
      if (urlRef.current === "") {
        setSubmitError(e instanceof Error ? e.message : "submit failed");
        updateUrl(entryUrl);
        setAudioOnly(snapshotAudioOnly);
        setOutputDir(snapshotOutputDir);
      }
    }
    if (!postFailed) {
      try {
        await refreshAll();
      } catch {
        // Ignored — SSE will pick up the new job within a tick or two.
      }
    }
    setSubmitting(false);
  }
  // Keep the ref pointed at the latest closure so the auto-submit timer
  // always uses the user's most-recent audio_only/subtitles/output_dir
  // selections, even if they were toggled mid-countdown.
  submitSingleRef.current = submitSingle;

  async function submitPickedUrls(urls: string[]) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const effectiveFormat = audioOnly ? "audio_only" : format;
      const effectiveOutputDir = outputDir.trim() || undefined;
      // If any selected URL is flagged as already-downloaded in the current
      // preview, the user has explicitly opted in to re-fetching it (they
      // toggled "Include already-downloaded" or manually re-checked the
      // box). Pass force_overwrite so the server's 409 check doesn't
      // reject the batch — otherwise a single duplicate would fail the
      // whole submit.
      const selectedSet = new Set(urls);
      const anyDuplicate = ready?.entries.some(
        (e) => selectedSet.has(e.url) && e.already_downloaded,
      );
      await createJobsFromPick(
        urls,
        effectiveFormat,
        effectiveSubtitles(),
        effectiveOutputDir,
        anyDuplicate ? { force_overwrite: true } : undefined,
      );
      updateUrl("");
      setAudioOnly(false);
      setOutputDir("");
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

  // ---- Auto-submit countdown ----
  // When a single-video preview resolves and the server-configured delay is
  // positive, start a 1Hz countdown. On zero, submitSingle() fires through
  // the same code path the manual Download button uses (audio_only,
  // subtitles, output_dir all apply). The interval lives in a ref so any
  // other code path can cancel it. We deliberately use setInterval over
  // setTimeout-with-Date-arithmetic: the banner reads `remaining` directly
  // from state, so the tick IS the display update.
  useEffect(() => {
    // Defensive: tear down any prior interval before deciding whether to
    // start a fresh one. Effect re-runs cover URL edits inside the same
    // mount; the dep array tells React when.
    if (autoSubmitInterval.current !== null) {
      window.clearInterval(autoSubmitInterval.current);
      autoSubmitInterval.current = null;
    }
    const delay = status?.autosubmit_delay_s ?? 0;
    // Don't start a countdown while a submit is in flight. Common shape:
    // user clicks Download, the manual POST is mid-flight, then /status
    // arrives and re-fires this effect — without the guard, a new timer
    // would race the manual submit and enqueue the same URL twice.
    //
    // Duplicate suppression: if the entry is already in the library, the
    // tool must never silently re-fetch it. The user has to click
    // Force re-download explicitly.
    if (
      !singleEntry ||
      delay <= 0 ||
      submitting ||
      singleEntry.already_downloaded
    ) {
      if (autoSubmit !== null) setAutoSubmit(null);
      return;
    }
    // Don't re-arm for a URL the user already cancelled OR a URL whose
    // submit already fired (success or failure). Either case re-enters
    // this effect when `submitting` flips back to false; without this
    // guard, a failed submit would loop POSTs every `delay` seconds and
    // a cancelled banner would come back unbidden.
    //
    // Match against BOTH the resolved preview URL AND the input URL.
    // yt-dlp's probe canonicalizes some URLs (`youtu.be/X` becomes
    // `youtube.com/watch?v=X`), so a lock keyed on the raw paste would
    // miss the canonical form. Comparing against urlRef.current covers
    // the failure-restore case where the user is still looking at the
    // input they manually attempted, regardless of canonicalization.
    // Reading via the ref (not the url state directly) avoids needing
    // `url` in the deps array — it's updated synchronously by
    // updateUrl(), so it's always current when this effect runs.
    if (
      autoSubmitAttemptedFor.current === singleEntry.url ||
      autoSubmitAttemptedFor.current === urlRef.current
    ) {
      return;
    }
    // Capture the URL at countdown-start so a late tick (interleaved with
    // a URL edit that hadn't yet been observed by the effect) can't
    // submit a stale value.
    const entryUrl = singleEntry.url;
    setAutoSubmit({ remaining: delay });
    // Track remaining outside React state so the interval callback is
    // pure relative to the state updater. With the submit side effect
    // inside a setAutoSubmit() updater, React.StrictMode's double-
    // invocation in dev would fire the POST twice. Keep the updater
    // pure (return-only) and drive the side effect from the interval
    // callback itself.
    let ticksRemaining = delay;
    autoSubmitInterval.current = window.setInterval(() => {
      ticksRemaining -= 1;
      if (ticksRemaining <= 0) {
        if (autoSubmitInterval.current !== null) {
          window.clearInterval(autoSubmitInterval.current);
          autoSubmitInterval.current = null;
        }
        setAutoSubmit(null);
        // Mark this URL as attempted BEFORE the submit fires. If the
        // submit fails, the effect re-runs when `submitting` flips back
        // to false; without this lock it would restart the countdown
        // and loop POSTs every delay seconds.
        autoSubmitAttemptedFor.current = entryUrl;
        // submitSingle() handles its own try/catch and clears the URL
        // on success; failures surface via submitError. Going through
        // the ref guarantees we use the freshest closure (with the
        // user's latest audio_only/subtitles/output_dir values).
        submitSingleRef.current(entryUrl).catch(() => {});
        return;
      }
      setAutoSubmit({ remaining: ticksRemaining });
    }, 1000);

    return () => {
      if (autoSubmitInterval.current !== null) {
        window.clearInterval(autoSubmitInterval.current);
        autoSubmitInterval.current = null;
      }
    };
    // We intentionally key off the entry URL (not the entry object) so the
    // effect only restarts when the user actually moves to a different
    // video. `status?.autosubmit_delay_s` covers the case where /status
    // resolves after the preview already did. `submitting` ensures we
    // tear down (or skip starting) a countdown while a manual submit is
    // in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [singleEntry?.url, status?.autosubmit_delay_s, submitting]);

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
              {(() => {
                // Cookies can come from a browser store, a cookies.txt file,
                // or both — they're independent sources. Show whichever are
                // active so the chip doesn't read "none" when a file is set
                // (the common Docker case, where no browser is reachable).
                const parts: string[] = [];
                if (status.cookies_browser) {
                  parts.push(
                    `${status.cookies_browser}${
                      status.cookies_source === "autodetect" ? " (auto)" : ""
                    }`,
                  );
                }
                if (status.cookies_file) parts.push("file");
                const active = parts.length > 0;
                const title = status.cookies_file
                  ? `cookies.txt: ${status.cookies_file}`
                  : status.cookies_source === "autodetect"
                    ? "browser auto-detected at startup"
                    : status.cookies_source === "explicit"
                      ? "from YTDL_COOKIES_BROWSER / config.toml"
                      : "no cookies configured — auth-gated content may fail";
                return (
                  <span
                    className={active ? "" : "text-amber-400"}
                    title={title}
                  >
                    {active ? `cookies: ${parts.join(" + ")}` : "cookies: none"}
                  </span>
                );
              })()}
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
              {status.pot_provider_url && (
                <span title={`PO token provider: ${status.pot_provider_url}`}>
                  pot: ✓
                </span>
              )}
            </>
          )}
          <span>{sseState}</span>
        </div>
      </header>

      <SubmitForm
        url={url}
        onUrlChange={(value) => {
          // Per-paste fields (audio-only, output-dir) reset on any
          // non-typing change — clear, replace, backspace, or
          // paste-extend.
          //
          // A "typing" change adds exactly one character to the end of
          // the existing text (`value.startsWith(url) && value.length
          // === url.length + 1`). Anything else (multi-char insertion
          // from paste/autofill, select-all-paste of a longer URL that
          // happens to share a prefix, backspace, full replace) means
          // the user is moving to a different URL and the per-paste
          // intent shouldn't silently carry over.
          // Treat the empty-to-anything transition as a fresh start
          // (the user set the override BEFORE pasting). Otherwise,
          // only a single-char append counts as typing.
          const isFreshPaste = url === "" && value !== "";
          const isTypingExtension =
            value.startsWith(url) && value.length === url.length + 1;
          // Audio-only and output-dir are preserved across typing-shaped
          // edits (the user is still composing the same URL). They reset
          // only on non-typing transitions (replace, backspace, multi-
          // char extension, clear).
          if (!isFreshPaste && !isTypingExtension) {
            setAudioOnly(false);
            setOutputDir("");
          }
          // Clear any stale submit error when the user edits the URL.
          // A failed submit's message belongs to the failed URL only —
          // if the user is moving on, the old error is misleading. We
          // clear it here (in the user-typing path) rather than in the
          // [url] useEffect so the failure-restore path doesn't wipe
          // its own error message.
          if (value !== url) setSubmitError(null);
          // The auto-submit countdown is ALWAYS invalidated by any URL
          // change. The timer captured the previous URL at scheduling
          // time; a tick after the input has changed would submit the
          // stale value. A fresh countdown (if any) will start when the
          // new preview resolves.
          if (value !== url) {
            cancelAutoSubmit();
            // Moving to a different URL clears the "already-attempted"
            // lock so the new preview gets a fresh countdown. Note this
            // must happen AFTER cancelAutoSubmit() — that helper sets
            // the lock to the previous URL, which we then drop here.
            autoSubmitAttemptedFor.current = null;
          }
          updateUrl(value);
        }}
        format={format}
        onFormatChange={setFormat}
        subtitles={subtitles}
        onSubtitlesChange={handleSubtitlesChange}
        audioOnly={audioOnly}
        onAudioOnlyChange={setAudioOnly}
        outputDir={outputDir}
        onOutputDirChange={setOutputDir}
        outputDirPlaceholder={status?.output_dir ?? ""}
        submitting={submitting}
        onQueue={() => {
          // Eager submit: fire the same code path as the preview card's
          // Download button, but without waiting on /preview. submitSingle
          // already calls cancelAutoSubmit() at the top, so a click during
          // an active countdown can't double-fire.
          const trimmed = url.trim();
          if (!trimmed) return;
          submitSingle(trimmed).catch(() => {});
        }}
      />

      {preview.kind === "loading" && (
        <p className="text-xs text-neutral-500">Fetching preview…</p>
      )}

      {preview.kind === "error" && (
        <p className="text-xs text-red-400">
          Could not preview: {preview.message}
        </p>
      )}

      {autoSubmit !== null && (
        <AutoSubmitBanner
          remaining={autoSubmit.remaining}
          onCancel={cancelAutoSubmit}
        />
      )}

      {singleEntry && (
        <PreviewVideo
          entry={singleEntry}
          enriched={singleEnriched ?? undefined}
          format={audioOnly ? "audio_only" : format}
          onDownload={() =>
            submitSingle(
              singleEntry.url,
              singleEntry.already_downloaded
                ? { force_overwrite: true }
                : undefined,
            )
          }
          busy={submitting}
        />
      )}

      {playlistEntries && ready && (
        <PreviewPanel
          title={ready.title}
          entries={playlistEntries}
          onConfirm={(urls) => submitPickedUrls(urls)}
          onCancel={() => {
            // The user explicitly dismissed the form. Stale errors from
            // a previous failed submit (single or picker) shouldn't linger
            // under an empty form. Same intent as the user-typing path's
            // setSubmitError(null) clear — the [url] effect no longer
            // handles this so we do it explicitly here.
            setSubmitError(null);
            updateUrl("");
            setAudioOnly(false);
            setOutputDir("");
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
        onRedownload={async (id) => {
          await redownloadJob(id);
          await refreshAll();
        }}
      />
    </div>
  );
}
