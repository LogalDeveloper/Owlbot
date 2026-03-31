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

"""Emoji wall module for Owlbot.

Listens to chat events, extracts Unicode emojis and custom Owncast emotes,
and broadcasts them via Server-Sent Events (SSE) to connected browser sources
for rendering as floating animations in an OBS overlay.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

import emoji
from aiohttp import web

if TYPE_CHECKING:
    import logging

from owlbot.api import (
    ChatEvent,
    EventContext,
    EventType,
    ModuleContext,
    RouteContext,
    on_event,
    on_route,
    on_setup,
    on_teardown,
)

__all__ = [
    "events_stream",
    "on_chat",
    "setup",
    "static_wall_js",
    "teardown",
    "wall_page",
]


class _EmoteParser(HTMLParser):
    """HTML parser that extracts ``src`` from ``<img>`` tags with an emoji class."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Collect src from img tags whose class is exactly ``emoji``."""
        if tag != "img":
            return
        attr_dict = dict(attrs)
        src = attr_dict.get("src")
        if attr_dict.get("class") == "emoji" and src:
            self.urls.append(src)


_WALL_JS_PATH: Path = Path(__file__).resolve().parent / "static" / "wall.js"

_DIRECTION_PRESETS: dict[str, int] = {
    "up": 0,
    "up-right": 45,
    "right": 90,
    "down-right": 135,
    "down": 180,
    "down-left": 225,
    "left": 270,
    "up-left": 315,
}


def _resolve_direction(value: str | int) -> int | str:
    """Resolve a direction value to a degree integer or ``"random"``.

    Accepts named presets (case-insensitive), the string ``"random"``,
    or a numeric degree value (0-359). Invalid values fall back to 0.

    :param value: Direction preset name, ``"random"``, or degree value.
    :return: Integer degrees (0-359) or the string ``"random"``.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "random":
            return "random"
        if normalized in _DIRECTION_PRESETS:
            return _DIRECTION_PRESETS[normalized]
        try:
            value = int(normalized)
        except ValueError:
            return 0
    return max(0, min(359, int(value)))


def _resolve_settings(
    config: dict[str, object],
    query: dict[str, str],
) -> dict[str, int | str]:
    """Build a validated settings dict from config values and query overrides.

    For each known setting key, the query parameter value (if present and
    parseable) takes precedence over the config value. All numeric values
    are clamped to their allowed ranges. Min/max pairs are swapped if
    inverted.

    :param config: Module config dict from ``ctx.config.as_dict()``.
    :param query: Query parameters from the HTTP request.
    :return: Validated settings dict ready for template injection.
    """

    def _int(key: str) -> int:
        if key in query:
            try:
                return int(query[key])
            except (ValueError, TypeError):
                pass
        try:
            return int(str(config[key]))
        except (ValueError, TypeError, KeyError):
            return 0

    min_size = max(1, min(500, _int("min_size")))
    max_size = max(1, min(500, _int("max_size")))
    if min_size > max_size:
        min_size, max_size = max_size, min_size

    min_duration = max(1, min(60, _int("min_duration")))
    max_duration = max(1, min(60, _int("max_duration")))
    if min_duration > max_duration:
        min_duration, max_duration = max_duration, min_duration

    raw_direction = query.get("direction") or str(config.get("direction", "up"))

    return {
        "min_size": min_size,
        "max_size": max_size,
        "min_duration": min_duration,
        "max_duration": max_duration,
        "max_count": max(1, min(1000, _int("max_count"))),
        "direction": _resolve_direction(raw_direction),
        "max_rotation": max(0, min(360, _int("max_rotation"))),
    }


def _extract_emojis(
    raw_body: str, html_body: str, owncast_url: str
) -> list[dict[str, str]]:
    """Extract Unicode emojis and custom Owncast emotes from a chat message.

    Unicode emojis are detected from the raw body (original user input)
    to avoid matching emojis inside HTML attributes. Custom emotes are
    extracted from the rendered HTML body, which contains ``<img>`` tags
    with a class of ``emoji``.

    :param raw_body: The plain-text message body.
    :param html_body: The rendered HTML message body from the chat event.
    :param owncast_url: The base URL of the Owncast server.
    :return: List of SSE event dicts ready for broadcast.
    """
    events: list[dict[str, str]] = [
        {"type": "unicode", "emoji": e}
        for e in (item["emoji"] for item in emoji.emoji_list(raw_body))
    ]
    parser = _EmoteParser()
    parser.feed(html_body)
    events.extend(
        {"type": "emote", "url": f"{owncast_url}/{src.lstrip('/')}"}
        for src in parser.urls
    )
    return events


async def _broadcast(
    clients: set[web.StreamResponse],
    events: list[dict[str, str]],
    logger: logging.Logger,
) -> None:
    """Broadcast SSE events to all connected clients.

    Serializes each event as JSON, concatenates them into a single
    payload, and writes it to every client in one call. Clients
    that fail to receive the message are removed from the set.

    :param clients: The set of connected SSE stream responses.
    :param events: The event data dicts to broadcast.
    :param logger: Logger instance from the module context.
    """
    payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
    snapshot = list(clients)
    results = await asyncio.gather(
        *(client.write(payload) for client in snapshot),
        return_exceptions=True,
    )
    failed = 0
    for client, result in zip(snapshot, results, strict=True):
        if isinstance(result, Exception):
            clients.discard(client)
            failed += 1
    if failed:
        logger.debug("Removed %d failed client(s) during broadcast.", failed)


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the emoji wall module.

    Registers config defaults, generates token if needed,
    and creates the SSE client tracking set.

    :param ctx: Module context with config, storage, and other services.
    """
    ctx.config.register_defaults(
        {
            "min_size": 20,
            "max_size": 60,
            "min_duration": 3,
            "max_duration": 10,
            "max_count": 50,
            "direction": "up",
            "max_rotation": 25,
        }
    )
    ctx.state["clients"] = set[web.StreamResponse]()

    token = ctx.config.get("token")
    if not token:
        token = secrets.token_urlsafe(32)
        ctx.config.set("token", token)
        ctx.logger.info("Generated emoji wall token and saved to config.")

    ctx.state["token"] = token
    overlay_url = ctx.routes.url_for(f"/{token}")
    ctx.logger.info("Emoji wall overlay URL: %s", overlay_url)


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the emoji wall module.

    Closes all connected SSE clients and clears the client set.

    :param ctx: Module context.
    """
    clients: set[web.StreamResponse] = ctx.state["clients"]
    ctx.logger.debug("Closing %d SSE client(s).", len(clients))
    for client in clients:
        with contextlib.suppress(Exception):
            await client.write_eof()
    clients.clear()
    ctx.logger.info("Emoji wall module cleaned up.")


@on_event(EventType.CHAT)
async def on_chat(ctx: EventContext[ChatEvent]) -> None:
    """Handle chat events by extracting emojis and broadcasting them via SSE.

    Extracts both Unicode emojis and custom Owncast emotes from the chat
    message and sends each one as an SSE event to all connected overlay
    clients.

    :param ctx: The event context containing the chat event.
    """
    clients: set[web.StreamResponse] = ctx.module.state["clients"]
    if not clients:
        ctx.module.logger.debug(
            "Skipping chat %s: no SSE clients connected.",
            ctx.event.message_id,
        )
        return

    owncast_url: str = ctx.module.owncast_client.base_url
    events = _extract_emojis(ctx.event.raw_body, ctx.event.body, owncast_url)

    if events:
        ctx.module.logger.debug(
            "Broadcasting %d emoji(s) from %s to %d client(s).",
            len(events),
            ctx.event.user.display_name,
            len(clients),
        )
        await _broadcast(clients, events, ctx.module.logger)


@on_route("/{token}")
async def wall_page(ctx: RouteContext) -> web.Response:
    """Serve the emoji wall overlay page.

    Validates the token and serves the overlay HTML. Invalid tokens return 401.

    :param ctx: The route context.
    :return: HTML response for the overlay page, or 401 if token is invalid.
    """
    expected_token = ctx.module.state.get("token")
    if not secrets.compare_digest(
        ctx.match_info.get("token", ""), expected_token or ""
    ):
        ctx.logger.debug("Wall page rejected: invalid token.")
        return web.Response(
            status=401,
            text="Invalid token. Check the Owlbot log for the correct emoji wall URL.",
        )

    events_url = ctx.routes.url_for(f"/{expected_token}/events")
    js_url = ctx.routes.url_for("/static/wall.js")
    settings = _resolve_settings(ctx.config.as_dict(), dict(ctx.request.query))

    ctx.logger.debug("Serving wall page with settings: %s.", settings)
    page = ctx.templates.render(
        "wall.html",
        events_url=events_url,
        js_url=js_url,
        settings=settings,
    )
    return web.Response(text=page, content_type="text/html")


@on_route("/{token}/events", streaming=True)
async def events_stream(ctx: RouteContext) -> web.StreamResponse | web.Response:
    """Server-Sent Events endpoint for emoji wall updates.

    Validates the token and establishes a persistent SSE connection.
    Sends keepalive comments every 15 seconds to prevent proxy timeouts.

    :param ctx: The route context.
    :return: SSE stream response, or 401 if token is invalid.
    """
    expected_token = ctx.module.state.get("token")
    if not secrets.compare_digest(
        ctx.match_info.get("token", ""), expected_token or ""
    ):
        ctx.logger.debug("SSE stream rejected: invalid token.")
        return web.Response(
            status=401,
            text="Invalid token. Check the Owlbot log for the correct emoji wall URL.",
        )

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(ctx.request)

    clients: set[web.StreamResponse] = ctx.module.state["clients"]
    clients.add(response)
    ctx.logger.debug("SSE client connected (%d total).", len(clients))

    try:
        while True:
            await asyncio.sleep(15)
            try:
                await response.write(b": keepalive\n\n")
            except Exception:
                ctx.logger.debug("SSE keepalive failed, disconnecting client.")
                break
    finally:
        clients.discard(response)
        ctx.logger.debug("SSE client disconnected (%d remaining).", len(clients))

    return response


@on_route("/static/wall.js", methods=["GET"])
async def static_wall_js(ctx: RouteContext) -> web.FileResponse:
    """Serve the emoji wall JavaScript file.

    :param ctx: The route context.
    :return: The wall.js file with caching headers.
    """
    return web.FileResponse(
        _WALL_JS_PATH,
        headers={"Cache-Control": "max-age=86400"},
    )
