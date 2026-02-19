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

"""Main bot class that ties together the webhook server, modules, and Owncast API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

from . import OWNCAST_TARGET_VERSION, __version__
from .api.config import Config
from .api.http_client import HttpClient
from .api.owncast_client import OwncastError
from .http_server import HttpServer
from .module_loader import ModuleLoader

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("owlbot")


class StartupError(Exception):
    """Raised when a fatal error occurs during bot startup."""


class Owlbot:
    """The main Owlbot application."""

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        overrides: dict[str, Any] | None = None,
        skip_api_check: bool = False,
    ):
        """
        Initialize Owlbot.

        :param config_path: Path to the YAML config file.
        :param overrides: CLI overrides passed to the config manager.
        :param skip_api_check: Skip API accessibility checks during startup.
        """
        logger.info(f"Owlbot v{__version__} - A logal.dev project")

        self.skip_api_check = skip_api_check

        # Load config. Raises StartupError on missing/invalid config.
        try:
            self.config = Config(config_path, overrides=overrides)
        except (FileNotFoundError, ValueError, yaml.YAMLError, OSError) as e:
            raise StartupError(str(e)) from e

        self.modules_dir = self.config.modules_dir
        if not self.modules_dir.exists():
            logger.info(
                f"User modules directory not found: {self.modules_dir} "
                f"(only built-in modules will be loaded)"
            )

        # Shared HTTP client (owns the aiohttp session lifecycle).
        self.http_client = HttpClient()

        # Module loader (owns API clients, dispatchers, and module contexts).
        self.module_loader = ModuleLoader(
            self.modules_dir,
            self.config,
            self.http_client,
        )

        self.http_server = HttpServer(
            config=self.config,
            event_dispatch=self.module_loader.event_dispatcher.dispatch,
            route_dispatcher=self.module_loader.route_dispatcher,
        )

        logger.debug("Owlbot initialization complete.")

    async def __aenter__(self) -> Owlbot:
        """Start the bot as an async context manager."""
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Stop the bot when exiting the async context manager."""
        await self.stop()

    async def start(self) -> None:
        """Initialize the bot and start listening for webhooks.

        Returns once the bot is ready. The caller is responsible for
        keeping the event loop alive and calling :meth:`stop` when done.
        Prefer using Owlbot as an async context manager instead of
        calling ``start`` / ``stop`` manually.

        On failure, cleans up any partially initialized state before
        raising.

        :raises StartupError: If the bot fails to start (e.g., API unreachable).
        """
        logger.info("Starting Owlbot...")

        try:
            # Auth is handled per-request by each Owncast client.
            await self.http_client._start()

            if self.skip_api_check:
                logger.info("Skipping startup connection tests.")
                if self.module_loader.admin_client:
                    logger.info("Owncast admin API is enabled.")
            else:
                await self._check_api_accessibility()

            await self.module_loader.load_all_modules()
            await self.http_server.start(self.config.host, self.config.port)

            logger.info("Owlbot start complete.")

        except StartupError:
            await self.stop()
            raise
        except OSError as e:
            await self.stop()
            raise StartupError(
                f"Failed to start web server on "
                f"{self.config.host}:{self.config.port}: {e}"
            ) from e

    async def stop(self) -> None:
        """Gracefully shut down the bot, unloading all modules.

        Safe to call multiple times or on partially initialized state.
        """
        logger.info("Shutting down Owlbot...")

        # Stop accepting new webhooks.
        await self.http_server.stop()

        # Wait for in-flight event handlers to finish.
        await self.http_server.drain()

        # Unload modules now that all handlers have completed.
        await self.module_loader.unload_all_modules()

        # Close the shared HTTP client (safe to call if startup failed early).
        await self.http_client._close()

        logger.info("Owlbot shutdown complete.")

    async def _check_api_accessibility(self) -> None:
        """
        Verify that the Owncast APIs are reachable before proceeding with startup.

        Calls a non-transformative endpoint on each configured client to confirm
        the server is accessible and credentials are valid. Raises
        :class:`StartupError` if any check fails.
        """
        owncast_client = self.module_loader.owncast_client
        admin_client = self.module_loader.admin_client

        base_url = owncast_client.base_url

        logger.debug("Checking Owncast server version...")
        try:
            status = await owncast_client.get_status()
        except OwncastError as e:
            if e.status == 0:
                logger.error(
                    "Startup connection test failed: "
                    f"Could not connect to {base_url}: {e.message}"
                )
                raise StartupError(
                    f"Could not connect to {base_url}: {e.message}"
                ) from e
            logger.error(
                "Startup connection test failed: "
                f"{base_url} returned HTTP {e.status}: {e.message}"
            )
            raise StartupError(
                "Owncast version check failed: "
                f"{base_url} returned HTTP {e.status}: {e.message}"
            ) from e

        version = status.get("versionNumber") if isinstance(status, dict) else None
        if not version:
            logger.error(
                f"Startup connection test failed: {base_url} "
                "does not appear to be an Owncast instance "
                "(no versionNumber in response)"
            )
            raise StartupError(
                f"{base_url} does not appear to be an Owncast instance "
                "(no versionNumber in response)"
            )

        # Warn on version mismatch but continue startup.
        if version != OWNCAST_TARGET_VERSION:
            logger.warning(
                f"Owncast version {version} detected at {base_url}, "
                f"but this build of Owlbot is designed for {OWNCAST_TARGET_VERSION}. "
                "Startup will continue, but some things may not behave as expected."
            )
        else:
            logger.info(f"Owncast version {version} detected at {base_url}.")

        logger.debug("Checking Owncast integrations API accessibility...")
        try:
            await owncast_client.get_connected_clients()
            logger.debug("Owncast integrations API is accessible.")
        except OwncastError as e:
            if e.status == 0:
                logger.error(
                    "Startup connection test failed: Could not "
                    f"connect to integrations API: {e.message}"
                )
                raise StartupError(
                    f"Could not connect to integrations API: {e.message}"
                ) from e
            logger.error(
                "Startup connection test failed: "
                "Integrations API returned "
                f"HTTP {e.status}: {e.message}"
            )
            raise StartupError(
                "Owncast integrations API check failed: "
                f"returned HTTP {e.status}: {e.message}"
            ) from e

        if admin_client:
            logger.debug("Checking Owncast admin API accessibility...")
            try:
                await admin_client.get_status()
                logger.info("Owncast admin API is enabled.")
            except OwncastError as e:
                if e.status == 0:
                    logger.error(
                        "Startup connection test failed: Could "
                        f"not connect to admin API: {e.message}"
                    )
                    raise StartupError(
                        f"Could not connect to admin API: {e.message}"
                    ) from e
                logger.error(
                    "Startup connection test failed: "
                    "Admin API returned "
                    f"HTTP {e.status}: {e.message}"
                )
                raise StartupError(
                    "Owncast admin API check failed: "
                    f"returned HTTP {e.status}: {e.message}"
                ) from e
