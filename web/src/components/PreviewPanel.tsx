import { useEffect, useMemo, useState } from "react";
import {
  enrichUrls,
  type EnrichedEntry,
  type PreviewEntry,
} from "../api";

interface Props {
  title: string | null;
  entries: PreviewEntry[];
  /** Called with the URLs the user picked (in original playlist order). */
  onConfirm: (urls: string[]) => Promise<void>;
  onCancel: () => void;
}

const ENRICH_BATCH = 20; // matches backend _ENRICH_BATCH_MAX

function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Compute the default set of selected indexes for a fresh render.
 *
 * When `includeDuplicates` is true, every entry is checked (the historical
 * behavior). When false, already-downloaded entries are unchecked by default
 * so the user's confirm click doesn't silently re-queue N files that are
 * already on disk. */
function initialSelected(
  entries: PreviewEntry[],
  includeDuplicates: boolean,
): Set<number> {
  if (includeDuplicates) return new Set(entries.map((_, i) => i));
  const out = new Set<number>();
  entries.forEach((entry, i) => {
    if (!entry.already_downloaded) out.add(i);
  });
  return out;
}

/**
 * Playlist picker. Renders synchronously from the flat probe and fetches
 * per-entry details (duration, uploader, thumbnail) in batches afterwards
 * so the picker appears instantly even on long playlists.
 *
 * All entries are selected by default; the user can deselect any and hit
 * "Download N selected" to enqueue only the chosen subset.
 */
export function PreviewPanel({ title, entries, onConfirm, onCancel }: Props) {
  // "Include already-downloaded" toggle. Defaults OFF so the user doesn't
  // accidentally re-queue everything they already have when they submit
  // the picker. Flipping it on re-adds the duplicate rows to the initial
  // selection; the user can still cherry-pick individual boxes either way.
  const [includeDuplicates, setIncludeDuplicates] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() =>
    initialSelected(entries, includeDuplicates),
  );
  const [enriched, setEnriched] = useState<Map<string, EnrichedEntry>>(new Map());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Reset selection whenever entries change OR the includeDuplicates toggle
  // flips. New playlist == new checkbox state; toggling the switch re-seeds
  // the selection from the toggle's current value (previous manual picks
  // are intentionally discarded — surfacing "here's what you'd get with
  // the toggle in this state" beats trying to remember prior intent).
  useEffect(() => {
    setSelected(initialSelected(entries, includeDuplicates));
  }, [entries, includeDuplicates]);

  // Lazy-enrich in batches once the picker is on screen. Sequential
  // batches keep server fan-out bounded; the backend further caps
  // concurrency inside each batch.
  useEffect(() => {
    let cancelled = false;
    async function run() {
      for (let i = 0; i < entries.length; i += ENRICH_BATCH) {
        const batch = entries.slice(i, i + ENRICH_BATCH).map((e) => e.url);
        try {
          const resp = await enrichUrls(batch);
          if (cancelled) return;
          setEnriched((prev) => {
            const next = new Map(prev);
            for (const item of resp.entries) {
              next.set(item.url, item);
            }
            return next;
          });
        } catch {
          // Surface nothing — enrichment is best-effort; the flat data
          // already populates the row.
        }
      }
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [entries]);

  const allChecked = selected.size === entries.length;
  const noneChecked = selected.size === 0;

  const orderedSelected = useMemo(() => {
    return entries
      .map((e, i) => ({ url: e.url, index: i }))
      .filter(({ index }) => selected.has(index))
      .map(({ url }) => url);
  }, [entries, selected]);

  function toggle(index: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set(entries.map((_, i) => i)));
  }

  function selectNone() {
    setSelected(new Set());
  }

  const dupeCount = entries.filter((e) => e.already_downloaded).length;

  return (
    <section
      className="border border-neutral-800 rounded bg-neutral-950"
      aria-label="playlist picker"
    >
      <header className="flex items-center justify-between p-3 border-b border-neutral-800 gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-medium">
            {title ?? "Playlist"}
          </h2>
          <p className="text-xs text-neutral-400">
            {entries.length} entries — {selected.size} selected
            {dupeCount > 0 && ` — ${dupeCount} already downloaded`}
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          {dupeCount > 0 && (
            <label className="flex items-center gap-1 text-neutral-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={includeDuplicates}
                onChange={(e) => setIncludeDuplicates(e.target.checked)}
                aria-label="Include already-downloaded"
                className="accent-amber-500"
              />
              Include already-downloaded
            </label>
          )}
          <button
            type="button"
            className="text-neutral-400 hover:text-neutral-100"
            onClick={selectAll}
            disabled={allChecked}
          >
            select all
          </button>
          <span className="text-neutral-600">|</span>
          <button
            type="button"
            className="text-neutral-400 hover:text-neutral-100"
            onClick={selectNone}
            disabled={noneChecked}
          >
            select none
          </button>
        </div>
      </header>
      <ul className="max-h-[28rem] overflow-y-auto divide-y divide-neutral-900">
        {entries.map((entry, idx) => {
          const meta = enriched.get(entry.url);
          const checked = selected.has(idx);
          const displayTitle = meta?.title ?? entry.title ?? entry.url;
          const duration = formatDuration(meta?.duration_s ?? null);
          const isDuplicate = Boolean(entry.already_downloaded);
          return (
            <li
              key={entry.url + idx}
              className={
                "flex items-center gap-3 px-3 py-2 text-sm " +
                (isDuplicate ? "opacity-60" : "")
              }
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(idx)}
                aria-label={`select ${displayTitle}`}
                className="accent-emerald-500"
              />
              <span className="w-8 text-right text-xs text-neutral-500 tabular-nums">
                {entry.position ?? idx + 1}
              </span>
              {meta?.thumbnail_url ? (
                <img
                  src={meta.thumbnail_url}
                  alt=""
                  className="w-16 h-9 object-cover rounded bg-neutral-900"
                  loading="lazy"
                />
              ) : (
                <div className="w-16 h-9 rounded bg-neutral-900" aria-hidden />
              )}
              <div className="flex-1 min-w-0">
                <p className="truncate flex items-center gap-1">
                  {isDuplicate && (
                    <span
                      title={`Already downloaded to ${entry.already_downloaded?.path ?? ""}`}
                      aria-label="already downloaded"
                      className="text-amber-400"
                    >
                      ✓
                    </span>
                  )}
                  <span className="truncate">{displayTitle}</span>
                </p>
                <p className="text-xs text-neutral-500 truncate">
                  {meta?.uploader ?? ""}
                  {meta?.uploader && duration ? " · " : ""}
                  {duration}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
      {err && <p className="text-xs text-red-400 px-3 py-2">{err}</p>}
      <footer className="flex justify-end gap-2 p-3 border-t border-neutral-800">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="text-sm rounded px-3 py-1.5 text-neutral-300 hover:text-neutral-100"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy || noneChecked}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await onConfirm(orderedSelected);
            } catch (ex) {
              setErr(ex instanceof Error ? ex.message : "submit failed");
            } finally {
              setBusy(false);
            }
          }}
          className="text-sm rounded px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? "…" : `Download ${selected.size} selected`}
        </button>
      </footer>
    </section>
  );
}
