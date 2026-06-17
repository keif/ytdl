"""FastAPI app factory.

Wires the jobs/events/library routers to a Config-driven app instance.
Lifespan starts the worker supervisor unless workers=0 (test mode).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ytdl.config import Config
from ytdl.db import connect, migrate
from ytdl.events_bus import EventsBus


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
            )
            await supervisor.start()
        try:
            yield
        finally:
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
    return app
