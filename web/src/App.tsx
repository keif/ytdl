import { useEffect, useState } from "react";
import { cancelJob, createJob, listJobs, type Job } from "./api";
import { SubmitForm } from "./components/SubmitForm";
import { JobList } from "./components/JobList";
import { useJobsStream } from "./hooks/useJobsStream";

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);

  async function refresh() {
    const list = await listJobs();
    setJobs(list.jobs);
  }

  useEffect(() => {
    refresh().catch(() => {});
  }, []);

  const sseState = useJobsStream(() => {
    // any event => refresh listing
    refresh().catch(() => {});
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
