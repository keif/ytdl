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
}

/**
 * Controlled URL/format input. Owns no submit semantics — the URL change is
 * pushed back to App, which debounces it into a /preview call and renders the
 * appropriate preview surface (inline single-video card or playlist picker)
 * below this form. The actual "Download" action lives on those surfaces.
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
}: Props) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2 flex-wrap items-center">
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
      </div>
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
