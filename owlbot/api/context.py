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

"""Context objects for Owlbot module handlers.

This module provides the context objects that handlers receive:
- ModuleContext: Shared services available to all handlers
- EventContext: For event handlers
- CommandContext: For command handlers
- RouteContext: For HTTP route handlers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

    from ..registries.commands import ModuleCommands
    from ..registries.events import ModuleEvents
    from ..registries.routes import ModuleRoutes
    from .commands import CommandEvent
    from .config import ModuleConfig
    from .event_types import ChatEvent, User
    from .http_client import HttpClient
    from .owncast_admin_client import OwncastAdminClient
    from .owncast_client import OwncastClient
    from .storage import ModuleStorage
    from .templates import ModuleTemplates


@dataclass
class ModuleContext:
    """Shared services available to all module handlers.

    Created once per module during loading and reused for all handler
    invocations. This bundles the common dependencies that every handler needs.
    """

    # Module name (identity).
    module_name: str

    # Module-scoped configuration.
    config: ModuleConfig

    # Client for interacting with the Owncast server.
    owncast_client: OwncastClient

    # SQLite storage API for persisting data.
    storage: ModuleStorage

    # Module-scoped command API for dynamic command registration/lookup.
    commands: ModuleCommands

    # Module-scoped event handler API for dynamic handler registration/lookup.
    events: ModuleEvents

    # Module-scoped route API for URL building and route introspection.
    routes: ModuleRoutes

    # Shared HTTP client for making web requests.
    http: HttpClient

    # Module-scoped Jinja2 template rendering.
    templates: ModuleTemplates

    # Optional admin client for the Owncast Admin API (None if admin is not enabled).
    admin_client: OwncastAdminClient | None = None

    # Per-instance state storage for modules.
    # Modules can store runtime objects (managers, schedulers, etc.) here
    # instead of using module-level globals, ensuring multiple bot instances
    # in the same process don't share mutable state. Access values through
    # typed helper functions that narrow with isinstance checks.
    state: dict[str, Any] = field(default_factory=dict)

    # Module-scoped logger (named "owlbot.modules.<module_name>").
    # Derived from module_name in __post_init__; not a constructor parameter.
    logger: logging.Logger = field(init=False)

    def __post_init__(self) -> None:
        """Derive the module-scoped logger from the module name."""
        self.logger = logging.getLogger(f"owlbot.modules.{self.module_name}")


@dataclass
class PropagationState:
    """Mutable state for controlling event propagation across handlers."""

    stopped: bool = False
    reason: str = ""


@dataclass
class EventContext[E]:
    """Context passed to event handlers.

    Each handler invocation receives its own EventContext instance with the event
    data and access to shared services via the module context.
    """

    # The event that triggered this handler (ChatEvent, UserJoinedEvent, etc.).
    event: E

    # Shared services for this module.
    module: ModuleContext

    # Shared state for propagation control.
    # All handlers for a single event dispatch share the same instance.
    _propagation: PropagationState = field(default_factory=PropagationState, repr=False)

    @property
    def module_name(self) -> str:
        """Module name."""
        return self.module.module_name

    @property
    def config(self) -> ModuleConfig:
        """Module-scoped configuration."""
        return self.module.config

    @property
    def owncast_client(self) -> OwncastClient:
        """Client for interacting with the Owncast server."""
        return self.module.owncast_client

    @property
    def storage(self) -> ModuleStorage:
        """SQLite storage API for persisting data."""
        return self.module.storage

    @property
    def commands(self) -> ModuleCommands:
        """Command registry for dynamic command registration/lookup."""
        return self.module.commands

    @property
    def events(self) -> ModuleEvents:
        """Module-scoped event handler API for dynamic handler registration/lookup."""
        return self.module.events

    @property
    def routes(self) -> ModuleRoutes:
        """Module-scoped route API for URL building and route introspection."""
        return self.module.routes

    @property
    def logger(self) -> logging.Logger:
        """Module-scoped logger."""
        return self.module.logger

    @property
    def http(self) -> HttpClient:
        """Shared HTTP client for making web requests."""
        return self.module.http

    @property
    def templates(self) -> ModuleTemplates:
        """Module-scoped Jinja2 template rendering."""
        return self.module.templates

    @property
    def admin_client(self) -> OwncastAdminClient | None:
        """Optional client for the Owncast Admin API (None if admin is not enabled)."""
        return self.module.admin_client

    @property
    def propagation_stopped(self) -> bool:
        """Check if event propagation has been stopped by a handler.

        :return: True if stop_propagation() was called by any handler.
        """
        return self._propagation.stopped

    def stop_propagation(self, reason: str | None = None) -> None:
        """Stop event from being dispatched to remaining handlers and commands.

        Once called, no further handlers will be invoked for this event, and
        command dispatch (for CHAT events) will be skipped.

        :param reason: Optional reason for stopping (logged for debugging).
        """
        self._propagation.stopped = True
        # Only store the first reason provided (subsequent calls don't override).
        if reason and not self._propagation.reason:
            self._propagation.reason = reason


@dataclass
class CommandContext:
    """Context passed to command handlers.

    Provides access to the parsed command data, original chat event context,
    and shared services. Like EventContext and RouteContext, all ModuleContext
    fields are proxied for convenience.
    """

    # The parsed command information.
    command_event: CommandEvent

    # The event context for the original ChatEvent (provides propagation control).
    event_context: EventContext[ChatEvent]

    # Shared services for this module.
    module: ModuleContext

    @property
    def command(self) -> str:
        """The command name that was invoked (canonical name, not alias)."""
        return self.command_event.command

    @property
    def args(self) -> str:
        """The raw argument string after the command."""
        return self.command_event.args

    @property
    def args_list(self) -> list[str]:
        """Arguments split into a list."""
        return self.command_event.args_list

    @property
    def prefix(self) -> str:
        """The command prefix (e.g., '!')."""
        return self.command_event.prefix

    @property
    def chat_event(self) -> ChatEvent:
        """The original chat event that triggered this command."""
        return self.command_event.chat_event

    @property
    def user(self) -> User:
        """The user who invoked the command (shortcut to chat_event.user)."""
        return self.command_event.chat_event.user

    @property
    def module_name(self) -> str:
        """Module name."""
        return self.module.module_name

    @property
    def config(self) -> ModuleConfig:
        """Module-scoped configuration."""
        return self.module.config

    @property
    def owncast_client(self) -> OwncastClient:
        """Client for interacting with the Owncast server."""
        return self.module.owncast_client

    @property
    def storage(self) -> ModuleStorage:
        """SQLite storage API for persisting data."""
        return self.module.storage

    @property
    def commands(self) -> ModuleCommands:
        """Command registry for dynamic command registration/lookup."""
        return self.module.commands

    @property
    def events(self) -> ModuleEvents:
        """Module-scoped event handler API for dynamic handler registration/lookup."""
        return self.module.events

    @property
    def routes(self) -> ModuleRoutes:
        """Module-scoped route API for URL building and route introspection."""
        return self.module.routes

    @property
    def logger(self) -> logging.Logger:
        """Module-scoped logger."""
        return self.module.logger

    @property
    def http(self) -> HttpClient:
        """Shared HTTP client for making web requests."""
        return self.module.http

    @property
    def templates(self) -> ModuleTemplates:
        """Module-scoped Jinja2 template rendering."""
        return self.module.templates

    @property
    def admin_client(self) -> OwncastAdminClient | None:
        """Optional client for the Owncast Admin API (None if admin is not enabled)."""
        return self.module.admin_client


@dataclass
class RouteContext:
    """Context passed to HTTP route handlers.

    Similar to EventContext but includes the aiohttp request object
    for accessing HTTP-specific data (body, headers, query params).
    """

    # The aiohttp request object.
    request: web.Request

    # Shared services for this module.
    module: ModuleContext

    # Captured path parameters from pattern matching (e.g., {"id": "123"}).
    # Empty dict for plain routes, populated for routes with {name} patterns.
    match_info: dict[str, str] = field(default_factory=dict)

    @property
    def module_name(self) -> str:
        """Module name."""
        return self.module.module_name

    @property
    def config(self) -> ModuleConfig:
        """Module-scoped configuration."""
        return self.module.config

    @property
    def owncast_client(self) -> OwncastClient:
        """Client for interacting with the Owncast server."""
        return self.module.owncast_client

    @property
    def storage(self) -> ModuleStorage:
        """SQLite storage API for persisting data."""
        return self.module.storage

    @property
    def commands(self) -> ModuleCommands:
        """Command registry for dynamic command registration/lookup."""
        return self.module.commands

    @property
    def events(self) -> ModuleEvents:
        """Module-scoped event handler API for dynamic handler registration/lookup."""
        return self.module.events

    @property
    def routes(self) -> ModuleRoutes:
        """Module-scoped route API for URL building and route introspection."""
        return self.module.routes

    @property
    def logger(self) -> logging.Logger:
        """Module-scoped logger."""
        return self.module.logger

    @property
    def http(self) -> HttpClient:
        """Shared HTTP client for making web requests."""
        return self.module.http

    @property
    def templates(self) -> ModuleTemplates:
        """Module-scoped Jinja2 template rendering."""
        return self.module.templates

    @property
    def admin_client(self) -> OwncastAdminClient | None:
        """Optional client for the Owncast Admin API (None if admin is not enabled)."""
        return self.module.admin_client
