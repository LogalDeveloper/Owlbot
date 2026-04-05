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

import aiohttp
import orjson
from aiohttp import ContentTypeError
from markupsafe import escape as _escape_html

if TYPE_CHECKING:
    from .http_client import HttpClient


def _extract_error(text: str) -> str:
    """Extract a human-readable error from a response body.

    Owncast may return ``{"error": "..."}`` or ``{"success": false, "message": "..."}``,
    or plain text.  This helper unwraps the JSON envelope when present.

    :param text: Raw response body.
    :return: The extracted error string.
    """
    try:
        data = orjson.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(data, dict):
        if "error" in data:
            return str(data["error"])
        if "message" in data:
            return str(data["message"])
    return text


class OwncastError(Exception):
    """Raised when an Owncast API request fails."""

    def __init__(self, status: int, message: str):
        """Initialize the error.

        :param status: HTTP status code from the failed request, or 0 if the
            request failed due to a connection error before receiving a response.
        :param message: Error message or response body from the server.
        """
        self.status = status
        self.message = message
        super().__init__(f"Owncast error {status}: {message}")


class OwncastClient:
    """Async client for the Owncast Integration API.

    Uses a shared :class:`~owlbot.api.http_client.HttpClient` for HTTP
    transport, with per-request Bearer token authentication.
    """

    def __init__(self, base_url: str, access_token: str, http_client: HttpClient):
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

    async def get_status(self) -> dict[str, Any]:
        """Get the public server status.

        This is a public endpoint that does not require authentication.
        Returns server info including version, online status, and viewer count.

        :return: Status dict with ``versionNumber``, ``online``, ``viewerCount``, etc.
        """
        self._logger.debug("Fetching server status.")
        return dict(await self._get("/api/status"))

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
        self._logger.info(f"Sending chat message: {body}")
        return await self._post("/api/integrations/chat/send", {"body": body})

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
        self._logger.info(f"Sending system message: {body}")
        return await self._post("/api/integrations/chat/system", {"body": body})

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
        self._logger.info(f"Sending action: {body}")
        return await self._post("/api/integrations/chat/action", {"body": body})

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
        self._logger.info(f"Sending system message to client {client_id}: {body}")
        return await self._post(
            f"/api/integrations/chat/system/client/{client_id}", {"body": body}
        )

    async def set_message_visibility(
        self, message_ids: list[str], visible: bool
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
        self._logger.info(f"{action} {len(message_ids)} message(s): {ids}")
        return await self._post(
            "/api/integrations/chat/messagevisibility",
            {"idArray": message_ids, "visible": visible},
        )

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

    async def set_stream_title(self, title: str) -> str:
        """Update the stream title.

        :param title: The new stream title.
        :return: Success message from the server.
        """
        self._logger.info(f"Setting stream title: {title}")
        return await self._post("/api/integrations/streamtitle", {"value": title})

    async def _post(self, endpoint: str, data: dict[str, Any] | None = None) -> str:
        """Send a POST request to the Owncast API.

        Owncast POST endpoints return ``{"success": true, "message": "..."}``.
        This method validates the response and returns just the message string.

        :param endpoint: The API endpoint path.
        :param data: Optional JSON body to send.
        :return: The success message string from the response.
        :raises OwncastError: If the request fails.
        """
        url = f"{self._base_url}{endpoint}"
        self._logger.debug("POST %s", endpoint)
        try:
            async with self._http.session.post(
                url,
                json=data,
                headers=self._headers,
                auth=self._auth,
                allow_redirects=False,
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    message = _extract_error(text)
                    self._logger.error(
                        f"Error {response.status} on POST {endpoint}: {message}"
                    )
                    raise OwncastError(response.status, message)
                self._logger.debug("POST %s -> %d", endpoint, response.status)
                if response.content_type == "application/json":
                    try:
                        result = await response.json(loads=orjson.loads)
                    except (ValueError, ContentTypeError):
                        text = await response.text()
                        self._logger.error(f"Invalid JSON on POST {endpoint}: {text}")
                        raise OwncastError(response.status, text) from None
                    if isinstance(result, dict):
                        # Is there an error field? Owncast returns
                        # {"error": "..."} for internal errors.
                        if "error" in result:
                            self._logger.error(
                                f"Error on POST {endpoint}: {result['error']}"
                            )
                            raise OwncastError(response.status, result["error"])
                        # Does the success flag indicate failure?
                        if "success" in result and not result["success"]:
                            message = result.get("message", "unknown error")
                            self._logger.error(f"Error on POST {endpoint}: {message}")
                            raise OwncastError(response.status, message)
                        # Is this a simple success response? Extract the message.
                        if "success" in result:
                            return str(result.get("message", ""))
                    # Unknown response shape from Owncast.
                    self._logger.warning(
                        f"Unknown response on POST {endpoint}: {result}"
                    )
                    return ""
                return ""
        except aiohttp.ClientError as e:
            self._logger.error(f"Connection error on POST {endpoint}: {e}")
            raise OwncastError(0, str(e)) from e

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request to the Owncast API.

        :param endpoint: The API endpoint path.
        :param params: Optional query parameters.
        :return: The JSON response.
        :raises OwncastError: If the request fails.
        """
        url = f"{self._base_url}{endpoint}"
        self._logger.debug("GET %s", endpoint)
        try:
            async with self._http.session.get(
                url,
                params=params,
                headers=self._headers,
                auth=self._auth,
                allow_redirects=False,
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    message = _extract_error(text)
                    self._logger.error(
                        f"Error {response.status} on GET {endpoint}: {message}"
                    )
                    raise OwncastError(response.status, message)
                self._logger.debug("GET %s -> %d", endpoint, response.status)
                try:
                    result = await response.json(loads=orjson.loads)
                except (ValueError, ContentTypeError):
                    text = await response.text()
                    self._logger.error(f"Invalid JSON on GET {endpoint}: {text}")
                    raise OwncastError(response.status, text) from None
                # Is there an error field? Owncast returns
                # {"error": "..."} for internal errors.
                if isinstance(result, dict) and "error" in result:
                    self._logger.error(f"Error on GET {endpoint}: {result['error']}")
                    raise OwncastError(response.status, result["error"])
                return result
        except aiohttp.ClientError as e:
            self._logger.error(f"Connection error on GET {endpoint}: {e}")
            raise OwncastError(0, str(e)) from e
