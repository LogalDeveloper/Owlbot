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

"""Route registry, module-scoped wrapper, and dispatcher.

Internal infrastructure for managing HTTP route registration and dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, overload

import orjson
from aiohttp import web
from aiohttp.web import DynamicResource

from owlbot.api.context import RouteContext
from owlbot.api.routes import RouteHandler, RouteInfo, RouteMark
from owlbot.sessions import SESSION_COOKIE_NAME
from owlbot.web.sessions import connect_guidance_response

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from owlbot.api.context import ModuleContext
    from owlbot.sessions import BrowserSession, SessionManager

logger = logging.getLogger("owlbot.web")


@dataclass(slots=True)
class _RouteGroup:
    """Groups route handlers that share the same path pattern.

    Each group holds a single ``DynamicResource`` for path matching
    and a list of ``RouteInfo`` entries whose method sets must not overlap.
    """

    resource: DynamicResource
    full_path: str
    handlers: list[RouteInfo]


class RouteRegistry:
    """Holds all registered routes for a bot instance.

    Routes are namespaced by module to prevent conflicts. Supports path
    patterns using aiohttp's ``{name}`` and ``{name:regex}`` syntax via
    ``DynamicResource`` for pattern compilation and matching.

    Multiple handlers may be registered on the same path as long as their
    HTTP method sets do not overlap.
    """

    def __init__(self) -> None:
        """Initialize an empty route registry."""
        # Ordered list of route groups for pattern matching.
        # Each group represents a unique path pattern with one or more handlers.
        self._groups: list[_RouteGroup] = []
        # Maps module_name -> list of full_paths (for cleanup).
        self._module_routes: dict[str, list[str]] = defaultdict(list)
        logger.debug("RouteRegistry initialized.")

    def _find_group(self, full_path: str) -> _RouteGroup | None:
        """Find a route group by its exact full path.

        :param full_path: The full route path including namespace.
        :return: The route group if found, None otherwise.
        """
        for group in self._groups:
            if group.full_path == full_path:
                return group
        return None

    def register(
        self,
        path: str,
        handler: RouteHandler,
        *,
        methods: list[str] | None = None,
        module_name: str,
        streaming: bool = False,
        requires_session: bool = False,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
    ) -> RouteInfo:
        """Register a route handler.

        :param path: URL path relative to module namespace. Supports
            ``{name}`` and ``{name:regex}`` patterns.
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods. Default: ["GET"].
        :param module_name: Name of the module registering this route.
        :param streaming: Whether this route streams its response and should
            bypass the handler timeout. Default: False.
        :return: RouteInfo for the registered route.
        :raises ValueError: If any method overlaps with an existing handler
            on the same path.
        """
        if methods is None:
            methods = ["GET"]

        if not path.startswith("/"):
            path = "/" + path

        full_path = f"/owlbot/{module_name}{path}"
        new_methods = frozenset(m.upper() for m in methods)

        group = self._find_group(full_path)
        if group is not None:
            for existing in group.handlers:
                overlap = existing.methods & new_methods
                if overlap:
                    raise ValueError(
                        f"Route '{full_path}' already has a handler for "
                        f"method(s): {', '.join(sorted(overlap))}"
                    )

        info = RouteInfo(
            path=path,
            full_path=full_path,
            methods=new_methods,
            handler=handler,
            module_name=module_name,
            streaming=streaming,
            requires_session=requires_session,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
        )

        if group is None:
            group = _RouteGroup(
                resource=DynamicResource(full_path),
                full_path=full_path,
                handlers=[info],
            )
            self._groups.append(group)
        else:
            group.handlers.append(info)

        if full_path not in self._module_routes[module_name]:
            self._module_routes[module_name].append(full_path)

        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        module_logger.debug(
            "Registered route '%s' [%s].",
            full_path,
            ", ".join(sorted(info.methods)),
        )
        return info

    def unregister(self, full_path: str, *, method: str | None = None) -> bool:
        """Unregister route handler(s) by full path.

        When ``method`` is ``None``, removes all handlers for the path
        (and the route group itself). When ``method`` is given, removes
        only the handler covering that method; the group is removed if
        no handlers remain.

        :param full_path: The full route path including namespace.
        :param method: Optional HTTP method to target a specific handler.
        :return: True if anything was removed, False otherwise.
        """
        for i, group in enumerate(self._groups):
            if group.full_path != full_path:
                continue

            if method is None:
                # Remove entire group.
                self._groups.pop(i)
                for handler in group.handlers:
                    if handler.module_name in self._module_routes:
                        self._module_routes[handler.module_name] = [
                            p
                            for p in self._module_routes[handler.module_name]
                            if p != full_path
                        ]
                module_names = {h.module_name for h in group.handlers}
                for name in module_names:
                    module_logger = logging.getLogger(f"owlbot.modules.{name}.routes")
                    module_logger.debug("Unregistered route '%s'.", full_path)
                return True

            # Remove only the handler for the specified method.
            method_upper = method.upper()
            for j, handler in enumerate(group.handlers):
                if method_upper in handler.methods:
                    group.handlers.pop(j)
                    # Only remove from _module_routes if the module has no
                    # remaining handlers at this path.
                    module_name = handler.module_name
                    still_owns_path = any(
                        h.module_name == module_name for h in group.handlers
                    )
                    if not still_owns_path and module_name in self._module_routes:
                        self._module_routes[module_name] = [
                            p
                            for p in self._module_routes[module_name]
                            if p != full_path
                        ]
                    # Remove the group if no handlers remain.
                    if not group.handlers:
                        self._groups.pop(i)
                    module_logger = logging.getLogger(
                        f"owlbot.modules.{module_name}.routes"
                    )
                    module_logger.debug(
                        "Unregistered route '%s' [%s].", full_path, method_upper
                    )
                    return True

            return False

        return False

    @overload
    def get(self, full_path: str) -> list[RouteInfo]: ...

    @overload
    def get(self, full_path: str, *, method: str) -> RouteInfo | None: ...

    def get(
        self, full_path: str, *, method: str | None = None
    ) -> list[RouteInfo] | RouteInfo | None:
        """Look up routes by full path.

        When ``method`` is ``None``, returns all handlers registered at
        the path. When ``method`` is given, returns the single handler
        covering that method, or ``None``.

        :param full_path: The full route path including namespace.
        :param method: Optional HTTP method to look up a specific handler.
        :return: List of RouteInfo (no method), RouteInfo or None (with method).
        """
        group = self._find_group(full_path)
        if group is None:
            return [] if method is None else None

        if method is None:
            return list(group.handlers)

        method_upper = method.upper()
        for handler in group.handlers:
            if method_upper in handler.methods:
                return handler
        return None

    def match(
        self, full_path: str, method: str
    ) -> tuple[RouteInfo, dict[str, str]] | tuple[None, frozenset[str], dict[str, str]]:
        """Match a request path and method against registered routes.

        Scans route groups in registration order. Uses
        ``DynamicResource._match()`` for both plain and parameterized paths.

        :param full_path: The request path to match.
        :param method: The HTTP method of the request.
        :return: ``(RouteInfo, match_dict)`` on success, or
            ``(None, allowed_methods, match_dict)`` if the path matches
            but the method is not allowed.
        :raises LookupError: If no route group matches the path (404).
        """
        method = method.upper()
        for group in self._groups:
            match_dict = group.resource._match(full_path)  # noqa: SLF001  # no public API alternative
            if match_dict is not None:
                for handler in group.handlers:
                    if method in handler.methods:
                        return handler, match_dict
                # Path matched but method not allowed.
                all_methods: frozenset[str] = frozenset().union(
                    *(h.methods for h in group.handlers)
                )
                return None, all_methods, match_dict
        raise LookupError(full_path)

    def get_all(self) -> list[RouteInfo]:
        """Get all registered route handlers.

        :return: List of all RouteInfo across all groups.
        """
        return [handler for group in self._groups for handler in group.handlers]

    def get_by_module(self, module_name: str) -> list[RouteInfo]:
        """Get all routes registered by a specific module.

        :param module_name: The module name.
        :return: List of RouteInfo for that module.
        """
        paths = set(self._module_routes.get(module_name, []))
        return [
            handler
            for group in self._groups
            if group.full_path in paths
            for handler in group.handlers
            if handler.module_name == module_name
        ]

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all routes registered by a specific module.

        :param module_name: The module whose routes should be removed.
        :return: Number of route handlers removed.
        """
        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        paths = set(self._module_routes.get(module_name, []))

        if not paths:
            return 0

        count = 0
        # Iterate in reverse to allow safe removal.
        for i in range(len(self._groups) - 1, -1, -1):
            group = self._groups[i]
            if group.full_path not in paths:
                continue

            before = len(group.handlers)
            group.handlers = [h for h in group.handlers if h.module_name != module_name]
            removed = before - len(group.handlers)
            count += removed

            if not group.handlers:
                self._groups.pop(i)

        if count > 0:
            module_logger.debug("Unregistered all routes (%d handler(s) total).", count)

        self._module_routes.pop(module_name, None)
        return count

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for @on_route-decorated functions and register them.

        Looks for functions with the ``_owlbot_route`` attribute set by
        the ``@on_route`` decorator and registers each one.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        for obj in vars(module).values():
            if callable(obj):
                route_info = getattr(obj, "_owlbot_route", None)
                if route_info is not None:
                    mark = cast("RouteMark", route_info)
                    self.register(
                        path=mark["path"],
                        handler=obj,
                        methods=mark["methods"],
                        module_name=module_name,
                        streaming=mark["streaming"],
                        requires_session=mark["requires_session"],
                        requires_authenticated=mark["requires_authenticated"],
                        requires_moderator=mark["requires_moderator"],
                    )


class RouteDispatcher:
    """Dispatches HTTP requests to registered module route handlers.

    Looks up routes in the RouteRegistry, validates methods, creates
    RouteContext, and calls the handler with timeout enforcement.
    """

    def __init__(
        self,
        get_module_context: Callable[[str], ModuleContext],
        handler_timeout: float,
        session_manager: SessionManager,
        command_prefix: str = "!",
    ) -> None:
        """Initialize the route dispatcher.

        Creates and owns a :class:`RouteRegistry` internally.

        :param get_module_context: Callable that looks up a
            ModuleContext by module name.
        :param handler_timeout: Timeout for route handlers in seconds.
        """
        self._route_registry = RouteRegistry()
        self._get_module_context = get_module_context
        self._handler_timeout = handler_timeout
        self._session_manager = session_manager
        self._command_prefix = command_prefix
        self._streaming_tasks: set[asyncio.Task[Any]] = set()
        self._handler_tasks: set[asyncio.Task[Any]] = set()

    def register(
        self,
        path: str,
        handler: RouteHandler,
        *,
        methods: list[str] | None = None,
        module_name: str,
        streaming: bool = False,
        requires_session: bool = False,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
    ) -> RouteInfo:
        """Register a route handler.

        Delegates to the internal RouteRegistry.

        :param path: URL path relative to module namespace.
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods. Default: ["GET"].
        :param module_name: Name of the module registering this route.
        :param streaming: If True, handler runs without timeout. Default: False.
        :return: RouteInfo for the registered route.
        :raises ValueError: If route conflicts with existing route.
        """
        return self._route_registry.register(
            path=path,
            handler=handler,
            methods=methods,
            module_name=module_name,
            streaming=streaming,
            requires_session=requires_session,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
        )

    def unregister(self, full_path: str, *, method: str | None = None) -> bool:
        """Unregister route handler(s) by full path.

        Delegates to the internal RouteRegistry.

        :param full_path: The full route path including namespace.
        :param method: Optional HTTP method to target a specific handler.
        :return: True if anything was removed, False otherwise.
        """
        return self._route_registry.unregister(full_path, method=method)

    @overload
    def get(self, full_path: str) -> list[RouteInfo]: ...

    @overload
    def get(self, full_path: str, *, method: str) -> RouteInfo | None: ...

    def get(
        self, full_path: str, *, method: str | None = None
    ) -> list[RouteInfo] | RouteInfo | None:
        """Look up routes by full path.

        Delegates to the internal RouteRegistry.

        :param full_path: The full route path including namespace.
        :param method: Optional HTTP method to look up a specific handler.
        :return: List of RouteInfo (no method), RouteInfo or None (with method).
        """
        if method is None:
            return self._route_registry.get(full_path)
        return self._route_registry.get(full_path, method=method)

    def get_by_module(self, module_name: str) -> list[RouteInfo]:
        """Get all routes registered by a specific module.

        Delegates to the internal RouteRegistry.

        :param module_name: The module name.
        :return: List of RouteInfo for that module.
        """
        return self._route_registry.get_by_module(module_name)

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for @on_route-decorated functions and register them.

        Delegates to the internal RouteRegistry.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        self._route_registry.register_from_module(module, module_name)

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all routes registered by a specific module.

        Delegates to the internal RouteRegistry.

        :param module_name: The module whose routes should be removed.
        :return: Number of routes removed.
        """
        return self._route_registry.unregister_by_module(module_name)

    async def drain_handlers(self) -> None:
        """Drain all active route handlers during shutdown.

        Waits for in-flight non-streaming handlers to complete, then
        cancels long-lived streaming handlers so their connections
        close promptly instead of blocking until aiohttp's shutdown
        timeout expires.

        Intended to be called via ``app.on_shutdown``.
        """
        if self._handler_tasks:
            logger.info(
                "Waiting for %d non-streaming handler(s) to complete...",
                len(self._handler_tasks),
            )
            await asyncio.gather(*list(self._handler_tasks), return_exceptions=True)
            logger.debug("All non-streaming handlers completed.")

        if not self._streaming_tasks:
            return

        logger.info(
            "Cancelling %d active streaming handler(s)...",
            len(self._streaming_tasks),
        )
        tasks = list(self._streaming_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug("All streaming handlers cancelled.")

    async def dispatch(self, request: web.Request) -> web.StreamResponse:
        """Dispatch an HTTP request to the appropriate module route handler.

        Extracts module_name and path from the URL, matches it against
        registered routes (supporting path patterns), validates the HTTP
        method, and calls the handler.

        :param request: The aiohttp request object.
        :return: HTTP response.
        """
        module_name = request.match_info["module_name"]
        path = request.match_info.get("path", "")
        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        session = self._session_manager.get_session(session_id)

        relative_path = f"/{path}" if path else "/"

        full_path = f"/owlbot/{module_name}{relative_path}"

        mod_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        try:
            result = self._route_registry.match(full_path, request.method)
        except LookupError:
            mod_logger.debug("No route registered for '%s'.", full_path)
            return web.Response(status=404)

        if result[0] is None:
            # Path matched, method not allowed.
            _, allowed_methods, _ = result
            allowed = ", ".join(sorted(allowed_methods))
            mod_logger.debug(
                "Method %s not allowed for '%s' (allowed: %s)",
                request.method,
                full_path,
                allowed,
            )
            return web.Response(status=405, headers={"Allow": allowed})

        route_info, match_info = result
        return await self._handle_module_route(
            request,
            route_info,
            match_info,
            session=session,
        )

    async def _handle_module_route(
        self,
        request: web.Request,
        route_info: RouteInfo,
        match_info: dict[str, str] | None = None,
        *,
        session: BrowserSession | None,
    ) -> web.StreamResponse:
        """Handle an HTTP request to a module-registered route.

        :param request: The aiohttp request object.
        :param route_info: Information about the registered route.
        :param match_info: Captured path parameters from pattern matching.
        :return: HTTP response.
        """
        module_name = route_info.module_name
        mod_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")

        module_ctx = self._get_module_context(module_name)

        ctx = RouteContext(
            request=request,
            module=module_ctx,
            match_info=match_info if match_info is not None else {},
            session=session,
        )

        guard_response = self._guard_response(
            route_info,
            session=session,
        )
        if guard_response is not None:
            return guard_response

        logger.debug(
            "Calling route handler: %s from module: %s",
            route_info.full_path,
            module_name,
        )

        task = asyncio.current_task()
        if task is not None:
            if route_info.streaming:
                self._streaming_tasks.add(task)
            else:
                self._handler_tasks.add(task)

        try:
            start = time.perf_counter()
            if route_info.streaming:
                mod_logger.debug(
                    "Streaming handler '%s' dispatched (no timeout).",
                    route_info.full_path,
                )
                result = await route_info.handler(ctx)
            else:
                result = await asyncio.wait_for(
                    route_info.handler(ctx), timeout=self._handler_timeout
                )
            elapsed = (time.perf_counter() - start) * 1000

            mod_logger.debug(
                "Route handler '%s' completed in %.1fms.",
                route_info.full_path,
                elapsed,
            )

            if result is None:
                return web.Response(status=204)  # No Content.
            if isinstance(result, web.StreamResponse):
                return result
            if isinstance(result, dict):
                return web.Response(
                    body=orjson.dumps(result),
                    content_type="application/json",
                )
            # pragma: no branch — defensive against untyped handlers
            mod_logger.error(  # type: ignore[unreachable]
                "Route handler '%s' returned unsupported type: %s",
                route_info.full_path,
                type(result).__name__,
            )
            return web.Response(status=500)
        except web.HTTPException:
            raise
        except TimeoutError:
            mod_logger.warning(
                "Route handler '%s' timed out after %ss.",
                route_info.full_path,
                self._handler_timeout,
            )
            return web.Response(status=500)
        except Exception:
            mod_logger.exception(
                "Route handler '%s' raised exception.",
                route_info.full_path,
            )
            return web.Response(status=500)
        finally:
            if task is not None:
                self._streaming_tasks.discard(task)
                self._handler_tasks.discard(task)

    def _guard_response(
        self,
        route_info: RouteInfo,
        *,
        session: BrowserSession | None,
    ) -> web.Response | None:
        requires_session = (
            route_info.requires_session
            or route_info.requires_authenticated
            or route_info.requires_moderator
        )
        if requires_session and session is None:
            return connect_guidance_response(
                status=401,
                command_prefix=self._command_prefix,
            )
        if session is None:
            return None
        if route_info.requires_authenticated and not session.is_authenticated:
            return connect_guidance_response(
                status=403,
                command_prefix=self._command_prefix,
                title="Authentication required",
                message="You must be authenticated in Owncast to access this page.",
                command_message=(
                    "If you believe this is in error, try using "
                    f"{self._command_prefix}connect in chat to reconnect your "
                    "Owncast account."
                ),
            )
        if route_info.requires_moderator and not session.is_moderator:
            return connect_guidance_response(
                status=403,
                command_prefix=self._command_prefix,
                title="Moderator access required",
                message="Only moderators can access this page.",
                command_message=(
                    "If you believe this is in error, try using "
                    f"{self._command_prefix}connect in chat to reconnect your "
                    "Owncast account."
                ),
            )
        return None


class ModuleRoutes:
    """Module-scoped wrapper around RouteDispatcher.

    This wrapper auto-supplies the module name for route operations,
    so modules don't need to know the internal routing namespace.
    Follows the same pattern as ModuleCommands.
    """

    def __init__(
        self,
        dispatcher: RouteDispatcher,
        module_name: str,
        public_base_url: str,
    ) -> None:
        """Initialize the module-scoped routes wrapper.

        :param dispatcher: The RouteDispatcher that owns the route registry.
        :param module_name: The name of the module using this wrapper.
        :param public_base_url: The public base URL for building route URLs.
        """
        self._dispatcher = dispatcher
        self._module_name = module_name
        self._public_base_url = public_base_url

    @property
    def module_routes(self) -> list[RouteInfo]:
        """Routes registered by this module."""
        return self._dispatcher.get_by_module(self._module_name)

    def url_for(self, path: str) -> str:
        """Build a public URL for a route registered by this module.

        :param path: The route path (e.g., "/list").
        :return: Full public URL (e.g., "http://host/owlbot/quotes/list").
        """
        return f"{self._public_base_url}{self._full_path(path)}"

    def register(
        self,
        path: str,
        handler: RouteHandler,
        *,
        methods: list[str] | None = None,
        streaming: bool = False,
        requires_session: bool = False,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
    ) -> RouteInfo:
        """Register a route handler for this module.

        The module name is automatically supplied.

        :param path: URL path relative to module namespace (e.g., "/stats").
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods (e.g., ["GET"]). Default: ["GET"].
        :param streaming: If True, handler runs without timeout. Default: False.
        :return: RouteInfo for the registered route.
        :raises ValueError: If route conflicts with existing route.
        """
        return self._dispatcher.register(
            path=path,
            handler=handler,
            methods=methods,
            module_name=self._module_name,
            streaming=streaming,
            requires_session=requires_session,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
        )

    def unregister(self, path: str, *, method: str | None = None) -> bool:
        """Unregister route handler(s) by relative path.

        :param path: Relative route path (e.g., "/stats").
        :param method: Optional HTTP method to target a specific handler.
            When ``None``, removes all handlers for the path.
        :return: True if anything was removed, False otherwise.
        """
        return self._dispatcher.unregister(self._full_path(path), method=method)

    @overload
    def get(self, path: str) -> list[RouteInfo]: ...

    @overload
    def get(self, path: str, *, method: str) -> RouteInfo | None: ...

    def get(
        self, path: str, *, method: str | None = None
    ) -> list[RouteInfo] | RouteInfo | None:
        """Look up routes by relative path.

        :param path: Relative route path (e.g., "/stats").
        :param method: Optional HTTP method to look up a specific handler.
        :return: List of RouteInfo when ``method`` is ``None``,
            or ``RouteInfo | None`` when ``method`` is given.
        """
        if method is None:
            return self._dispatcher.get(self._full_path(path))
        return self._dispatcher.get(self._full_path(path), method=method)

    def exists(self, path: str, *, method: str | None = None) -> bool:
        """Check if a route is registered at the given relative path.

        :param path: Relative route path (e.g., "/stats").
        :param method: Optional HTTP method to check for a specific handler.
        :return: True if a matching route exists, False otherwise.
        """
        if method is None:
            return len(self.get(path)) > 0
        return self.get(path, method=method) is not None

    def _full_path(self, path: str) -> str:
        """Normalize a relative path into the full namespaced path.

        :param path: Relative route path (e.g., "/list" or "list").
        :return: Full path (e.g., "/owlbot/quotes/list").
        """
        if not path.startswith("/"):
            path = "/" + path
        return f"/owlbot/{self._module_name}{path}"
