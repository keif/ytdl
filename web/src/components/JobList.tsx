import type { Job } from "../api";
import { JobRow } from "./JobRow";

interface Props {
  jobs: Job[];
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
}

export function JobList({ jobs, onCancel, onRetry }: Props) {
  if (jobs.length === 0) {
    return <p className="text-sm text-neutral-500 px-3 py-6">No jobs yet.</p>;
  }
  return (
    <ul className="border border-neutral-800 rounded">
      {jobs.map((j) => (
        <JobRow key={j.id} job={j} onCancel={onCancel} onRetry={onRetry} />
      ))}
    </ul>
  );
}
