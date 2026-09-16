"""FastAPI app factory + uvicorn lifecycle for the operator web portal.

Runs alongside the bot as an asyncio task. This is the ADMIN portal
(radio, receiver, text-bot console, schedule, settings, logs); the goestools
dashboard on its own port is the public, read-only page. The portal itself
has no login: keep it on the LAN or put access control at the edge.
"""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from meshcore_weather.config import settings
from meshcore_weather.portal import logbuf

logger = logging.getLogger(__name__)

_PORTAL_DIR = Path(__file__).parent
_STATIC_DIR = _PORTAL_DIR / "static"
_TEMPLATES_DIR = _PORTAL_DIR / "templates"


def create_app(bot: Any) -> FastAPI:
    """Create a FastAPI app wired to a running bot instance.

    The bot exposes store, radio, emwin, and _broadcaster attributes
    that the routes read from (and occasionally trigger actions on).
    """
    app = FastAPI(
        title="Meshcore Weather Portal",
        docs_url=None,  # disable Swagger UI (depends on CDN assets)
        redoc_url=None,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.bot = bot
    app.state.templates = templates

    # Any state-changing request must carry a header that a cross-site page
    # cannot set without a CORS preflight (which we never answer). This is
    # what stops a web page someone on the LAN opens from flipping transmit
    # on or rewriting .env through their browser.
    @app.middleware("http")
    async def _require_xhr_header(request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("x-requested-with") != "meshcore-portal":
            return Response('{"detail":"missing X-Requested-With: meshcore-portal"}', status_code=403,
                            media_type="application/json")
        return await call_next(request)

    # Mount static files (served from local disk, no CDN)
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # Register routes
    from meshcore_weather.portal.routes import admin, api, bridge, pages
    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    # The debug bridge answers only when MCW_BRIDGE_TOKEN is set (bridge.py).
    app.include_router(bridge.router, prefix="/api")

    return app


class _Server(uvicorn.Server):
    """uvicorn with its hands off the process signals.

    `Server.serve()` calls `signal.signal` for SIGINT and SIGTERM, which
    replaces the handlers the bot installed before it (main.run). A systemd
    stop would then shut the web server down while the radio, the scheduler
    and the store ran on, and the bot's own shutdown would only start when
    uvicorn re-raised the signal on its way out. The bot owns the signals;
    the portal stops when stop() tells it to.
    """

    def install_signal_handlers(self) -> None:          # uvicorn < 0.29
        pass

    @contextlib.contextmanager
    def capture_signals(self):                          # uvicorn >= 0.29
        yield


class PortalServer:
    """Manages the uvicorn lifecycle as an asyncio task."""

    def __init__(self, bot: Any):
        self.bot = bot
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        logbuf.install()
        app = create_app(self.bot)
        config = uvicorn.Config(
            app,
            host=settings.portal_host,
            port=settings.portal_port,
            log_level="warning",  # uvicorn logs are noisy, let the app log
            access_log=False,
            lifespan="off",
        )
        self._server = _Server(config)
        self._task = asyncio.create_task(self._server.serve())
        logger.info(
            "Portal running at http://%s:%d",
            settings.portal_host,
            settings.portal_port,
        )

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
