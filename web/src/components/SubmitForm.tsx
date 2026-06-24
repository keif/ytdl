interface Props {
  url: string;
  onUrlChange: (url: string) => void;
  format: string;
  onFormatChange: (format: string) => void;
  subtitles: boolean;
  onSubtitlesChange: (value: boolean) => void;
  audioOnly: boolean;
  onAudioOnlyChange: (value: boolean) => void;
  outputDir: string;
  onOutputDirChange: (value: string) => void;
  // Server's configured default — shown as the input placeholder so the user
  // can see what the request will fall back to when they leave it blank.
  outputDirPlaceholder: string;
  // Eager-submit affordance: the parent fires the same code path as the
  // preview card's Download button, but without waiting for /preview to
  // resolve. Surfaced as both a clickable "Queue" button and the Enter key
  // inside the URL input.
  onQueue: () => void;
  // Mirrors the parent's in-flight POST /jobs state so the Queue button can
  // disable while a submit is pending — same contract as the preview card's
  // Download button.
  submitting: boolean;
}

/**
 * Controlled URL/format input. The URL change is pushed back to App, which
 * debounces it into a /preview call and renders the appropriate preview
 * surface (inline single-video card or playlist picker) below this form.
 *
 * Two submit affordances live here:
 *   1. The "Queue" button next to the URL input — always visible, enabled
 *      the moment the URL passes a basic http(s) shape check. Calls
 *      onQueue() which submits immediately, without waiting on /preview.
 *   2. Enter key inside the URL input — wired through the standard
 *      <form onSubmit> so the browser only fires it on Enter (not every
 *      keystroke). preventDefault stops the browser from trying to
 *      navigate away.
 *
 * The preview card's "Download" button (rendered by PreviewVideo) is a
 * separate, post-preview commit point for users who want to confirm before
 * queueing. Both paths route through the same submitSingle in App.
 *
 * The "Audio only" checkbox is a first-class toggle — when checked it forces
 * format_pref="audio_only" at submit time regardless of the dropdown, and the
 * dropdown visually mutes to signal the override.
 *
 * The "Advanced" disclosure carries the per-job output-directory override.
 * Blank means "use the server's configured default" (placeholder reveals what
 * that is). Validation is server-side — we don't try to second-guess the
 * filesystem from the browser.
 */
export function SubmitForm({
  url,
  onUrlChange,
  format,
  onFormatChange,
  subtitles,
  onSubtitlesChange,
  audioOnly,
  onAudioOnlyChange,
  outputDir,
  onOutputDirChange,
  outputDirPlaceholder,
  onQueue,
  submitting,
}: Props) {
  // Same shape check the preview effect uses to short-circuit obviously bad
  // input. Disabling Queue locally avoids a 422 round-trip and gives the
  // user instant feedback that the URL isn't ready.
  const trimmed = url.trim();
  const queueDisabled =
    !trimmed || !/^https?:\/\//i.test(trimmed) || submitting;

  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex gap-2 flex-wrap items-center"
        onSubmit={(e) => {
          e.preventDefault();
          if (queueDisabled) return;
          onQueue();
        }}
      >
        <input
          className="flex-1 min-w-[24rem] bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm"
          placeholder="Paste a YouTube URL or playlist…"
          value={url}
          onChange={(e) => onUrlChange(e.target.value)}
        />
        <select
          className={`bg-neutral-900 border border-neutral-800 rounded px-2 py-2 text-sm ${
            audioOnly ? "opacity-50 text-neutral-500 cursor-not-allowed" : ""
          }`}
          value={format}
          onChange={(e) => onFormatChange(e.target.value)}
          disabled={audioOnly}
          title={
            audioOnly
              ? "Audio-only is selected — format dropdown has no effect"
              : undefined
          }
        >
          <option value="best">Best</option>
          <option value="1080p">1080p</option>
          <option value="720p">720p</option>
          <option value="audio_only">Audio only</option>
        </select>
        <label
          className="flex items-center gap-1.5 text-sm text-neutral-300 cursor-pointer select-none"
          title="Force audio_only regardless of the format dropdown — useful for podcasts, music, lectures"
        >
          <input
            type="checkbox"
            checked={audioOnly}
            onChange={(e) => onAudioOnlyChange(e.target.checked)}
          />
          <span>Audio only</span>
          <span className="text-xs text-neutral-500">(MP3-style, no video)</span>
        </label>
        <label
          className="flex items-center gap-1.5 text-sm text-neutral-300 cursor-pointer select-none"
          title="Download real subtitles, embed them in the MP4, and save a sidecar .vtt"
        >
          <input
            type="checkbox"
            checked={subtitles}
            onChange={(e) => onSubtitlesChange(e.target.checked)}
          />
          <span>Subtitles</span>
          <span className="text-xs text-neutral-500">(your locale + EN)</span>
        </label>
        <button
          type="submit"
          disabled={queueDisabled}
          title="Queue this URL for download without waiting for the preview"
          className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-sm rounded px-4 py-2"
        >
          {submitting ? "…" : "Queue"}
        </button>
      </form>
      <details className="text-sm text-neutral-300">
        <summary className="cursor-pointer select-none text-neutral-400 hover:text-neutral-200 w-fit">
          Advanced
        </summary>
        <div className="mt-2 flex gap-2 flex-wrap items-center">
          <label
            htmlFor="output-dir-input"
            className="text-sm text-neutral-300 select-none"
            title="Per-job destination override. Leave blank to use the server's configured default."
          >
            Save to
          </label>
          <input
            id="output-dir-input"
            type="text"
            className="flex-1 min-w-[20rem] bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm font-mono"
            placeholder={outputDirPlaceholder || "(default)"}
            value={outputDir}
            onChange={(e) => onOutputDirChange(e.target.value)}
          />
        </div>
      </details>
    </div>
  );
}
