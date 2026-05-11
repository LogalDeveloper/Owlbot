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

"""Server-Sent Event connection tracking for the polls module."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import orjson

if TYPE_CHECKING:
    from logging import Logger

    from aiohttp import web

    from owlbot.api import User


class SSEEvent(StrEnum):
    """Server-Sent Event types for the polls module."""

    KEEPALIVE = "keepalive"
    POLL_CANCELLED = "poll_cancelled"
    POLL_ENDED = "poll_ended"
    TALLY = "tally"


def sse_payload(event_type: SSEEvent, data: dict[str, Any]) -> bytes:
    """Serialize an SSE event into wire format.

    :param event_type: The event type name.
    :param data: The JSON-serializable event data.
    :return: Encoded SSE frame ready for ``StreamResponse.write()``.
    """
    return b"event: " + event_type.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"


@dataclass(slots=True)
class PollSSEConnection:
    """Runtime metadata for one connected poll event stream.

    :param id: Internal connection ID.
    :param user_id: Owncast user ID associated with the stream.
    :param is_moderator: Whether the connected viewer has moderator privileges.
    :param response: Prepared response used for SSE writes.
    :param write_lock: Per-connection lock that serializes writes.
    """

    id: int
    user_id: str
    is_moderator: bool
    response: web.StreamResponse
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PollSSERegistry:
    """Maintain live poll SSE connections and serialize writes per client."""

    def __init__(self, logger: Logger) -> None:
        """Initialize an empty SSE connection registry.

        :param logger: Logger for connection lifecycle diagnostics.
        """
        self._logger = logger
        self._connection_ids = itertools.count()
        self._connections: dict[int, PollSSEConnection] = {}

    def __len__(self) -> int:
        """Return the number of currently tracked connections.

        :return: Active SSE connection count.
        """
        return len(self._connections)

    def register(
        self,
        user: User,
        *,
        response: web.StreamResponse,
    ) -> PollSSEConnection:
        """Register an SSE client and return its connection.

        :param user: Viewer associated with the event stream.
        :param response: Prepared streaming response for the client.
        :return: Registered connection metadata.
        """
        connection = PollSSEConnection(
            id=next(self._connection_ids),
            user_id=user.id,
            is_moderator=user.is_moderator,
            response=response,
        )
        self._connections[connection.id] = connection
        return connection

    def unregister(self, connection: PollSSEConnection) -> None:
        """Remove one SSE connection.

        :param connection: Connection to remove from the registry.
        """
        removed = self._connections.pop(connection.id, None)
        if removed is not None:
            self._logger.debug(
                "SSE client disconnected: %s (%d remaining).",
                removed.user_id,
                len(self._connections),
            )

    async def send(
        self,
        connection: PollSSEConnection,
        event_type: SSEEvent,
        data: dict[str, Any],
    ) -> bool:
        """Send one event to one connection, removing it if the write fails.

        :param connection: Target SSE connection.
        :param event_type: Event type to send.
        :param data: JSON-serializable event payload.
        :return: True if the event was written successfully.
        """
        if self._connections.get(connection.id) is not connection:
            return False

        try:
            await self._write_connection(connection, sse_payload(event_type, data))
        except Exception:  # noqa: BLE001  # transport failures vary by server/client state
            self._connections.pop(connection.id, None)
            self._logger.debug(
                "Removing disconnected SSE client: %s", connection.user_id
            )
            return False
        return True

    async def broadcast(
        self,
        event_type: SSEEvent,
        data: dict[str, Any],
        *,
        moderators_only: bool = False,
    ) -> None:
        """Broadcast one event to currently tracked connections.

        :param event_type: Event type to send.
        :param data: JSON-serializable event payload.
        :param moderators_only: Whether to limit delivery to moderator clients.
        """
        targets = [
            connection
            for connection in self._connections.values()
            if not moderators_only or connection.is_moderator
        ]
        if not targets:
            return

        payload = sse_payload(event_type, data)
        results = await asyncio.gather(
            *(self._write_connection(connection, payload) for connection in targets),
            return_exceptions=True,
        )
        for connection, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                self._logger.debug(
                    "Removing disconnected SSE client: %s", connection.user_id
                )
                self._connections.pop(connection.id, None)

    async def close_all(self) -> None:
        """Close and forget every tracked SSE response."""
        connections = list(self._connections.values())
        for connection in connections:
            with contextlib.suppress(Exception):
                async with connection.write_lock:
                    await connection.response.write_eof()
        self._connections.clear()

    async def _write_connection(
        self,
        connection: PollSSEConnection,
        payload: bytes,
    ) -> None:
        """Write one encoded SSE frame while holding the connection lock.

        :param connection: Target SSE connection.
        :param payload: Encoded SSE frame.
        """
        async with connection.write_lock:
            await connection.response.write(payload)
