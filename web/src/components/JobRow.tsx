import type { Job } from "../api";

interface Props {
  job: Job;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
}

function relativeTime(ms: number | null | undefined): string {
  if (!ms) return "";
  const diff = Date.now() - ms;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

function pickTimestamp(job: Job): { label: string; ts: number | null } {
  if (job.status === "done" || job.status === "failed" || job.status === "canceled") {
    if (job.finished_at) return { label: job.status === "done" ? "finished" : job.status, ts: job.finished_at };
    if (job.started_at) return { label: "started", ts: job.started_at };
    return { label: "queued", ts: job.created_at };
  }
  if (job.status === "running" || job.status === "canceling") {
    return { label: "started", ts: job.started_at ?? job.created_at };
  }
  return { label: "queued", ts: job.created_at };
}

export function JobRow({ job, onCancel, onRetry }: Props) {
  const total = job.filesize_bytes ?? 0;
  const done = job.bytes_done ?? 0;
  const pct = total ? Math.min(100, Math.floor((done * 100) / total)) : 0;
  const cancellable = job.status === "pending" || job.status === "running";
  const retryable = job.status === "failed" || job.status === "canceled" || job.status === "done";
  const { label, ts } = pickTimestamp(job);
  const abs = ts ? new Date(ts).toLocaleString() : "";
  const rel = relativeTime(ts);
  const attempts = job.attempts > 1 ? ` · ${job.attempts} attempts` : "";
  return (
    <li className="flex flex-col gap-1 p-3 border-b border-neutral-800">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <span className="font-medium">{job.title ?? job.url}</span>
          <span className="text-xs text-neutral-400">{job.url}</span>
          <time className="text-xs text-neutral-500" dateTime={abs} title={abs}>
            {label} {rel}{attempts}
          </time>
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
      {(cancellable || retryable) && (
        <div className="self-end flex items-center gap-3">
          {retryable && (
            <button
              className="text-xs text-neutral-400 hover:text-neutral-200"
              onClick={() => onRetry(job.id)}
            >
              retry
            </button>
          )}
          {cancellable && (
            <button
              className="text-xs text-neutral-400 hover:text-neutral-200"
              onClick={() => onCancel(job.id)}
            >
              cancel
            </button>
          )}
        </div>
      )}
    </li>
  );
}
