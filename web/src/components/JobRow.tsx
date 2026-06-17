import type { Job } from "../api";

interface Props {
  job: Job;
  onCancel: (id: string) => void;
}

export function JobRow({ job, onCancel }: Props) {
  const total = job.filesize_bytes ?? 0;
  const done = job.bytes_done ?? 0;
  const pct = total ? Math.min(100, Math.floor((done * 100) / total)) : 0;
  return (
    <li className="flex flex-col gap-1 p-3 border-b border-neutral-800">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <span className="font-medium">{job.title ?? job.url}</span>
          <span className="text-xs text-neutral-400">{job.url}</span>
        </div>
        <span className="text-xs uppercase text-neutral-500">{job.status}</span>
      </div>
      {job.status === "running" && (
        <div className="flex items-center gap-2 text-xs text-neutral-400">
          <div className="h-1 flex-1 bg-neutral-800 rounded">
            <div className="h-1 bg-emerald-500 rounded" style={{ width: `${pct}%` }} />
          </div>
          <span>{pct}%</span>
        </div>
      )}
      {job.error && (
        <p className="text-xs text-red-400">{job.error}</p>
      )}
      {(job.status === "pending" || job.status === "running") && (
        <button
          className="self-end text-xs text-neutral-400 hover:text-neutral-200"
          onClick={() => onCancel(job.id)}
        >
          cancel
        </button>
      )}
    </li>
  );
}
