import type { EnrichedEntry, PreviewEntry } from "../api";

interface Props {
  entry: PreviewEntry;
  enriched?: EnrichedEntry;
  format: string;
  onDownload: () => Promise<void>;
  busy: boolean;
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Inline preview card for a single-video URL. Renders synchronously from the
 * flat /preview payload and upgrades in place once /preview/enrich returns
 * duration/uploader/thumbnail. The Download button is the only commit point
 * for the queue from this surface.
 *
 * Duplicate detection: when the /preview response marked this entry as
 * already downloaded, we render a warning banner above the card and swap
 * the Download button to a warning-styled "Force re-download". Clicking the
 * button surfaces the same submit path but with `force_overwrite=true` so
 * the server's 409 duplicate check is bypassed and yt-dlp overwrites the
 * existing file on disk.
 */
export function PreviewVideo({ entry, enriched, format, onDownload, busy }: Props) {
  const title = enriched?.title ?? entry.title ?? entry.url;
  const duration = formatDuration(enriched?.duration_s);
  const uploader = enriched?.uploader;
  const thumb = enriched?.thumbnail_url;
  const duplicate = entry.already_downloaded;
  return (
    <div className="flex flex-col gap-2">
      {duplicate && (
        <div
          role="alert"
          className="text-xs text-amber-300 border border-amber-700/60 bg-amber-950/40 rounded px-3 py-2"
        >
          <span className="font-medium">Already downloaded</span> to{" "}
          <code className="text-amber-200 break-all">{duplicate.path}</code>.
          Queueing will overwrite the existing file.
        </div>
      )}
      <section
        className="border border-neutral-800 rounded p-4 flex gap-4 items-start bg-neutral-950"
        aria-label="video preview"
      >
        {thumb ? (
          <img
            src={thumb}
            alt=""
            className="w-32 h-20 object-cover rounded bg-neutral-900"
            loading="lazy"
          />
        ) : (
          <div className="w-32 h-20 rounded bg-neutral-900" aria-hidden />
        )}
        <div className="flex-1 flex flex-col gap-1 min-w-0">
          <h3 className="font-medium truncate" title={title}>
            {title}
          </h3>
          <p className="text-xs text-neutral-400 truncate" title={entry.url}>
            {entry.url}
          </p>
          <div className="text-xs text-neutral-500 flex gap-3 flex-wrap">
            {uploader && <span>{uploader}</span>}
            {duration && <span>{duration}</span>}
            <span className="uppercase">{format}</span>
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            onDownload().catch(() => {});
          }}
          className={
            duplicate
              ? "bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-sm rounded px-4 py-2"
              : "bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-sm rounded px-4 py-2"
          }
        >
          {busy ? "…" : duplicate ? "Force re-download" : "Download"}
        </button>
      </section>
    </div>
  );
}
