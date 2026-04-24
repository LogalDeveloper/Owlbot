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

"""Shared HTTP transport helpers for the Owncast API clients."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp
import orjson
from aiohttp import ContentTypeError

if TYPE_CHECKING:
    import logging

    from owlbot.api.http_client import HttpClient


class OwncastError(Exception):
    """Raised when an Owncast API request fails."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize the error.

        :param status: HTTP status code from the failed request, or 0 if the
            request failed due to a connection error before receiving a response.
        :param message: Error message or response body from the server.
        """
        self.status = status
        self.message = message
        super().__init__(f"Owncast error {status}: {message}")


def _extract_error(text: str) -> str:
    """Extract a human-readable error from an Owncast response body.

    Owncast may return ``{"error": "..."}`` or
    ``{"success": false, "message": "..."}``, or plain text.  This helper
    unwraps the JSON envelope when present.
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


async def request_with_envelope(
    http: HttpClient,
    base_url: str,
    endpoint: str,
    data: dict[str, Any] | aiohttp.FormData | None = None,
    *,
    logger: logging.Logger,
    auth: aiohttp.BasicAuth | None = None,
    headers: dict[str, str] | None = None,
    method: str = "POST",
) -> str:
    """Send a body-carrying request and unwrap the Owncast envelope.

    The Owncast envelope is the ``{"success", "message"}`` JSON object
    returned by most admin and integration endpoints.

    :param data: Request body. A ``dict`` is sent as JSON; an
        :class:`aiohttp.FormData` is sent as ``multipart/form-data`` with
        aiohttp picking the boundary. ``None`` sends no body.
    :param method: HTTP method to use (``POST`` by default; also ``PUT``,
        ``DELETE``, ``PATCH``).
    :raises OwncastError: If the request fails or the server reports failure.
    """
    url = f"{base_url}{endpoint}"
    logger.debug("%s %s", method, endpoint)
    request_kwargs: dict[str, Any] = (
        {"data": data} if isinstance(data, aiohttp.FormData) else {"json": data}
    )
    try:
        async with http.session.request(
            method,
            url,
            headers=headers,
            auth=auth,
            allow_redirects=False,
            **request_kwargs,
        ) as response:
            if response.status >= HTTPStatus.BAD_REQUEST:
                text = await response.text()
                message = _extract_error(text)
                logger.error(
                    "Error %d on %s %s: %s",
                    response.status,
                    method,
                    endpoint,
                    message,
                )
                raise OwncastError(response.status, message)
            logger.debug("%s %s -> %d", method, endpoint, response.status)
            if response.content_type == "application/json":
                try:
                    result = await response.json(loads=orjson.loads)
                except (ValueError, ContentTypeError):
                    text = await response.text()
                    logger.exception(
                        "Invalid JSON on %s %s: %s", method, endpoint, text
                    )
                    raise OwncastError(response.status, text) from None
                if isinstance(result, dict):
                    if "error" in result:
                        logger.error(
                            "Error on %s %s: %s", method, endpoint, result["error"]
                        )
                        raise OwncastError(response.status, result["error"])
                    if "success" in result and not result["success"]:
                        message = result.get("message", "unknown error")
                        logger.error("Error on %s %s: %s", method, endpoint, message)
                        raise OwncastError(response.status, message)
                    if "success" in result:
                        return str(result.get("message", ""))
                logger.warning(
                    "Unknown response on %s %s: %s", method, endpoint, result
                )
                return ""
            return ""
    except aiohttp.ClientError as e:
        logger.exception("Connection error on %s %s.", method, endpoint)
        raise OwncastError(0, str(e)) from e


async def get_text(
    http: HttpClient,
    base_url: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    logger: logging.Logger,
    auth: aiohttp.BasicAuth | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """Send a GET and return the raw response body as text.

    Used for endpoints that return non-JSON payloads (e.g. Prometheus metrics).

    :raises OwncastError: If the request fails.
    """
    url = f"{base_url}{endpoint}"
    logger.debug("GET %s", endpoint)
    try:
        async with http.session.get(
            url,
            params=params,
            headers=headers,
            auth=auth,
            allow_redirects=False,
        ) as response:
            if response.status >= HTTPStatus.BAD_REQUEST:
                text = await response.text()
                message = _extract_error(text)
                logger.error(
                    "Error %d on GET %s: %s", response.status, endpoint, message
                )
                raise OwncastError(response.status, message)
            logger.debug("GET %s -> %d", endpoint, response.status)
            return await response.text()
    except aiohttp.ClientError as e:
        logger.exception("Connection error on GET %s.", endpoint)
        raise OwncastError(0, str(e)) from e


async def get_json(
    http: HttpClient,
    base_url: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    logger: logging.Logger,
    auth: aiohttp.BasicAuth | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Send a GET and return the parsed JSON body.

    :raises OwncastError: If the request fails or the response cannot be parsed.
    """
    url = f"{base_url}{endpoint}"
    logger.debug("GET %s", endpoint)
    try:
        async with http.session.get(
            url,
            params=params,
            headers=headers,
            auth=auth,
            allow_redirects=False,
        ) as response:
            if response.status >= HTTPStatus.BAD_REQUEST:
                text = await response.text()
                message = _extract_error(text)
                logger.error(
                    "Error %d on GET %s: %s", response.status, endpoint, message
                )
                raise OwncastError(response.status, message)
            logger.debug("GET %s -> %d", endpoint, response.status)
            try:
                result = await response.json(loads=orjson.loads)
            except (ValueError, ContentTypeError):
                text = await response.text()
                logger.exception("Invalid JSON on GET %s: %s", endpoint, text)
                raise OwncastError(response.status, text) from None
            if isinstance(result, dict) and "error" in result:
                logger.error("Error on GET %s: %s", endpoint, result["error"])
                raise OwncastError(response.status, result["error"])
            return result
    except aiohttp.ClientError as e:
        logger.exception("Connection error on GET %s.", endpoint)
        raise OwncastError(0, str(e)) from e
