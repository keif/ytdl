"""Typer CLI: `ytdl`."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ytdl.config import _xdg_config_home, load_config
from ytdl.cookies import normalize_browser
from ytdl.db import connect, migrate
from ytdl.models import JobKind
from ytdl.queue import enqueue, list_jobs

app = typer.Typer(no_args_is_help=True, add_completion=False)
queue_app = typer.Typer(no_args_is_help=True, help="Inspect or manage the queue")
cookies_app = typer.Typer(no_args_is_help=True, help="Browser-cookie selection for yt-dlp")
app.add_typer(queue_app, name="queue")
app.add_typer(cookies_app, name="cookies")

console = Console()


_OUTPUT_DIR_OPTION: Path | None = typer.Option(None, "--output", "-o", help="Override output dir")


def _parse_pick(spec: str, *, max_index: int) -> list[int]:
    """Parse ``--pick 1,3,5-9`` into a sorted, deduped list of 1-based indices.

    Raises ``typer.BadParameter`` on malformed input or out-of-range entries.
    """
    if not spec.strip():
        raise typer.BadParameter("pick spec must not be empty")
    selected: set[int] = set()
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError as exc:
                raise typer.BadParameter(f"bad range: {token!r}") from exc
            if lo > hi:
                raise typer.BadParameter(f"empty range: {token!r}")
            for i in range(lo, hi + 1):
                selected.add(i)
        else:
            try:
                selected.add(int(token))
            except ValueError as exc:
                raise typer.BadParameter(f"bad index: {token!r}") from exc
    if not selected:
        raise typer.BadParameter("pick spec yielded no indices")
    bad = [i for i in selected if i < 1 or i > max_index]
    if bad:
        raise typer.BadParameter(
            f"indices out of range (1..{max_index}): {sorted(bad)}"
        )
    return sorted(selected)


def _preview_entries(url: str, cookies_browser: str | None) -> tuple[str, list[dict]]:
    """Run a flat probe and normalize into (kind, [{url,title,position}]).

    Used by both `preview` and the `--pick` paths so the index numbering
    stays consistent across commands.
    """
    from ytdl.downloader import probe

    info = probe(url, cookies_browser=cookies_browser)
    kind = "playlist" if info.get("_type") == "playlist" else "video"
    raw_entries = info.get("entries") if kind == "playlist" else [info]
    entries: list[dict] = []
    for idx, entry in enumerate(raw_entries or []):
        if not isinstance(entry, dict):
            continue
        entry_url = entry.get("webpage_url") or entry.get("url") or ""
        if not entry_url:
            continue
        entries.append(
            {
                "url": entry_url,
                "title": entry.get("title"),
                "position": entry.get("playlist_index") or (idx + 1),
            }
        )
    return kind, entries


def _download_one(
    url: str, *, format_pref: str, output_dir: Path, cookies_browser: str | None
) -> None:
    """Synchronously download a single URL, printing percent progress."""
    from yt_dlp import YoutubeDL  # late import to keep CLI startup snappy

    from ytdl.downloader import DownloadContext, download
    from ytdl.models import Job, JobStatus
    from ytdl.ulid import new_ulid

    output_dir.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=new_ulid(),
        url=url,
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref=format_pref,
        output_dir=str(output_dir),
    )

    last_pct = -1

    def on_progress(d: dict) -> None:
        nonlocal last_pct
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int(done * 100 / total) if total else 0
            if pct != last_pct:
                last_pct = pct
                console.print(f"  [{pct:3d}%] {done} / {total} bytes", end="\r")

    ctx = DownloadContext(
        ydl_cls=YoutubeDL,
        cookies_browser=cookies_browser,
        on_progress=on_progress,
        cancel_flag=lambda: False,
    )
    console.print(f"[bold]Downloading[/bold] {url}")
    result = download(job, ctx)
    console.print(f"[green]Done[/green] -> {result.output_path}")


@app.command()
def get(
    url: str = typer.Argument(..., help="URL to download"),
    format_pref: str = typer.Option(
        "best", "--format", "-f", help="best | 1080p | audio_only | <yt-dlp format>"
    ),
    output_dir: Path | None = _OUTPUT_DIR_OPTION,
    pick: str | None = typer.Option(
        None,
        "--pick",
        help="When URL is a playlist, download only these 1-based entries "
        "(e.g. '1,3,5-9'). Without --pick the entire playlist is downloaded.",
    ),
) -> None:
    """Download a URL directly (synchronous, no server).

    Plain video: downloads the video.
    Playlist URL with --pick: probes the playlist, narrows to the picked
    entries, and downloads each in order.
    Playlist URL without --pick: downloads the whole list (yt-dlp default).
    """
    cfg = load_config()
    out = output_dir or cfg.output_dir

    if pick is None:
        _download_one(
            url,
            format_pref=format_pref,
            output_dir=out,
            cookies_browser=cfg.cookies_browser,
        )
        return

    kind, entries = _preview_entries(url, cfg.cookies_browser)
    if kind != "playlist" or not entries:
        raise typer.BadParameter(
            "--pick requires a playlist URL with at least one entry"
        )
    indices = _parse_pick(pick, max_index=len(entries))
    console.print(
        f"[bold]Picked {len(indices)} of {len(entries)}[/bold] from playlist"
    )
    for i in indices:
        entry = entries[i - 1]
        title = entry["title"] or entry["url"]
        console.print(f"[cyan]{i:>3}.[/cyan] {title}")
        _download_one(
            entry["url"],
            format_pref=format_pref,
            output_dir=out,
            cookies_browser=cfg.cookies_browser,
        )


@app.command()
def preview(
    url: str = typer.Argument(..., help="URL to probe"),
) -> None:
    """Print a numbered listing of a playlist's entries (or the single video).

    Pairs with `ytdl get --pick` / `ytdl queue add --pick`: pick entries by
    the index shown in the leftmost column.
    """
    cfg = load_config()
    kind, entries = _preview_entries(url, cfg.cookies_browser)
    if not entries:
        console.print("[yellow]no entries[/yellow]")
        return
    table = Table("#", "title", "url", title=f"{kind}: {len(entries)} entries")
    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), entry["title"] or "—", entry["url"])
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    """Start the API + web UI."""
    import uvicorn

    from ytdl.api import build_app

    cfg = load_config()
    app_obj = build_app(cfg)
    uvicorn.run(app_obj, host=host, port=port, log_level=cfg.log_level.lower())


@queue_app.command("ls")
def queue_ls(status: str | None = typer.Option(None, "--status")) -> None:
    """List jobs in the queue, optionally filtered by status."""
    from ytdl.models import JobStatus

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    jobs = list_jobs(conn, status=JobStatus(status) if status else None)
    conn.close()

    table = Table("id", "status", "title", "url")
    for j in jobs:
        table.add_row(j.id, j.status.value, j.title or "—", j.url)
    console.print(table)


@queue_app.command("add")
def queue_add(
    url: str,
    format_pref: str = typer.Option("best", "--format", "-f"),
    pick: str | None = typer.Option(
        None,
        "--pick",
        help="When URL is a playlist, enqueue only these 1-based entries "
        "(e.g. '1,3,5-9'). Without --pick, the URL is enqueued as-is and "
        "the worker handles playlist expansion.",
    ),
) -> None:
    """Enqueue a URL for the worker pool to pick up.

    Without --pick: enqueues the URL as a single VIDEO job; the worker
    detects playlists and expands them into children.
    With --pick: probes the playlist here (synchronously) and enqueues only
    the picked entries as standalone VIDEO jobs.
    """
    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    try:
        if pick is None:
            job_id = enqueue(
                conn,
                url=url,
                kind=JobKind.VIDEO,
                format_pref=format_pref,
                output_dir=str(cfg.output_dir),
            )
            console.print(f"queued [bold]{job_id}[/bold]")
            return

        kind, entries = _preview_entries(url, cfg.cookies_browser)
        if kind != "playlist" or not entries:
            raise typer.BadParameter(
                "--pick requires a playlist URL with at least one entry"
            )
        indices = _parse_pick(pick, max_index=len(entries))
        ids: list[str] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for i in indices:
                entry = entries[i - 1]
                ids.append(
                    enqueue(
                        conn,
                        url=entry["url"],
                        kind=JobKind.VIDEO,
                        format_pref=format_pref,
                        output_dir=str(cfg.output_dir),
                    )
                )
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        console.print(
            f"queued [bold]{len(ids)}[/bold] picked entries from playlist"
        )
    finally:
        conn.close()


@queue_app.command("retry")
def queue_retry(job_id: str) -> None:
    """Re-enqueue a failed/canceled/done job as a new pending job."""
    from ytdl.queue import retry_job

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    try:
        new_id = retry_job(conn, job_id)
    finally:
        conn.close()
    if new_id is None:
        console.print(
            f"[red]Cannot retry {job_id}[/red] (not found or not in a retryable state)"
        )
        raise typer.Exit(code=1)
    console.print(f"queued retry as [bold]{new_id}[/bold]")


@cookies_app.command("use")
def cookies_use(browser: str) -> None:
    """Persist the browser yt-dlp should read cookies from."""
    try:
        name = normalize_browser(browser)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    cfg_path = _xdg_config_home() / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = cfg_path.read_text() if cfg_path.exists() else ""
    lines = [line for line in existing.splitlines() if not line.startswith("cookies_browser")]
    lines.append(f'cookies_browser = "{name}"')
    cfg_path.write_text("\n".join(lines) + "\n")
    console.print(f"cookies_browser set to [bold]{name}[/bold] in {cfg_path}")
