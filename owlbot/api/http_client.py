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

"""Shared HTTP client for Owlbot.

Provides a managed aiohttp ClientSession with convenience methods for
making HTTP requests. Used internally by OwncastClient and available
to modules via ``ctx.http``.
"""

import logging
from typing import Any

import aiohttp
import orjson

logger = logging.getLogger("owlbot.http")


class HttpClient:
    """Shared HTTP client for making web requests.

    Owns the underlying :class:`aiohttp.ClientSession` and exposes
    convenience methods that return aiohttp context managers::

        async with ctx.http.get("https://api.example.com/data") as resp:
            data = await resp.json()

    The lifecycle is managed by :class:`~owlbot.bot.Owlbot`:
    :meth:`_start` creates the session and :meth:`_close` tears it down.
    """

    def __init__(self) -> None:
        """Initialize the HTTP client (session is not created until :meth:`_start`)."""
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """The underlying aiohttp ClientSession.

        :raises RuntimeError: If the client has not been started yet.
        """
        if self._session is None:
            raise RuntimeError("HttpClient has not been started")
        return self._session

    async def _start(self) -> None:
        """Create the underlying aiohttp session.

        The User-Agent header is derived from the package version at
        call time so the import is deferred until the bot is actually
        starting up.
        """
        from .. import __version__

        connector = aiohttp.TCPConnector(keepalive_timeout=120)
        timeout = aiohttp.ClientTimeout(connect=10, sock_connect=10, sock_read=10)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            json_serialize=lambda obj: orjson.dumps(obj).decode(),
            headers={
                "User-Agent": (
                    f"Owlbot/{__version__} "
                    "(bot; +https://git.logal.dev/"
                    "LogalDeveloper/Owlbot)"
                ),
            },
        )
        logger.debug("HTTP client started.")

    async def _close(self) -> None:
        """Close the underlying session. Safe to call multiple times."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("HTTP client closed.")

    def get(self, url: str, **kwargs: Any) -> aiohttp.client._RequestContextManager:
        """Send a GET request.

        :param url: The URL to request.
        :param kwargs: Additional arguments passed to :meth:`aiohttp.ClientSession.get`.
        :return: An async context manager yielding a :class:`aiohttp.ClientResponse`.
        """
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> aiohttp.client._RequestContextManager:
        """Send a POST request.

        :param url: The URL to request.
        :param kwargs: Additional arguments passed to
            :meth:`aiohttp.ClientSession.post`.
        :return: An async context manager yielding a :class:`aiohttp.ClientResponse`.
        """
        return self.session.post(url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> aiohttp.client._RequestContextManager:
        """Send a PUT request.

        :param url: The URL to request.
        :param kwargs: Additional arguments passed to :meth:`aiohttp.ClientSession.put`.
        :return: An async context manager yielding a :class:`aiohttp.ClientResponse`.
        """
        return self.session.put(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> aiohttp.client._RequestContextManager:
        """Send a DELETE request.

        :param url: The URL to request.
        :param kwargs: Additional arguments passed to
            :meth:`aiohttp.ClientSession.delete`.
        :return: An async context manager yielding a :class:`aiohttp.ClientResponse`.
        """
        return self.session.delete(url, **kwargs)

    def request(
        self, method: str, url: str, **kwargs: Any
    ) -> aiohttp.client._RequestContextManager:
        """Send a request with an arbitrary HTTP method.

        :param method: The HTTP method (e.g., ``"PATCH"``).
        :param url: The URL to request.
        :param kwargs: Additional arguments passed to
            :meth:`aiohttp.ClientSession.request`.
        :return: An async context manager yielding a :class:`aiohttp.ClientResponse`.
        """
        return self.session.request(method, url, **kwargs)
