"""FastAPI app factory.

Wires the jobs/events/library routers to a Config-driven app instance.
Lifespan starts the worker supervisor unless workers=0 (test mode).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ytdl.config import Config
from ytdl.db import connect, migrate
from ytdl.events_bus import EventsBus

log = logging.getLogger(__name__)


def build_app(config: Config) -> FastAPI:
    bus = EventsBus()

    # Migrate eagerly so the schema exists regardless of whether the ASGI
    # lifespan is driven by the test client (TestClient runs it; raw
    # AsyncClient + ASGITransport does not). migrate() is idempotent.
    conn = connect(config.db_path)
    migrate(conn)
    conn.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        supervisor = None
        if config.workers > 0:
            from ytdl.workers import Supervisor

            supervisor = Supervisor(
                db_path=config.db_path,
                workers=config.workers,
                bus=bus,
                cookies_browser=config.cookies_browser,
                cookies_file=config.cookies_file,
                pot_provider_url=config.pot_provider_url,
                subtitle_langs=config.subtitle_langs,
                probe_timeout_s=config.probe_timeout_s,
            )
            await supervisor.start()
            app.state.supervisor = supervisor
        else:
            app.state.supervisor = None
        # Startup dedup scan runs in the background so ASGI startup isn't
        # blocked by a large library. The task result is logged for
        # operators; failure is non-fatal — the /library/rescan endpoint
        # is available as a manual retry.
        rescan_task: asyncio.Task | None = None
        if config.dedup_enabled:
            rescan_task = asyncio.create_task(
                _initial_library_scan(config), name="ytdl.library.initial_scan"
            )
        try:
            yield
        finally:
            if rescan_task and not rescan_task.done():
                rescan_task.cancel()
                try:
                    await rescan_task
                except (asyncio.CancelledError, Exception):
                    pass
            if supervisor:
                await supervisor.stop()

    app = FastAPI(title="ytdl", lifespan=lifespan)
    app.state.config = config
    app.state.bus = bus

    from ytdl.api import routes_jobs

    app.include_router(routes_jobs.router)

    from ytdl.api import routes_events

    app.include_router(routes_events.router)

    from ytdl.api import routes_library

    app.include_router(routes_library.router)

    from ytdl.api import routes_preview

    app.include_router(routes_preview.router)

    @app.get("/status", tags=["status"])
    def status() -> dict:
        """Return runtime status for the web UI header chips.

        Cookies + presence of host binaries (deno for YouTube's JS challenge,
        ffmpeg for stream merging). The UI renders these so the user can fix
        a missing dep before they hit a confusing [forbidden] error.
        """
        from ytdl.runtime import probe_deno, probe_ffmpeg

        cfg: Config = app.state.config
        deno = probe_deno()
        ffmpeg = probe_ffmpeg()
        return {
            "cookies_browser": cfg.cookies_browser,
            "cookies_source": cfg.cookies_source,
            "cookies_file": cfg.cookies_file,
            "pot_provider_url": cfg.pot_provider_url,
            "deno": {"present": deno.present, "path": deno.path},
            "ffmpeg": {"present": ffmpeg.present, "path": ffmpeg.path},
            "subtitles_default": cfg.subtitles_default,
            # Surface the configured default output_dir so the UI's
            # "Save to" override can show what the server would use as
            # the placeholder when the field is left blank.
            "output_dir": str(cfg.output_dir),
            # Seconds the UI waits before auto-submitting a single-video
            # preview. The UI reads this on mount so the countdown banner
            # uses the configured default. A value of 0 disables the
            # auto-submit flow entirely.
            "autosubmit_delay_s": cfg.autosubmit_delay_s,
            # Probe timeout (seconds) — surfaced so the UI can hint to the
            # user how long a hung preview will wait before the server
            # returns a 504. Configurable via YTDL_PROBE_TIMEOUT_S or the
            # probe_timeout_s TOML key.
            "probe_timeout_s": cfg.probe_timeout_s,
            # Duplicate-detection surface. A future settings panel renders
            # both of these; today the UI just uses them to know the
            # feature is on. Empty ``library_scan_dirs`` means "server
            # falls back to (output_dir,)" — we resolve the fallback here
            # so the client sees the actual list that gets scanned.
            "library_scan_dirs": list(cfg.resolve_library_scan_dirs()),
            "dedup_enabled": cfg.dedup_enabled,
        }

    # Serve the built Vite bundle when present. The Dockerfile copies the
    # production build into ytdl/web/; in dev there's no bundle and Vite
    # proxies the API calls instead. Mount AFTER API routers so /jobs,
    # /events, /library keep precedence.
    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists() and (web_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="ui")

    return app


def _load_runtime_config() -> Config:
    from ytdl.config import load_config

    return load_config()


def app_factory() -> FastAPI:
    return build_app(_load_runtime_config())


async def _initial_library_scan(config: Config) -> None:
    """Kick off a fire-and-forget scan of the configured library dirs.

    Runs in a worker thread so a large tree doesn't monopolize the loop.
    Logs the outcome — good enough for operators; the /library/rescan
    endpoint exists for surfacing failures to the UI on demand.
    """
    from ytdl.library import scan_directories

    dirs = list(config.resolve_library_scan_dirs())
    db_path = config.db_path

    def _run() -> tuple[int, list[str], float]:
        conn = connect(db_path)
        try:
            migrate(conn)
            return scan_directories(conn, dirs)
        finally:
            conn.close()

    try:
        count, scanned, elapsed = await asyncio.to_thread(_run)
        log.info(
            "library: initial scan indexed %d file(s) across %d dir(s) in %.2fs",
            count,
            len(scanned),
            elapsed,
        )
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        log.warning("library: initial scan failed: %s", exc)
