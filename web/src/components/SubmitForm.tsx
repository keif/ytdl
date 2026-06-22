interface Props {
  url: string;
  onUrlChange: (url: string) => void;
  format: string;
  onFormatChange: (format: string) => void;
  subtitles: boolean;
  onSubtitlesChange: (value: boolean) => void;
}

/**
 * Controlled URL/format input. Owns no submit semantics — the URL change is
 * pushed back to App, which debounces it into a /preview call and renders the
 * appropriate preview surface (inline single-video card or playlist picker)
 * below this form. The actual "Download" action lives on those surfaces.
 */
export function SubmitForm({
  url,
  onUrlChange,
  format,
  onFormatChange,
  subtitles,
  onSubtitlesChange,
}: Props) {
  return (
    <div className="flex gap-2 flex-wrap items-center">
      <input
        className="flex-1 min-w-[24rem] bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm"
        placeholder="Paste a YouTube URL or playlist…"
        value={url}
        onChange={(e) => onUrlChange(e.target.value)}
      />
      <select
        className="bg-neutral-900 border border-neutral-800 rounded px-2 py-2 text-sm"
        value={format}
        onChange={(e) => onFormatChange(e.target.value)}
      >
        <option value="best">Best</option>
        <option value="1080p">1080p</option>
        <option value="720p">720p</option>
        <option value="audio_only">Audio only</option>
      </select>
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
  );
}
