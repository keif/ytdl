import { useEffect, useRef, useState } from "react";
import {
  cancelJob,
  createJob,
  createJobsFromPick,
  listJobs,
  previewUrl,
  type Job,
  type PreviewResponse,
} from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { JobList } from "./components/JobList";
import { PreviewPanel } from "./components/PreviewPanel";
import { useJobsStream } from "./hooks/useJobsStream";

const REFRESH_DEBOUNCE_MS = 200;

interface PendingPick {
  format: string;
  preview: PreviewResponse;
}

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [pending, setPending] = useState<PendingPick | null>(null);
  const debounceTimer = useRef<number | null>(null);

  async function refresh() {
    const list = await listJobs();
    setJobs(list.jobs);
  }

  function scheduleRefresh() {
    if (debounceTimer.current !== null) {
      window.clearTimeout(debounceTimer.current);
    }
    debounceTimer.current = window.setTimeout(() => {
      debounceTimer.current = null;
      refresh().catch(() => {});
    }, REFRESH_DEBOUNCE_MS);
  }

  useEffect(() => {
    refresh().catch(() => {});
    return () => {
      if (debounceTimer.current !== null) {
        window.clearTimeout(debounceTimer.current);
      }
    };
  }, []);

  const sseState = useJobsStream(() => {
    // Trailing-edge debounce: bursts of events (e.g. playlist expansion)
    // result in at most one refresh per REFRESH_DEBOUNCE_MS instead of
    // one per event.
    scheduleRefresh();
  });

  /**
   * Two-step submit:
   *   1. POST /preview to discover whether this is a single video or a
   *      playlist.
   *   2. Single video -> enqueue immediately, same as before.
   *      Playlist     -> hand off to PreviewPanel so the user can pick a
   *      subset before anything hits the queue.
   */
  async function handleSubmit(url: string, format: string): Promise<void> {
    const preview = await previewUrl(url);
    if (preview.kind === "video" || preview.entries.length <= 1) {
      await createJob(url, format);
      await refresh();
      return;
    }
    setPending({ format, preview });
  }

  return (
    <div className="min-h-screen p-6 max-w-4xl mx-auto flex flex-col gap-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">ytdl</h1>
          <p className="text-sm text-neutral-400">Self-hosted yt-dlp queue</p>
        </div>
        <span className="text-xs text-neutral-500">{sseState}</span>
      </header>
      <SubmitForm onSubmit={handleSubmit} />
      {pending && (
        <PreviewPanel
          title={pending.preview.title}
          entries={pending.preview.entries}
          onConfirm={async (urls) => {
            await createJobsFromPick(urls, pending.format);
            setPending(null);
            await refresh();
          }}
          onCancel={() => setPending(null)}
        />
      )}
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
