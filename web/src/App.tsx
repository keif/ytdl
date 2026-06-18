import { useEffect, useRef, useState } from "react";
import { cancelJob, createJob, listJobs, type Job } from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { JobList } from "./components/JobList";
import { useJobsStream } from "./hooks/useJobsStream";

const REFRESH_DEBOUNCE_MS = 200;

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
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
        onSubmit={async (url, format) => {
          await createJob(url, format);
          await refresh();
        }}
      />
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
