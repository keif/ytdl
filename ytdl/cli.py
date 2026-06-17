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


@app.command()
def get(
    url: str = typer.Argument(..., help="URL to download"),
    format_pref: str = typer.Option(
        "best", "--format", "-f", help="best | 1080p | audio_only | <yt-dlp format>"
    ),
    output_dir: Path | None = _OUTPUT_DIR_OPTION,
) -> None:
    """Download a single URL directly (synchronous, no server)."""
    from yt_dlp import YoutubeDL  # late import to keep CLI startup snappy

    from ytdl.downloader import DownloadContext, download
    from ytdl.models import Job, JobStatus
    from ytdl.ulid import new_ulid

    cfg = load_config()
    out = output_dir or cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    job = Job(
        id=new_ulid(),
        url=url,
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref=format_pref,
        output_dir=str(out),
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
        cookies_browser=cfg.cookies_browser,
        on_progress=on_progress,
        cancel_flag=lambda: False,
    )
    console.print(f"[bold]Downloading[/bold] {url}")
    result = download(job, ctx)
    console.print(f"[green]Done[/green] -> {result.output_path}")


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
) -> None:
    """Enqueue a single URL for the worker pool to pick up."""
    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    job_id = enqueue(
        conn,
        url=url,
        kind=JobKind.VIDEO,
        format_pref=format_pref,
        output_dir=str(cfg.output_dir),
    )
    conn.close()
    console.print(f"queued [bold]{job_id}[/bold]")


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
