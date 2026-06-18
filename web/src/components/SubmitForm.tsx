import { useState } from "react";

interface Props {
  onSubmit: (url: string, format: string) => Promise<void>;
}

export function SubmitForm({ onSubmit }: Props) {
  const [url, setUrl] = useState("");
  const [format, setFormat] = useState("best");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  return (
    <form
      className="flex gap-2 flex-wrap items-center"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!url.trim()) return;
        setBusy(true);
        setErr(null);
        try {
          await onSubmit(url.trim(), format);
          setUrl("");
        } catch (ex) {
          setErr(ex instanceof Error ? ex.message : "submit failed");
        } finally {
          setBusy(false);
        }
      }}
    >
      <input
        className="flex-1 min-w-[24rem] bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm"
        placeholder="Paste a YouTube URL or playlist…"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
      />
      <select
        className="bg-neutral-900 border border-neutral-800 rounded px-2 py-2 text-sm"
        value={format}
        onChange={(e) => setFormat(e.target.value)}
      >
        <option value="best">Best</option>
        <option value="1080p">1080p</option>
        <option value="720p">720p</option>
        <option value="audio_only">Audio only</option>
      </select>
      <button
        type="submit"
        disabled={busy}
        className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-sm rounded px-4 py-2"
      >
        {busy ? "…" : "Download"}
      </button>
      {err && <span className="text-xs text-red-400 basis-full">{err}</span>}
    </form>
  );
}
