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


def _preview_entries(
    url: str,
    cookies_browser: str | None,
    *,
    socket_timeout: int = 30,
) -> tuple[str, list[dict]]:
    """Run a flat probe and normalize into (kind, [{url,title,position}]).

    Used by both `preview` and the `--pick` paths so the index numbering
    stays consistent across commands. `socket_timeout` bounds individual
    HTTP reads inside yt-dlp; pass the configured `cfg.probe_timeout_s`
    so CLI behavior matches the server.
    """
    from ytdl.downloader import probe

    info = probe(url, cookies_browser=cookies_browser, socket_timeout=socket_timeout)
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
    url: str,
    *,
    format_pref: str,
    output_dir: Path,
    cookies_browser: str | None,
    subtitles: bool = False,
    subtitle_langs: tuple[str, ...] | list[str] = ("en",),
    probe_timeout_s: int = 30,
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
        subtitles=subtitles,
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
        subtitle_langs=tuple(subtitle_langs) or ("en",),
        probe_timeout_s=probe_timeout_s,
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
    subtitles: bool | None = typer.Option(
        None,
        "--subs/--no-subs",
        help="Download + embed subtitles (locale + EN). "
        "Without this flag, the `subtitles_default` config value applies.",
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
    subs = subtitles if subtitles is not None else cfg.subtitles_default

    if pick is None:
        _download_one(
            url,
            format_pref=format_pref,
            output_dir=out,
            cookies_browser=cfg.cookies_browser,
            subtitles=subs,
            subtitle_langs=cfg.subtitle_langs,
            probe_timeout_s=cfg.probe_timeout_s,
        )
        return

    kind, entries = _preview_entries(
        url, cfg.cookies_browser, socket_timeout=cfg.probe_timeout_s
    )
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
            subtitles=subs,
            subtitle_langs=cfg.subtitle_langs,
            probe_timeout_s=cfg.probe_timeout_s,
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
    kind, entries = _preview_entries(
        url, cfg.cookies_browser, socket_timeout=cfg.probe_timeout_s
    )
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
    if cfg.cookies_browser:
        if cfg.cookies_source == "autodetect":
            console.print(
                f"[cyan]cookies:[/cyan] using {cfg.cookies_browser} (auto-detected)"
            )
        else:
            console.print(f"[cyan]cookies:[/cyan] using {cfg.cookies_browser}")
    else:
        console.print(
            "[yellow]cookies:[/yellow] none detected; "
            "YouTube auth-gated content may fail"
        )
    _warn_if_web_bundle_missing()
    app_obj = build_app(cfg)
    uvicorn.run(app_obj, host=host, port=port, log_level=cfg.log_level.lower())


def _warn_if_web_bundle_missing() -> None:
    """Tell the user the UI bundle isn't built before they hit a 404 on `/`.

    `ytdl serve` mounts the static UI from `ytdl/web/index.html`. In dev
    mode (./dev.sh) you don't need this because Vite serves the source
    directly on :5174 — but a plain `ytdl serve` against a freshly cloned
    repo or after a `pnpm install` (which can clobber the bundle) silently
    serves only the API, and the user gets 404 on `/`.
    """
    from ytdl import api as api_pkg

    bundle = Path(api_pkg.__file__).parent.parent / "web" / "index.html"
    if bundle.exists():
        return
    console.print(
        "[yellow]web UI:[/yellow] no built bundle at "
        f"{bundle.parent} — only the API will be served. "
        "Build the UI with [bold]cd web && pnpm build[/bold] (one-time), "
        "or use [bold]./dev.sh[/bold] for HMR development."
    )


def _format_bytes_per_second(bps: int | None) -> str:
    """Render a bytes/sec value as a short human string. Empty for missing
    data, ``0 B/s`` for an explicit idle reading."""
    if bps is None:
        return ""
    if bps <= 0:
        return "0 B/s"
    if bps < 1024:
        return f"{int(bps)} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    if bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"


def _format_eta(seconds: int | None) -> str:
    """Render a seconds value as ``45s`` / ``2m 05s`` / ``1h 23m``. Empty
    for missing or negative readings."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m {seconds % 60:02d}s"
    hours = minutes // 60
    return f"{hours}h {minutes % 60:02d}m"


def _format_progress(job) -> str:
    """Build the progress column for ``queue ls``: ``67% · 5.2 MB/s · ETA 2m``
    for running jobs, blank otherwise. Pieces with no underlying data are
    dropped."""
    if job.status.value != "running":
        return ""
    parts: list[str] = []
    if job.filesize_bytes and job.bytes_done is not None:
        pct = min(100, int((job.bytes_done * 100) / job.filesize_bytes))
        parts.append(f"{pct}%")
    speed = _format_bytes_per_second(job.speed_bps)
    if speed:
        parts.append(speed)
    eta = _format_eta(job.eta_s)
    if eta:
        parts.append(f"ETA {eta}")
    return " · ".join(parts)


@queue_app.command("ls")
def queue_ls(status: str | None = typer.Option(None, "--status")) -> None:
    """List jobs in the queue, optionally filtered by status."""
    from ytdl.models import JobStatus

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    jobs = list_jobs(conn, status=JobStatus(status) if status else None)
    conn.close()

    table = Table("id", "status", "progress", "title", "url")
    for j in jobs:
        table.add_row(j.id, j.status.value, _format_progress(j), j.title or "—", j.url)
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
    subtitles: bool | None = typer.Option(
        None,
        "--subs/--no-subs",
        help="Download + embed subtitles (locale + EN). "
        "Without this flag, the `subtitles_default` config value applies.",
    ),
) -> None:
    """Enqueue a URL for the worker pool to pick up.

    Without --pick: enqueues the URL as a single VIDEO job; the worker
    detects playlists and expands them into children.
    With --pick: probes the playlist here (synchronously) and enqueues only
    the picked entries as standalone VIDEO jobs.
    """
    cfg = load_config()
    subs = subtitles if subtitles is not None else cfg.subtitles_default
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
                subtitles=subs,
            )
            console.print(f"queued [bold]{job_id}[/bold]")
            return

        kind, entries = _preview_entries(
            url, cfg.cookies_browser, socket_timeout=cfg.probe_timeout_s
        )
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
                        subtitles=subs,
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


@queue_app.command("redownload")
def queue_redownload(job_id: str) -> None:
    """Clone a failed/canceled/done job with force-overwrite so yt-dlp re-fetches
    the file even when it already exists on disk."""
    from ytdl.queue import retry_job

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    try:
        new_id = retry_job(conn, job_id, force_overwrite=True)
    finally:
        conn.close()
    if new_id is None:
        console.print(
            f"[red]Cannot redownload {job_id}[/red] "
            "(not found or not in a retryable state)"
        )
        raise typer.Exit(code=1)
    console.print(f"queued redownload as [bold]{new_id}[/bold]")


@queue_app.command("clear")
def queue_clear(
    older_than_days: int = typer.Option(
        7, "--older-than-days", "-d",
        help="Only clear DONE jobs older than this many days. Default: 7.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Delete DONE jobs older than --older-than-days (default 7).

    Failed and canceled jobs are kept so you can triage them.
    """
    if older_than_days < 0:
        console.print("[red]--older-than-days must be >= 0[/red]")
        raise typer.Exit(code=2)

    from ytdl.queue import clear_done_jobs, count_clearable

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    older_than_ms = older_than_days * 86_400_000
    n = count_clearable(conn, older_than_ms=older_than_ms)
    if n == 0:
        console.print("[dim]nothing to clear[/dim]")
        conn.close()
        return
    if not yes:
        console.print(
            f"about to delete [bold]{n}[/bold] DONE jobs older than "
            f"{older_than_days} days"
        )
        if not typer.confirm("continue?", default=False):
            conn.close()
            console.print("[dim]aborted[/dim]")
            return
    deleted = clear_done_jobs(conn, older_than_ms=older_than_ms)
    conn.close()
    console.print(f"deleted [bold]{deleted}[/bold] jobs")


@cookies_app.command("status")
def cookies_status() -> None:
    """Print the cookies browser that will be used at job time."""
    cfg = load_config()
    if cfg.cookies_browser:
        console.print(
            f"browser: [bold]{cfg.cookies_browser}[/bold] ({cfg.cookies_source})"
        )
    else:
        console.print("browser: [yellow]none detected[/yellow]")
        console.print(
            "hint: run `ytdl cookies use <browser>` to set one explicitly"
        )


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
