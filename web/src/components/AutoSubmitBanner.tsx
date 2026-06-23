interface Props {
  remaining: number;
  onCancel: () => void;
}

/**
 * Inline status banner that announces an in-progress auto-submit countdown.
 *
 * Sits between the SubmitForm and the preview card while the timer runs. The
 * banner is intentionally low-weight (same `text-xs text-neutral-400` tier as
 * the preview-loading message) so it doesn't shout — the user is about to do
 * the thing they pasted the URL for, not be warned about it.
 *
 * `role="status" aria-live="polite"` lets screen readers announce each tick
 * without interrupting whatever the user is doing. The Cancel button stops the
 * countdown; the parent owns the actual interval handle.
 */
export function AutoSubmitBanner({ remaining, onCancel }: Props) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-between text-xs text-neutral-400 border border-neutral-800 rounded px-3 py-2 bg-neutral-950"
    >
      <span>
        Downloading in{" "}
        <strong className="font-medium text-neutral-200">{remaining}s</strong>
      </span>
      <button
        type="button"
        onClick={onCancel}
        aria-label="Cancel auto-submit"
        className="text-xs text-neutral-400 hover:text-neutral-200"
      >
        Cancel
      </button>
    </div>
  );
}
