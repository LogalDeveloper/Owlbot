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

"""HTTP route registration decorators and types for Owlbot.

This module provides the module-facing API for HTTP routes:
- @on_route decorator for registering route handlers
- RouteInfo dataclass for route metadata
- RouteHandler type alias
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiohttp import web

    from .context import RouteContext


class RouteMark(TypedDict):
    """Type for the route marker attribute set by @on_route."""

    path: str
    methods: list[str] | None
    streaming: bool


# Handler type: receives RouteContext, returns a response or dict (auto-JSON).
type RouteHandler = (
    "Callable[[RouteContext], Awaitable[web.StreamResponse | dict[str, Any] | None]]"
)


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """Metadata about a registered route."""

    path: str  # Path relative to module namespace (e.g., "/stats").
    full_path: str  # Full path including namespace (e.g., "/owlbot/mymodule/stats").
    methods: frozenset[str]  # HTTP methods (GET, POST, etc.).
    handler: RouteHandler
    module_name: str
    streaming: bool = False


def on_route(
    path: str,
    *,
    methods: list[str] | None = None,
    streaming: bool = False,
) -> Callable[[RouteHandler], RouteHandler]:
    """Register an HTTP route handler.

    Routes are namespaced under /owlbot/<module_name>/<path>.

    :param path: URL path (relative to module namespace, e.g., "/stats").
    :param methods: List of HTTP methods to accept. Default: ["GET"].
    :param streaming: If True, the handler bypasses the dispatch timeout.
        The handler is responsible for its own keepalives and cleanup.
    :return: Decorator that marks the function for registration.
    """

    def decorator(func: RouteHandler) -> RouteHandler:
        # Mark the function with route info for deferred registration.
        # methods=None is resolved to ["GET"] by RouteRegistry.register().
        func._owlbot_route = RouteMark(  # type: ignore[attr-defined]
            path=path, methods=methods, streaming=streaming
        )
        return func

    return decorator
