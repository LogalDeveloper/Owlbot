# Copyright 2026 Logan Fick
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP server for Owlbot."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from aiohttp import web

from . import __version__
from .api.event_types import Event, EventType, parse_event

if TYPE_CHECKING:
    from .api.config import Config
    from .registries.routes import RouteDispatcher

logger = logging.getLogger("owlbot.web")


async def _on_response_prepare(
    request: web.Request, response: web.StreamResponse
) -> None:
    """Set the Server header on all responses before headers are sent."""
    response.headers["Server"] = f"Owlbot/{__version__}"
    if request.path.startswith("/owlbot/static/"):
        response.headers["Cache-Control"] = "max-age=86400"


# Callback type for webhook dispatch (injected from Owlbot).
type WebhookCallback = Callable[[EventType, Event], Coroutine[Any, Any, None]]


class HttpServer:
    """HTTP server for Owlbot.

    Manages the aiohttp Application, built-in routes (webhook),
    and module-registered routes via the RouteDispatcher.
    """

    def __init__(
        self,
        config: Config,
        event_dispatch: WebhookCallback,
        route_dispatcher: RouteDispatcher,
    ):
        """Initialize the web server.

        :param config: Bot configuration.
        :param event_dispatch: Async callback to dispatch webhook events.
        :param route_dispatcher: Dispatcher for module HTTP routes.
        """
        self.config = config
        self._event_dispatch = event_dispatch
        self._route_dispatcher = route_dispatcher

        # Pending webhook dispatch tasks (tracked so drain() can await them).
        self._pending_tasks: set[asyncio.Task[None]] = set()

        # Runner is created by start() and cleaned up by stop().
        self._runner: web.AppRunner | None = None

        self.app = web.Application()
        self.app.on_response_prepare.append(_on_response_prepare)
        self.app.on_shutdown.append(self._on_shutdown)
        self.app.router.add_post(self.config.webhook_path, self._handle_webhook)

        # Static assets served from the owlbot package.
        static_dir = Path(__file__).resolve().parent / "static"
        if static_dir.is_dir():
            self.app.router.add_static("/owlbot/static", static_dir)

        # Catch-all routes for dynamic module route dispatch.
        # These single aiohttp routes handle all requests under /owlbot/ and
        # dispatch them to the appropriate handler via RouteDispatcher lookup
        # at request time, enabling modules to register and unregister routes
        # dynamically without restarting the server.
        self.app.router.add_route(
            "*", "/owlbot/{module_name}/{path:.*}", self._handle_catch_all
        )
        self.app.router.add_route("*", "/owlbot/{module_name}", self._handle_catch_all)

        logger.debug("HttpServer initialized.")

    async def start(self, host: str, port: int) -> None:
        """Start the HTTP server.

        :param host: Address to bind to.
        :param port: Port to bind to.
        """
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, host, port)
        logger.debug("Binding web server to %s:%s...", host, port)
        try:
            await site.start()
        except OSError as e:
            logger.error(f"Failed to bind web server to {host}:{port}: {e}")
            raise

        logger.info(f"Web server started at: http://{host}:{port}")

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("Web server stopped.")

    async def drain(self) -> None:
        """Wait for all pending webhook dispatch tasks to complete."""
        if self._pending_tasks:
            logger.info(
                f"Waiting for {len(self._pending_tasks)} "
                "pending event(s) to complete..."
            )
            await asyncio.gather(*self._pending_tasks)
            logger.debug("All pending events drained.")

    async def _on_shutdown(self, _app: web.Application) -> None:
        """Drain active route handlers so connections close promptly."""
        await self._route_dispatcher.drain_handlers()

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook requests from Owncast."""
        try:
            data = await request.json(loads=orjson.loads)
        except ValueError as e:
            # Invalid JSON received. This shouldn't happen
            # with legitimate Owncast webhooks.
            logger.warning(f"Failed to parse webhook JSON: {e}")
            return web.Response(status=400)

        event_type = data.get("type", "unknown")
        logger.debug("Received webhook: %s", event_type)

        result = parse_event(data)
        if result is None:
            # Owncast may have added a new webhook we don't handle yet.
            logger.warning(
                f"Owncast sent unrecognized event type: {event_type!r}. Payload: {data}"
            )
            return web.Response(status=400)

        parsed_type, event = result

        # Dispatch the event to handlers in a background task so
        # we can respond immediately. This avoids the possibility
        # of slow handlers blocking Owncast's webhook delivery.
        task: asyncio.Task[None] = asyncio.create_task(
            self._event_dispatch(parsed_type, event),
            name=f"Event dispatch - {parsed_type.value}",
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

        return web.Response(status=202)

    async def _handle_catch_all(self, request: web.Request) -> web.StreamResponse:
        """Delegate module route requests to the RouteDispatcher."""
        return await self._route_dispatcher.dispatch(request)
