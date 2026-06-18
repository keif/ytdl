import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createJob,
  createJobsFromPick,
  enrichUrls,
  listJobs,
  previewUrl,
  type EnrichedEntry,
  type Job,
  type PreviewResponse,
} from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { PreviewVideo } from "./components/PreviewVideo";
import { PreviewPanel } from "./components/PreviewPanel";
import { JobList } from "./components/JobList";
import { useJobsStream } from "./hooks/useJobsStream";

const REFRESH_DEBOUNCE_MS = 200;
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

  const refreshDebounce = useRef<number | null>(null);
  const previewDebounce = useRef<number | null>(null);
  const previewAbort = useRef<AbortController | null>(null);

  async function refresh() {
    const list = await listJobs();
    setJobs(list.jobs);
  }

  function scheduleRefresh() {
    if (refreshDebounce.current !== null) {
      window.clearTimeout(refreshDebounce.current);
    }
    refreshDebounce.current = window.setTimeout(() => {
      refreshDebounce.current = null;
      refresh().catch(() => {});
    }, REFRESH_DEBOUNCE_MS);
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
    refresh().catch(() => {});
    return () => {
      if (refreshDebounce.current !== null) {
        window.clearTimeout(refreshDebounce.current);
      }
    };
  }, []);

  const sseState = useJobsStream(() => {
    // Trailing-edge debounce: bursts of events (e.g. playlist expansion)
    // result in at most one refresh per REFRESH_DEBOUNCE_MS.
    scheduleRefresh();
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
      await refresh();
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
      await refresh();
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
        <span className="text-xs text-neutral-500">{sseState}</span>
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

      <JobList
        jobs={jobs}
        onCancel={async (id) => {
          await cancelJob(id);
          await refresh();
        }}
      />
    </div>
  );
}
