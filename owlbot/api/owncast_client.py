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

"""Async HTTP client for the Owncast Integration API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from markupsafe import escape as _escape_html

from owlbot.owncast_http import get_json, request_with_envelope

if TYPE_CHECKING:
    import aiohttp

    from .http_client import HttpClient


class OwncastClient:
    """Async client for the Owncast Integration API.

    Uses a shared :class:`~owlbot.api.http_client.HttpClient` for HTTP
    transport, with per-request Bearer token authentication.
    """

    def __init__(
        self, base_url: str, access_token: str, http_client: HttpClient
    ) -> None:
        """Initialize the Owncast client.

        :param base_url: The Owncast server URL (e.g., "https://stream.logal.dev").
        :param access_token: API access token from Owncast admin settings.
        :param http_client: Shared HTTP client for making requests.
        """
        self._base_url = base_url.rstrip("/")
        self._http = http_client
        self._logger = logging.getLogger("owlbot.owncast_client")
        self._headers: dict[str, str] | None = {
            "Authorization": f"Bearer {access_token}"
        }
        self._auth: aiohttp.BasicAuth | None = None
        self._logger.debug("Owncast API client initialized for: %s", self._base_url)

    @property
    def base_url(self) -> str:
        """The Owncast server base URL (e.g., "https://stream.logal.dev")."""
        return self._base_url

    async def send_system_message(self, body: str, *, unsanitized: bool = False) -> str:
        """Send a system message visible to all viewers.

        System messages are styled differently from regular chat (typically
        italicized or dimmed) and are used for announcements or notifications.

        The message body is HTML-escaped by default. Pass
        ``unsanitized=True`` to send raw HTML intentionally.

        :param body: The message text.
        :param unsanitized: If ``True``, skip HTML escaping.
        :return: Success message from the server.
        """
        if not unsanitized:
            body = str(_escape_html(body))
        self._logger.info("Sending system message: %s", body)
        return await self._post("/api/integrations/chat/system", {"body": body})

    async def send_system_message_to_client(
        self, client_id: int, body: str, *, unsanitized: bool = False
    ) -> str:
        """Send a private system message to a specific viewer.

        The message is only visible to the targeted client, useful for
        welcome messages or private notifications.

        The message body is HTML-escaped by default. Pass
        ``unsanitized=True`` to send raw HTML intentionally.

        :param client_id: The numeric client ID (from event.client_id).
        :param body: The message text.
        :param unsanitized: If ``True``, skip HTML escaping.
        :return: Success message from the server.
        """
        if not unsanitized:
            body = str(_escape_html(body))
        self._logger.info("Sending system message to client %d: %s", client_id, body)
        return await self._post(
            f"/api/integrations/chat/system/client/{client_id}", {"body": body}
        )

    async def send_user_message(self) -> str:
        """Send a chat message on behalf of a user (deprecated by Owncast).

        Owncast no longer supports this endpoint as of v0.2.5: it always returns
        HTTP 400 with a message directing callers to :meth:`send_message`. The
        wrapper is provided for spec parity. Use :meth:`send_message` instead.

        :return: Success message from the server (never returned in practice).
        :raises OwncastError: Always, with status 400.
        """
        self._logger.info("Calling deprecated /integrations/chat/user endpoint.")
        return await self._post("/api/integrations/chat/user")

    async def send_message(self, body: str, *, unsanitized: bool = False) -> str:
        """Send a chat message visible to all viewers.

        The message body is HTML-escaped by default to prevent injection
        when echoing user-originated content. Markdown syntax is unaffected
        by escaping and will be rendered normally by Owncast.

        :param body: The message text (supports markdown).
        :param unsanitized: If ``True``, skip HTML escaping and send the body
            as-is. Only use this when the content is fully controlled by the
            module and intentionally contains HTML.
        :return: Success message from the server.
        """
        if not unsanitized:
            body = str(_escape_html(body))
        self._logger.info("Sending chat message: %s", body)
        return await self._post("/api/integrations/chat/send", {"body": body})

    async def send_action(self, body: str, *, unsanitized: bool = False) -> str:
        """Send an action message (like IRC /me).

        Action messages display as "*BotName does something*" and are used
        for describing actions rather than speech.

        The message body is HTML-escaped by default. Pass
        ``unsanitized=True`` to send raw HTML intentionally.

        :param body: The action text (displayed after the bot name).
        :param unsanitized: If ``True``, skip HTML escaping.
        :return: Success message from the server.
        """
        if not unsanitized:
            body = str(_escape_html(body))
        self._logger.info("Sending action: %s", body)
        return await self._post("/api/integrations/chat/action", {"body": body})

    async def set_message_visibility(
        self, message_ids: list[str], *, visible: bool
    ) -> str:
        """Hide or show chat messages (moderation).

        Hidden messages are removed from the chat display for all viewers.
        This is typically used for moderation purposes.

        :param message_ids: List of message IDs to modify.
        :param visible: True to show messages, False to hide them.
        :return: Success message from the server.
        """
        action = "Showing" if visible else "Hiding"
        ids = ", ".join(message_ids)
        self._logger.info("%s %d message(s): %s", action, len(message_ids), ids)
        return await self._post(
            "/api/integrations/chat/messagevisibility",
            {"idArray": message_ids, "visible": visible},
        )

    async def get_status(self) -> dict[str, Any]:
        """Get the public server status.

        This is a public endpoint that does not require authentication.
        Returns server info including version, online status, and viewer count.

        :return: Status dict with ``versionNumber``, ``online``, ``viewerCount``, etc.
        """
        self._logger.debug("Fetching server status.")
        return dict(await self._get("/api/status"))

    async def set_stream_title(self, title: str) -> str:
        """Update the stream title.

        :param title: The new stream title.
        :return: Success message from the server.
        """
        self._logger.info("Setting stream title: %s", title)
        return await self._post("/api/integrations/streamtitle", {"value": title})

    async def get_chat_history(self) -> list[dict[str, Any]]:
        """Fetch recent chat messages.

        :return: List of recent chat message objects with user info and content.
        """
        self._logger.debug("Fetching chat history.")
        return list(await self._get("/api/integrations/chat"))

    async def get_connected_clients(self) -> list[dict[str, Any]]:
        """Get list of currently connected viewers.

        :return: List of connected client objects with user info and connection details.
        """
        self._logger.debug("Fetching connected clients.")
        return list(await self._get("/api/integrations/clients"))

    async def get_user_details(self, user_id: str) -> dict[str, Any]:
        """Get details for a chat user.

        :param user_id: The user ID to look up.
        :return: Dict with ``user``, ``connectedClients``, and ``messages`` fields.
        """
        self._logger.debug("Fetching user details for %s.", user_id)
        return dict(
            await self._get(f"/api/integrations/moderation/chat/user/{user_id}")
        )

    async def _post(self, endpoint: str, data: dict[str, Any] | None = None) -> str:
        """Send a POST request to the Owncast API.

        :raises OwncastError: If the request fails.
        """
        return await request_with_envelope(
            self._http,
            self._base_url,
            endpoint,
            data,
            logger=self._logger,
            headers=self._headers,
            auth=self._auth,
        )

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request to the Owncast API.

        :raises OwncastError: If the request fails.
        """
        return await get_json(
            self._http,
            self._base_url,
            endpoint,
            params,
            logger=self._logger,
            headers=self._headers,
            auth=self._auth,
        )
