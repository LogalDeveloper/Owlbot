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
from typing import TYPE_CHECKING, cast

from aiohttp import web
from aiohttp.web import DynamicResource

from ..api.routes import RouteHandler, RouteInfo, RouteMark

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from ..api.context import ModuleContext

logger = logging.getLogger("owlbot.web")


class RouteRegistry:
    """
    Holds all registered routes for a bot instance.

    Routes are namespaced by module to prevent conflicts. Supports path
    patterns using aiohttp's ``{name}`` and ``{name:regex}`` syntax via
    ``DynamicResource`` for pattern compilation and matching.
    """

    def __init__(self) -> None:
        """Initialize an empty route registry."""
        # Ordered list of (DynamicResource, RouteInfo) for pattern matching.
        # DynamicResource handles both plain and parameterized paths uniformly.
        self._routes: list[tuple[DynamicResource, RouteInfo]] = []
        # Maps module_name -> list of full_paths (for cleanup).
        self._module_routes: dict[str, list[str]] = {}
        logger.debug("RouteRegistry initialized.")

    def register(
        self,
        path: str,
        handler: RouteHandler,
        *,
        methods: list[str] | None = None,
        module_name: str,
    ) -> RouteInfo:
        """
        Register a route handler.

        :param path: URL path relative to module namespace. Supports
            ``{name}`` and ``{name:regex}`` patterns.
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods. Default: ["GET"].
        :param module_name: Name of the module registering this route.
        :return: RouteInfo for the registered route.
        :raises ValueError: If route conflicts with existing route.
        """
        if methods is None:
            methods = ["GET"]

        if not path.startswith("/"):
            path = "/" + path

        full_path = f"/owlbot/{module_name}{path}"

        if any(info.full_path == full_path for _, info in self._routes):
            raise ValueError(f"Route '{full_path}' is already registered")

        info = RouteInfo(
            path=path,
            full_path=full_path,
            methods=frozenset(m.upper() for m in methods),
            handler=handler,
            module_name=module_name,
        )

        resource = DynamicResource(full_path)
        self._routes.append((resource, info))

        if module_name not in self._module_routes:
            self._module_routes[module_name] = []
        self._module_routes[module_name].append(full_path)

        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        module_logger.debug(
            f"Registered route '{full_path}' [{', '.join(info.methods)}]."
        )
        return info

    def unregister(self, full_path: str) -> bool:
        """
        Unregister a route by its full path.

        :param full_path: The full route path including namespace.
        :return: True if route was found and removed, False otherwise.
        """
        for i, (_, info) in enumerate(self._routes):
            if info.full_path == full_path:
                self._routes.pop(i)

                if info.module_name in self._module_routes:
                    self._module_routes[info.module_name] = [
                        p
                        for p in self._module_routes[info.module_name]
                        if p != full_path
                    ]

                module_logger = logging.getLogger(
                    f"owlbot.modules.{info.module_name}.routes"
                )
                module_logger.debug(f"Unregistered route '{full_path}'.")
                return True

        return False

    def get(self, full_path: str) -> RouteInfo | None:
        """
        Look up a route by its full path (exact match on registered pattern).

        :param full_path: The full route path including namespace.
        :return: RouteInfo if found, None otherwise.
        """
        for _, info in self._routes:
            if info.full_path == full_path:
                return info
        return None

    def match(self, full_path: str) -> tuple[RouteInfo, dict[str, str]] | None:
        """
        Match a request path against registered routes.

        Scans routes in registration order (first match wins). Uses
        ``DynamicResource._match()`` for both plain and parameterized paths.

        :param full_path: The request path to match.
        :return: Tuple of (RouteInfo, match_info dict) if matched, None otherwise.
        """
        for resource, info in self._routes:
            match_dict = resource._match(full_path)  # no public API alternative
            if match_dict is not None:
                return info, match_dict
        return None

    def get_all(self) -> dict[str, RouteInfo]:
        """
        Get all registered routes.

        :return: Dict mapping full paths to RouteInfo.
        """
        return {info.full_path: info for _, info in self._routes}

    def get_by_module(self, module_name: str) -> list[RouteInfo]:
        """
        Get all routes registered by a specific module.

        :param module_name: The module name.
        :return: List of RouteInfo for that module.
        """
        paths = set(self._module_routes.get(module_name, []))
        return [info for _, info in self._routes if info.full_path in paths]

    def unregister_by_module(self, module_name: str) -> int:
        """
        Remove all routes registered by a specific module.

        :param module_name: The module whose routes should be removed.
        :return: Number of routes removed.
        """
        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        paths = list(self._module_routes.get(module_name, []))

        if not paths:
            return 0

        module_logger.debug(f"Unregistering all routes ({len(paths)} total).")
        count = 0
        for path in paths:
            if self.unregister(path):
                count += 1

        self._module_routes.pop(module_name, None)
        return count

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """
        Scan a Python module for @on_route-decorated functions and register them.

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
                    )


class RouteDispatcher:
    """
    Dispatches HTTP requests to registered module route handlers.

    Looks up routes in the RouteRegistry, validates methods, creates
    RouteContext, and calls the handler with timeout and transaction management.
    """

    def __init__(
        self,
        get_module_context: Callable[[str], ModuleContext],
        handler_timeout: float,
    ) -> None:
        """
        Initialize the route dispatcher.

        Creates and owns a :class:`RouteRegistry` internally.

        :param get_module_context: Callable that looks up a
            ModuleContext by module name.
        :param handler_timeout: Timeout for route handlers in seconds.
        """
        self._route_registry = RouteRegistry()
        self._get_module_context = get_module_context
        self._handler_timeout = handler_timeout

    def register(
        self,
        path: str,
        handler: RouteHandler,
        *,
        methods: list[str] | None = None,
        module_name: str,
    ) -> RouteInfo:
        """
        Register a route handler.

        Delegates to the internal RouteRegistry.

        :param path: URL path relative to module namespace.
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods. Default: ["GET"].
        :param module_name: Name of the module registering this route.
        :return: RouteInfo for the registered route.
        :raises ValueError: If route conflicts with existing route.
        """
        return self._route_registry.register(
            path=path,
            handler=handler,
            methods=methods,
            module_name=module_name,
        )

    def unregister(self, full_path: str) -> bool:
        """
        Unregister a route by its full path.

        Delegates to the internal RouteRegistry.

        :param full_path: The full route path including namespace.
        :return: True if route was found and removed, False otherwise.
        """
        return self._route_registry.unregister(full_path)

    def get(self, full_path: str) -> RouteInfo | None:
        """
        Look up a route by its full path.

        Delegates to the internal RouteRegistry.

        :param full_path: The full route path including namespace.
        :return: RouteInfo if found, None otherwise.
        """
        return self._route_registry.get(full_path)

    def get_by_module(self, module_name: str) -> list[RouteInfo]:
        """
        Get all routes registered by a specific module.

        Delegates to the internal RouteRegistry.

        :param module_name: The module name.
        :return: List of RouteInfo for that module.
        """
        return self._route_registry.get_by_module(module_name)

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """
        Scan a Python module for @on_route-decorated functions and register them.

        Delegates to the internal RouteRegistry.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        self._route_registry.register_from_module(module, module_name)

    def unregister_by_module(self, module_name: str) -> int:
        """
        Remove all routes registered by a specific module.

        Delegates to the internal RouteRegistry.

        :param module_name: The module whose routes should be removed.
        :return: Number of routes removed.
        """
        return self._route_registry.unregister_by_module(module_name)

    async def dispatch(self, request: web.Request) -> web.StreamResponse:
        """
        Dispatch an HTTP request to the appropriate module route handler.

        Extracts module_name and path from the URL, matches it against
        registered routes (supporting path patterns), validates the HTTP
        method, and calls the handler.

        :param request: The aiohttp request object.
        :return: HTTP response.
        """
        module_name = request.match_info["module_name"]
        path = request.match_info.get("path", "")

        relative_path = f"/{path}" if path else "/"

        full_path = f"/owlbot/{module_name}{relative_path}"

        result = self._route_registry.match(full_path)
        mod_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")
        if result is None:
            mod_logger.debug(f"No route registered for '{full_path}'.")
            return web.Response(status=404)

        route_info, match_info = result

        if request.method not in route_info.methods:
            allowed = ", ".join(sorted(route_info.methods))
            mod_logger.debug(
                f"Method {request.method} not allowed for '{full_path}' "
                f"(allowed: {allowed})"
            )
            return web.Response(status=405, headers={"Allow": allowed})

        return await self._handle_module_route(request, route_info, match_info)

    async def _handle_module_route(
        self,
        request: web.Request,
        route_info: RouteInfo,
        match_info: dict[str, str] | None = None,
    ) -> web.StreamResponse:
        """
        Handle an HTTP request to a module-registered route.

        :param request: The aiohttp request object.
        :param route_info: Information about the registered route.
        :param match_info: Captured path parameters from pattern matching.
        :return: HTTP response.
        """
        # Import here to avoid circular imports at module load time.
        from ..api.context import RouteContext

        module_name = route_info.module_name
        mod_logger = logging.getLogger(f"owlbot.modules.{module_name}.routes")

        module_ctx = self._get_module_context(module_name)

        ctx = RouteContext(
            request=request,
            module=module_ctx,
            match_info=match_info if match_info is not None else {},
        )

        logger.debug(
            f"Calling route handler: {route_info.full_path} from module: {module_name}"
        )

        # The checkout acquires a pooled connection for the
        # duration of this route invocation.
        async with module_ctx.storage._checkout():
            try:
                start = time.perf_counter()
                result = await asyncio.wait_for(
                    route_info.handler(ctx), timeout=self._handler_timeout
                )
                elapsed = (time.perf_counter() - start) * 1000

                # Handler succeeded, commit any database changes.
                await module_ctx.storage._commit()

                mod_logger.debug(
                    f"Route handler '{route_info.full_path}' "
                    f"completed in {elapsed:.1f}ms."
                )

                if result is None:
                    return web.Response(status=204)  # No Content.
                elif isinstance(result, web.StreamResponse):
                    return result
                elif isinstance(result, dict):
                    return web.json_response(result)
                else:  # pragma: no branch — defensive against untyped handlers
                    mod_logger.error(  # type: ignore[unreachable]
                        f"Route handler '{route_info.full_path}' returned "
                        f"unsupported type: {type(result).__name__}"
                    )
                    return web.Response(status=500)

            except TimeoutError:
                await module_ctx.storage._rollback()
                mod_logger.warning(
                    f"Route handler '{route_info.full_path}' timed out "
                    f"after {self._handler_timeout}s."
                )
                return web.Response(status=500)
            except Exception as e:
                await module_ctx.storage._rollback()
                mod_logger.exception(
                    f"Route handler '{route_info.full_path}' raised exception: {e}"
                )
                return web.Response(status=500)


class ModuleRoutes:
    """
    Module-scoped wrapper around RouteDispatcher.

    This wrapper auto-supplies the module name for route operations,
    so modules don't need to know the internal routing namespace.
    Follows the same pattern as ModuleCommands.
    """

    def __init__(
        self, dispatcher: RouteDispatcher, module_name: str, public_base_url: str
    ) -> None:
        """
        Initialize the module-scoped routes wrapper.

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
        """
        Build a public URL for a route registered by this module.

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
    ) -> RouteInfo:
        """
        Register a route handler for this module.

        The module name is automatically supplied.

        :param path: URL path relative to module namespace (e.g., "/stats").
        :param handler: Async function to handle the route.
        :param methods: List of HTTP methods (e.g., ["GET"]). Default: ["GET"].
        :return: RouteInfo for the registered route.
        :raises ValueError: If route conflicts with existing route.
        """
        return self._dispatcher.register(
            path=path,
            handler=handler,
            methods=methods,
            module_name=self._module_name,
        )

    def unregister(self, path: str) -> bool:
        """
        Unregister a route by its relative path.

        :param path: Relative route path (e.g., "/stats").
        :return: True if route was found and removed, False otherwise.
        """
        return self._dispatcher.unregister(self._full_path(path))

    def get(self, path: str) -> RouteInfo | None:
        """
        Look up a route by its relative path.

        :param path: Relative route path (e.g., "/stats").
        :return: RouteInfo if found, None otherwise.
        """
        return self._dispatcher.get(self._full_path(path))

    def exists(self, path: str) -> bool:
        """
        Check if a route is registered at the given relative path.

        :param path: Relative route path (e.g., "/stats").
        :return: True if the route exists, False otherwise.
        """
        return self.get(path) is not None

    def _full_path(self, path: str) -> str:
        """
        Normalize a relative path into the full namespaced path.

        :param path: Relative route path (e.g., "/list" or "list").
        :return: Full path (e.g., "/owlbot/quotes/list").
        """
        if not path.startswith("/"):
            path = "/" + path
        return f"/owlbot/{self._module_name}{path}"
