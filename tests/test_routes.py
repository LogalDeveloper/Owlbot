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

"""Unit tests for the route API and route handling infrastructure.

Tests cover the @on_route decorator, RouteInfo dataclass, RouteRegistry
(registration, lookup, matching, unregistration, module scanning),
RouteDispatcher (dispatch, response types, timeout, streaming, error handling),
ModuleRoutes (ownership-scoped wrapper), and RouteContext.
"""

from __future__ import annotations

import asyncio
import types
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from owlbot.api.context import RouteContext
from owlbot.api.routes import RouteInfo, RouteMark, on_route
from owlbot.registries.routes import ModuleRoutes, RouteDispatcher, RouteRegistry
from tests.conftest import make_module_context

if TYPE_CHECKING:
    from owlbot.api.context import ModuleContext


def _make_dispatcher(
    handler_timeout: float = 0.1,
) -> RouteDispatcher:
    """Build a RouteDispatcher with a controllable timeout."""
    return RouteDispatcher(
        get_module_context=lambda name: make_module_context(module_name=name),
        handler_timeout=handler_timeout,
    )


async def _noop(ctx: Any) -> None:
    pass


class TestOnRouteDecorator:
    """Tests the @on_route decorator from owlbot.api.routes."""

    @pytest.mark.parametrize(
        (
            "path",
            "methods",
            "streaming",
            "expected_path",
            "expected_methods",
            "expected_streaming",
        ),
        [
            pytest.param("/stats", None, False, "/stats", None, False, id="defaults"),
            pytest.param(
                "/data",
                ["GET", "POST"],
                False,
                "/data",
                ["GET", "POST"],
                False,
                id="custom-methods",
            ),
            pytest.param("/events", None, True, "/events", None, True, id="streaming"),
            pytest.param(
                "/all", ["PUT"], True, "/all", ["PUT"], True, id="all-options"
            ),
        ],
    )
    def test_sets_route_mark(
        self,
        path: str,
        methods: list[str] | None,
        streaming: bool,
        expected_path: str,
        expected_methods: list[str] | None,
        expected_streaming: bool,
    ) -> None:
        """@on_route sets the _owlbot_route mark with correct fields."""

        @on_route(path, methods=methods, streaming=streaming)
        async def handler(ctx: Any) -> None:
            pass

        mark: RouteMark = handler._owlbot_route  # type: ignore[attr-defined]
        assert mark["path"] == expected_path
        assert mark["methods"] == expected_methods
        assert mark["streaming"] is expected_streaming

    def test_returns_function_unchanged(self) -> None:
        """@on_route returns the original function."""

        async def handler(ctx: Any) -> None:
            pass

        original = handler
        decorated = on_route("/test")(handler)
        assert decorated is original


class TestRouteInfo:
    """Tests RouteInfo from owlbot.api.routes."""

    @pytest.mark.parametrize(
        "streaming",
        [
            pytest.param(True, id="streaming-true"),
            pytest.param(False, id="streaming-false"),
        ],
    )
    def test_stores_streaming(self, streaming: bool) -> None:
        """RouteInfo stores the streaming flag."""
        info = RouteInfo(
            path="/x",
            full_path="/owlbot/mod/x",
            methods=frozenset({"GET"}),
            handler=_noop,
            module_name="mod",
            streaming=streaming,
        )
        assert info.streaming is streaming

    def test_streaming_defaults_false(self) -> None:
        """RouteInfo.streaming defaults to False when not specified."""
        info = RouteInfo(
            path="/x",
            full_path="/owlbot/mod/x",
            methods=frozenset({"GET"}),
            handler=_noop,
            module_name="mod",
        )
        assert info.streaming is False

    def test_fields_accessible(self) -> None:
        """All RouteInfo fields are accessible."""
        info = RouteInfo(
            path="/stats",
            full_path="/owlbot/mod/stats",
            methods=frozenset({"GET", "POST"}),
            handler=_noop,
            module_name="mod",
        )
        assert info.path == "/stats"
        assert info.full_path == "/owlbot/mod/stats"
        assert info.methods == frozenset({"GET", "POST"})
        assert info.handler is _noop
        assert info.module_name == "mod"


class TestRouteRegistry:
    """Tests RouteRegistry from owlbot.registries.routes."""

    def test_register_basic(self) -> None:
        """register() creates a RouteInfo with correct fields."""
        reg = RouteRegistry()
        info = reg.register("/stats", _noop, module_name="mod")
        assert info.path == "/stats"
        assert info.full_path == "/owlbot/mod/stats"
        assert info.methods == frozenset({"GET"})
        assert info.handler is _noop
        assert info.module_name == "mod"

    def test_register_default_method_is_get(self) -> None:
        """register() defaults to GET when methods is None."""
        reg = RouteRegistry()
        info = reg.register("/x", _noop, module_name="mod")
        assert info.methods == frozenset({"GET"})

    def test_register_multiple_methods(self) -> None:
        """register() accepts multiple methods."""
        reg = RouteRegistry()
        info = reg.register("/x", _noop, methods=["GET", "POST"], module_name="mod")
        assert info.methods == frozenset({"GET", "POST"})

    def test_register_uppercases_methods(self) -> None:
        """register() uppercases method names."""
        reg = RouteRegistry()
        info = reg.register("/x", _noop, methods=["get", "post"], module_name="mod")
        assert info.methods == frozenset({"GET", "POST"})

    def test_register_normalizes_path(self) -> None:
        """register() adds leading slash if missing."""
        reg = RouteRegistry()
        info = reg.register("stats", _noop, module_name="mod")
        assert info.path == "/stats"
        assert info.full_path == "/owlbot/mod/stats"

    def test_register_two_handlers_different_methods(self) -> None:
        """Two handlers on same path with different methods both register."""
        reg = RouteRegistry()
        info_get = reg.register("/data", _noop, methods=["GET"], module_name="mod")
        info_post = reg.register("/data", _noop, methods=["POST"], module_name="mod")
        assert info_get.methods == frozenset({"GET"})
        assert info_post.methods == frozenset({"POST"})

    def test_register_conflict_raises(self) -> None:
        """Overlapping methods on same path raises ValueError."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        with pytest.raises(ValueError, match="already has a handler"):
            reg.register("/data", _noop, methods=["GET"], module_name="mod")

    def test_register_partial_overlap_raises(self) -> None:
        """Partial method overlap raises ValueError."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET", "POST"], module_name="mod")
        with pytest.raises(ValueError, match="GET"):
            reg.register("/data", _noop, methods=["GET", "PUT"], module_name="mod")

    def test_register_streaming(self) -> None:
        """register() stores streaming flag on RouteInfo."""
        reg = RouteRegistry()
        info = reg.register("/sse", _noop, module_name="mod", streaming=True)
        assert info.streaming is True

    def test_get_no_method_returns_list(self) -> None:
        """get() without method returns list of all handlers at path."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.register("/data", _noop, methods=["POST"], module_name="mod")
        result = reg.get("/owlbot/mod/data")
        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_with_method_returns_single(self) -> None:
        """get() with method returns matching RouteInfo."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        result = reg.get("/owlbot/mod/data", method="GET")
        assert result is not None
        assert result.methods == frozenset({"GET"})

    def test_get_with_method_returns_none(self) -> None:
        """get() with non-matching method returns None."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        assert reg.get("/owlbot/mod/data", method="POST") is None

    def test_get_unknown_path_no_method(self) -> None:
        """get() for unknown path without method returns empty list."""
        reg = RouteRegistry()
        assert reg.get("/owlbot/mod/nope") == []

    def test_get_unknown_path_with_method(self) -> None:
        """get() for unknown path with method returns None."""
        reg = RouteRegistry()
        assert reg.get("/owlbot/mod/nope", method="GET") is None

    def test_get_case_insensitive_method(self) -> None:
        """get() uppercases the method for lookup."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        assert reg.get("/owlbot/mod/data", method="get") is not None

    def test_match_success(self) -> None:
        """match() returns (RouteInfo, match_dict) on success."""
        reg = RouteRegistry()
        reg.register("/stats", _noop, module_name="mod")
        result = reg.match("/owlbot/mod/stats", "GET")
        assert result[0] is not None
        route_info, match_dict = result
        assert route_info.full_path == "/owlbot/mod/stats"
        assert isinstance(match_dict, dict)

    def test_match_path_params(self) -> None:
        """match() populates match_dict with path parameters."""
        reg = RouteRegistry()
        reg.register("/users/{id}", _noop, module_name="mod")
        result = reg.match("/owlbot/mod/users/42", "GET")
        assert result[0] is not None
        _, match_dict = result
        assert match_dict["id"] == "42"

    def test_get_uses_exact_path_not_pattern(self) -> None:
        """get() requires the literal pattern path; match() resolves concrete paths."""
        reg = RouteRegistry()
        reg.register("/users/{id}", _noop, module_name="mod")
        # get() uses exact string lookup — the pattern itself works.
        assert len(reg.get("/owlbot/mod/users/{id}")) == 1
        # A concrete path does not match via get().
        assert reg.get("/owlbot/mod/users/42") == []
        # match() resolves the concrete path via DynamicResource pattern matching.
        result = reg.match("/owlbot/mod/users/42", "GET")
        assert result[0] is not None

    def test_match_404_raises(self) -> None:
        """match() raises LookupError when no route matches."""
        reg = RouteRegistry()
        with pytest.raises(LookupError):
            reg.match("/owlbot/mod/nope", "GET")

    def test_match_method_not_allowed(self) -> None:
        """match() returns (None, allowed_methods, match_dict) for wrong method."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        result = reg.match("/owlbot/mod/data", "POST")
        assert result[0] is None
        _, allowed_methods, match_dict = result
        assert "GET" in allowed_methods
        assert isinstance(match_dict, dict)

    def test_match_method_not_allowed_multiple(self) -> None:
        """match() returns all allowed methods when multiple handlers exist."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.register("/data", _noop, methods=["POST"], module_name="mod")
        result = reg.match("/owlbot/mod/data", "DELETE")
        assert result[0] is None
        _, allowed_methods, _ = result
        assert allowed_methods == frozenset({"GET", "POST"})

    def test_unregister_all_handlers(self) -> None:
        """unregister() without method removes all handlers at path."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.register("/data", _noop, methods=["POST"], module_name="mod")
        assert reg.unregister("/owlbot/mod/data") is True
        assert reg.get("/owlbot/mod/data") == []

    def test_unregister_per_method(self) -> None:
        """unregister() with method removes only that handler."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.register("/data", _noop, methods=["POST"], module_name="mod")
        assert reg.unregister("/owlbot/mod/data", method="GET") is True
        remaining = reg.get("/owlbot/mod/data")
        assert len(remaining) == 1
        assert remaining[0].methods == frozenset({"POST"})

    def test_unregister_last_handler_removes_group(self) -> None:
        """Removing the last handler at a path removes the group entirely."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.unregister("/owlbot/mod/data", method="GET")
        assert reg.get("/owlbot/mod/data") == []
        with pytest.raises(LookupError):
            reg.match("/owlbot/mod/data", "GET")

    def test_reregister_after_unregister(self) -> None:
        """A path can be re-registered after its handler is removed."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        reg.unregister("/owlbot/mod/data", method="GET")
        # Re-register the same path and method.
        info = reg.register("/data", _noop, methods=["GET"], module_name="mod")
        assert info.full_path == "/owlbot/mod/data"
        assert info.methods == frozenset({"GET"})
        result = reg.match("/owlbot/mod/data", "GET")
        assert result[0] is info

    def test_unregister_unknown_path(self) -> None:
        """unregister() returns False for unknown path."""
        reg = RouteRegistry()
        assert reg.unregister("/owlbot/mod/nope") is False

    def test_unregister_unknown_method(self) -> None:
        """unregister() returns False for unknown method on existing path."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod")
        assert reg.unregister("/owlbot/mod/data", method="DELETE") is False

    def test_get_all(self) -> None:
        """get_all() returns all registered handlers."""
        reg = RouteRegistry()
        reg.register("/a", _noop, module_name="mod")
        reg.register("/b", _noop, module_name="mod")
        assert len(reg.get_all()) == 2

    def test_get_all_cross_module(self) -> None:
        """get_all() returns handlers from all modules."""
        reg = RouteRegistry()
        reg.register("/a", _noop, module_name="mod_a")
        reg.register("/b", _noop, module_name="mod_b")
        result = reg.get_all()
        assert len(result) == 2
        modules = {r.module_name for r in result}
        assert modules == {"mod_a", "mod_b"}

    def test_get_all_empty(self) -> None:
        """get_all() returns empty list when nothing registered."""
        reg = RouteRegistry()
        assert reg.get_all() == []

    def test_get_all_after_unregister(self) -> None:
        """get_all() reflects removals."""
        reg = RouteRegistry()
        reg.register("/a", _noop, module_name="mod")
        reg.register("/b", _noop, module_name="mod")
        assert len(reg.get_all()) == 2
        reg.unregister("/owlbot/mod/a")
        assert len(reg.get_all()) == 1
        assert reg.get_all()[0].path == "/b"

    def test_get_by_module(self) -> None:
        """get_by_module() returns only the specified module's routes."""
        reg = RouteRegistry()
        reg.register("/a", _noop, module_name="mod_a")
        reg.register("/b", _noop, module_name="mod_b")
        result = reg.get_by_module("mod_a")
        assert len(result) == 1
        assert result[0].module_name == "mod_a"

    def test_get_by_module_unknown(self) -> None:
        """get_by_module() returns empty list for unknown module."""
        reg = RouteRegistry()
        assert reg.get_by_module("nope") == []

    def test_unregister_by_module(self) -> None:
        """unregister_by_module() removes all routes for a module."""
        reg = RouteRegistry()
        reg.register("/a", _noop, module_name="mod_a")
        reg.register("/b", _noop, module_name="mod_a")
        reg.register("/c", _noop, module_name="mod_b")
        count = reg.unregister_by_module("mod_a")
        assert count == 2
        assert reg.get_by_module("mod_a") == []
        assert len(reg.get_by_module("mod_b")) == 1

    def test_unregister_by_module_unknown(self) -> None:
        """unregister_by_module() returns 0 for unknown module."""
        reg = RouteRegistry()
        assert reg.unregister_by_module("nope") == 0

    def test_unregister_by_module_shared_path(self) -> None:
        """unregister_by_module() removes only target module's handlers."""
        reg = RouteRegistry()
        reg.register("/data", _noop, methods=["GET"], module_name="mod_a")
        reg.register("/data", _noop, methods=["POST"], module_name="mod_b")
        count = reg.unregister_by_module("mod_a")
        assert count == 1
        remaining = reg.get("/owlbot/mod_a/data")
        # mod_a's handler is gone; mod_b's is still there but under /owlbot/mod_b/data
        assert len(remaining) == 0

    def test_register_from_module(self) -> None:
        """register_from_module() scans decorated functions."""

        @on_route("/stats")
        async def stats_handler(ctx: Any) -> None:
            pass

        @on_route("/events", streaming=True, methods=["GET", "POST"])
        async def events_handler(ctx: Any) -> None:
            pass

        mod = types.ModuleType("fake_mod")
        mod.stats_handler = stats_handler  # type: ignore[attr-defined]
        mod.events_handler = events_handler  # type: ignore[attr-defined]

        reg = RouteRegistry()
        reg.register_from_module(mod, "fake_mod")

        routes = reg.get_by_module("fake_mod")
        assert len(routes) == 2
        paths = {r.path for r in routes}
        assert "/stats" in paths
        assert "/events" in paths

        events_route = next(r for r in routes if r.path == "/events")
        assert events_route.streaming is True
        assert events_route.methods == frozenset({"GET", "POST"})

    def test_register_from_module_ignores_non_decorated(self) -> None:
        """register_from_module() ignores functions without @on_route."""

        async def plain_function(ctx: Any) -> None:
            pass

        mod = types.ModuleType("fake_mod")
        mod.plain_function = plain_function  # type: ignore[attr-defined]

        reg = RouteRegistry()
        reg.register_from_module(mod, "fake_mod")
        assert reg.get_by_module("fake_mod") == []


class TestRouteDispatcherDispatch:
    """Tests RouteDispatcher.dispatch() from owlbot.registries.routes."""

    async def test_dispatch_dict_response(self) -> None:
        """Handler returning dict produces JSON 200 response."""

        async def handler(ctx: RouteContext) -> dict[str, str]:
            return {"status": "ok"}

        dispatcher = _make_dispatcher()
        dispatcher.register("/api", handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/api")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "api"

        response = await dispatcher.dispatch(request)
        assert response.status == 200
        assert response.content_type == "application/json"

    async def test_dispatch_web_response(self) -> None:
        """Handler returning web.Response is passed through."""

        async def handler(ctx: RouteContext) -> web.Response:
            return web.Response(text="hello", status=201)

        dispatcher = _make_dispatcher()
        dispatcher.register("/page", handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/page")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "page"

        response = await dispatcher.dispatch(request)
        assert response.status == 201

    async def test_dispatch_none_response(self) -> None:
        """Handler returning None produces 204 No Content."""

        async def handler(ctx: RouteContext) -> None:
            pass

        dispatcher = _make_dispatcher()
        dispatcher.register("/hook", handler, methods=["POST"], module_name="mod")

        request = make_mocked_request("POST", "/owlbot/mod/hook")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "hook"

        response = await dispatcher.dispatch(request)
        assert response.status == 204

    async def test_dispatch_404(self) -> None:
        """Dispatch to unregistered path returns 404."""
        dispatcher = _make_dispatcher()

        request = make_mocked_request("GET", "/owlbot/mod/nope")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "nope"

        response = await dispatcher.dispatch(request)
        assert response.status == 404

    async def test_dispatch_405(self) -> None:
        """Dispatch with wrong method returns 405 with Allow header."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/data", _noop, methods=["GET"], module_name="mod")

        request = make_mocked_request("POST", "/owlbot/mod/data")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "data"

        response = await dispatcher.dispatch(request)
        assert response.status == 405
        assert "GET" in response.headers["Allow"]

    async def test_dispatch_handler_exception(self) -> None:
        """Handler that raises returns 500."""

        async def bad_handler(ctx: RouteContext) -> web.Response:
            raise RuntimeError("boom")

        dispatcher = _make_dispatcher()
        dispatcher.register("/bad", bad_handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/bad")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "bad"

        response = await dispatcher.dispatch(request)
        assert response.status == 500

    async def test_dispatch_unsupported_return_type(self) -> None:
        """Handler returning unsupported type produces 500."""

        async def bad_return(ctx: RouteContext) -> str:
            return "not a valid return type"

        dispatcher = _make_dispatcher()
        dispatcher.register("/bad", bad_return, module_name="mod")  # type: ignore[arg-type]

        request = make_mocked_request("GET", "/owlbot/mod/bad")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "bad"

        response = await dispatcher.dispatch(request)
        assert response.status == 500

    async def test_dispatch_timeout(self) -> None:
        """Non-streaming handler exceeding timeout returns 500."""

        async def slow(ctx: RouteContext) -> web.Response:
            await asyncio.sleep(0.3)
            return web.Response(text="done")

        dispatcher = _make_dispatcher(handler_timeout=0.1)
        dispatcher.register("/slow", slow, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/slow")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "slow"

        response = await dispatcher.dispatch(request)
        assert response.status == 500

    async def test_dispatch_streaming_bypasses_timeout(self) -> None:
        """Streaming handler exceeding timeout is not cancelled."""

        async def slow_stream(ctx: RouteContext) -> web.Response:
            await asyncio.sleep(0.3)
            return web.Response(text="done")

        dispatcher = _make_dispatcher(handler_timeout=0.1)
        dispatcher.register("/stream", slow_stream, module_name="mod", streaming=True)

        request = make_mocked_request("GET", "/owlbot/mod/stream")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "stream"

        response = await dispatcher.dispatch(request)
        assert response.status == 200

    async def test_dispatch_streaming_exception(self) -> None:
        """Streaming handler that raises returns 500."""

        async def bad_stream(ctx: RouteContext) -> web.Response:
            raise RuntimeError("stream broke")

        dispatcher = _make_dispatcher()
        dispatcher.register("/bad", bad_stream, module_name="mod", streaming=True)

        request = make_mocked_request("GET", "/owlbot/mod/bad")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "bad"

        response = await dispatcher.dispatch(request)
        assert response.status == 500

    async def test_dispatch_streaming_normal_return(self) -> None:
        """Streaming handler returning dict produces JSON response."""

        async def fast_stream(ctx: RouteContext) -> dict[str, str]:
            return {"status": "ok"}

        dispatcher = _make_dispatcher()
        dispatcher.register("/fast", fast_stream, module_name="mod", streaming=True)

        request = make_mocked_request("GET", "/owlbot/mod/fast")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "fast"

        response = await dispatcher.dispatch(request)
        assert response.status == 200

    async def test_dispatch_path_params(self) -> None:
        """Path parameters are populated in RouteContext.match_info."""
        captured: list[dict[str, str]] = []

        async def handler(ctx: RouteContext) -> None:
            captured.append(dict(ctx.match_info))

        dispatcher = _make_dispatcher()
        dispatcher.register("/users/{id}", handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/users/42")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "users/42"

        await dispatcher.dispatch(request)
        assert len(captured) == 1
        assert captured[0]["id"] == "42"

    async def test_dispatch_root_path(self) -> None:
        """Dispatch to module root (no path segment) works."""

        async def handler(ctx: RouteContext) -> dict[str, str]:
            return {"root": "yes"}

        dispatcher = _make_dispatcher()
        dispatcher.register("/", handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = ""

        response = await dispatcher.dispatch(request)
        assert response.status == 200


class TestRouteDispatcherDrainHandlers:
    """Tests RouteDispatcher.drain_handlers() from owlbot.registries.routes."""

    async def test_drain_handlers_cancels_active_handler(self) -> None:
        """drain_handlers() cancels a running streaming handler."""
        entered = asyncio.Event()

        async def forever_stream(ctx: RouteContext) -> web.Response:
            entered.set()
            await asyncio.sleep(3600)
            return web.Response(text="done")

        dispatcher = _make_dispatcher()
        dispatcher.register(
            "/stream", forever_stream, module_name="mod", streaming=True
        )

        request = make_mocked_request("GET", "/owlbot/mod/stream")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "stream"

        # Run dispatch in a task so we can cancel it from the outside.
        task = asyncio.create_task(dispatcher.dispatch(request))
        await entered.wait()

        assert len(dispatcher._streaming_tasks) == 1
        await dispatcher.drain_handlers()
        assert len(dispatcher._streaming_tasks) == 0

        # The dispatch task should now be done (cancelled).
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_drain_handlers_noop_when_empty(self) -> None:
        """drain_handlers() with no active handlers completes immediately."""
        dispatcher = _make_dispatcher()
        await dispatcher.drain_handlers()
        assert len(dispatcher._streaming_tasks) == 0

    async def test_streaming_task_cleaned_up_on_normal_return(self) -> None:
        """Streaming task is removed from tracking after normal completion."""

        async def quick_stream(ctx: RouteContext) -> dict[str, str]:
            return {"ok": "true"}

        dispatcher = _make_dispatcher()
        dispatcher.register("/quick", quick_stream, module_name="mod", streaming=True)

        request = make_mocked_request("GET", "/owlbot/mod/quick")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "quick"

        await dispatcher.dispatch(request)
        assert len(dispatcher._streaming_tasks) == 0

    async def test_streaming_task_cleaned_up_on_exception(self) -> None:
        """Streaming task is removed from tracking after handler exception."""

        async def bad_stream(ctx: RouteContext) -> web.Response:
            raise RuntimeError("boom")

        dispatcher = _make_dispatcher()
        dispatcher.register("/bad", bad_stream, module_name="mod", streaming=True)

        request = make_mocked_request("GET", "/owlbot/mod/bad")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "bad"

        await dispatcher.dispatch(request)
        assert len(dispatcher._streaming_tasks) == 0

    async def test_drain_handlers_multiple_handlers(self) -> None:
        """drain_handlers() cancels all active streaming handlers."""
        entered = asyncio.Event()
        count = 0

        async def forever_stream(ctx: RouteContext) -> web.Response:
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await asyncio.sleep(3600)
            return web.Response(text="done")

        dispatcher = _make_dispatcher()
        dispatcher.register("/s1", forever_stream, module_name="mod", streaming=True)
        dispatcher.register("/s2", forever_stream, module_name="mod", streaming=True)

        request1 = make_mocked_request("GET", "/owlbot/mod/s1")
        request1.match_info["module_name"] = "mod"
        request1.match_info["path"] = "s1"
        request2 = make_mocked_request("GET", "/owlbot/mod/s2")
        request2.match_info["module_name"] = "mod"
        request2.match_info["path"] = "s2"

        task1 = asyncio.create_task(dispatcher.dispatch(request1))
        task2 = asyncio.create_task(dispatcher.dispatch(request2))
        await entered.wait()

        assert len(dispatcher._streaming_tasks) == 2
        await dispatcher.drain_handlers()
        assert len(dispatcher._streaming_tasks) == 0

        with pytest.raises(asyncio.CancelledError):
            await task1
        with pytest.raises(asyncio.CancelledError):
            await task2

    async def test_nonstreaming_task_tracked_and_cleaned_up(self) -> None:
        """Non-streaming handler tasks are tracked and cleaned up."""

        async def handler(ctx: RouteContext) -> dict[str, str]:
            return {"ok": "true"}

        dispatcher = _make_dispatcher()
        dispatcher.register("/api", handler, module_name="mod")

        request = make_mocked_request("GET", "/owlbot/mod/api")
        request.match_info["module_name"] = "mod"
        request.match_info["path"] = "api"

        await dispatcher.dispatch(request)
        assert len(dispatcher._handler_tasks) == 0

    async def test_drain_handlers_waits_for_nonstreaming_first(self) -> None:
        """drain_handlers() waits for non-streaming handlers before cancelling."""
        order: list[str] = []
        handler_entered = asyncio.Event()
        stream_entered = asyncio.Event()

        async def slow_handler(ctx: RouteContext) -> dict[str, str]:
            handler_entered.set()
            await asyncio.sleep(0.15)
            order.append("handler_done")
            return {"ok": "true"}

        async def forever_stream(ctx: RouteContext) -> web.Response:
            stream_entered.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                order.append("stream_cancelled")
                raise
            return web.Response(text="done")

        dispatcher = _make_dispatcher(handler_timeout=5.0)
        dispatcher.register("/api", slow_handler, module_name="mod")
        dispatcher.register(
            "/stream", forever_stream, module_name="mod", streaming=True
        )

        req_handler = make_mocked_request("GET", "/owlbot/mod/api")
        req_handler.match_info["module_name"] = "mod"
        req_handler.match_info["path"] = "api"
        req_stream = make_mocked_request("GET", "/owlbot/mod/stream")
        req_stream.match_info["module_name"] = "mod"
        req_stream.match_info["path"] = "stream"

        handler_task = asyncio.create_task(dispatcher.dispatch(req_handler))
        stream_task = asyncio.create_task(dispatcher.dispatch(req_stream))
        await handler_entered.wait()
        await stream_entered.wait()

        await dispatcher.drain_handlers()

        assert order == ["handler_done", "stream_cancelled"]

        await handler_task
        with pytest.raises(asyncio.CancelledError):
            await stream_task


class TestRouteDispatcherDelegation:
    """Tests RouteDispatcher delegation methods from owlbot.registries.routes."""

    def test_unregister(self) -> None:
        """RouteDispatcher.unregister() delegates to registry."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/x", _noop, module_name="mod")
        assert dispatcher.unregister("/owlbot/mod/x") is True
        assert dispatcher.unregister("/owlbot/mod/x") is False

    def test_get_no_method(self) -> None:
        """RouteDispatcher.get() without method returns list."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/x", _noop, module_name="mod")
        result = dispatcher.get("/owlbot/mod/x")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_with_method(self) -> None:
        """RouteDispatcher.get() with method returns RouteInfo or None."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/x", _noop, module_name="mod")
        assert dispatcher.get("/owlbot/mod/x", method="GET") is not None
        assert dispatcher.get("/owlbot/mod/x", method="POST") is None

    def test_get_by_module(self) -> None:
        """RouteDispatcher.get_by_module() delegates to registry."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/a", _noop, module_name="mod_a")
        dispatcher.register("/b", _noop, module_name="mod_b")
        result = dispatcher.get_by_module("mod_a")
        assert len(result) == 1

    def test_register_from_module(self) -> None:
        """RouteDispatcher.register_from_module() delegates to registry."""

        @on_route("/scan")
        async def handler(ctx: Any) -> None:
            pass

        mod = types.ModuleType("fake")
        mod.handler = handler  # type: ignore[attr-defined]

        dispatcher = _make_dispatcher()
        dispatcher.register_from_module(mod, "fake")
        assert len(dispatcher.get_by_module("fake")) == 1

    def test_unregister_by_module(self) -> None:
        """RouteDispatcher.unregister_by_module() delegates to registry."""
        dispatcher = _make_dispatcher()
        dispatcher.register("/a", _noop, module_name="mod")
        dispatcher.register("/b", _noop, module_name="mod")
        assert dispatcher.unregister_by_module("mod") == 2
        assert dispatcher.get_by_module("mod") == []


class TestModuleRoutes:
    """Tests ModuleRoutes from owlbot.registries.routes."""

    def _make_mod_routes(
        self, *module_names: str
    ) -> tuple[RouteDispatcher, dict[str, ModuleRoutes]]:
        """Build a dispatcher and ModuleRoutes wrappers for given modules."""
        dispatcher = _make_dispatcher(handler_timeout=5.0)
        mod_routes = {
            name: ModuleRoutes(dispatcher, name, "https://example.com")
            for name in module_names
        }
        return dispatcher, mod_routes

    def test_register_auto_supplies_module_name(self) -> None:
        """ModuleRoutes.register() auto-supplies module_name."""
        _, mod_routes = self._make_mod_routes("mod_a")
        info = mod_routes["mod_a"].register("/stats", _noop)
        assert info.module_name == "mod_a"
        assert info.full_path == "/owlbot/mod_a/stats"

    def test_register_streaming(self) -> None:
        """ModuleRoutes.register() passes streaming through."""
        _, mod_routes = self._make_mod_routes("mod_a")
        info = mod_routes["mod_a"].register("/sse", _noop, streaming=True)
        assert info.streaming is True

    def test_register_methods(self) -> None:
        """ModuleRoutes.register() passes methods through."""
        _, mod_routes = self._make_mod_routes("mod_a")
        info = mod_routes["mod_a"].register("/data", _noop, methods=["GET", "POST"])
        assert info.methods == frozenset({"GET", "POST"})

    def test_unregister_by_path(self) -> None:
        """ModuleRoutes.unregister() removes all handlers at path."""
        _, mod_routes = self._make_mod_routes("mod_a")
        mod_routes["mod_a"].register("/stats", _noop)
        assert mod_routes["mod_a"].unregister("/stats") is True
        assert mod_routes["mod_a"].get("/stats") == []

    def test_unregister_by_method(self) -> None:
        """ModuleRoutes.unregister() with method removes only that handler."""
        _, mod_routes = self._make_mod_routes("mod_a")
        mod_routes["mod_a"].register("/data", _noop, methods=["GET"])
        mod_routes["mod_a"].register("/data", _noop, methods=["POST"])
        assert mod_routes["mod_a"].unregister("/data", method="GET") is True
        remaining = mod_routes["mod_a"].get("/data")
        assert len(remaining) == 1

    def test_unregister_unknown(self) -> None:
        """ModuleRoutes.unregister() returns False for unknown path."""
        _, mod_routes = self._make_mod_routes("mod_a")
        assert mod_routes["mod_a"].unregister("/nope") is False

    def test_get_no_method(self) -> None:
        """ModuleRoutes.get() without method returns list."""
        _, mod_routes = self._make_mod_routes("mod_a")
        mod_routes["mod_a"].register("/data", _noop, methods=["GET"])
        mod_routes["mod_a"].register("/data", _noop, methods=["POST"])
        result = mod_routes["mod_a"].get("/data")
        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_with_method(self) -> None:
        """ModuleRoutes.get() with method returns RouteInfo or None."""
        _, mod_routes = self._make_mod_routes("mod_a")
        mod_routes["mod_a"].register("/data", _noop, methods=["GET"])
        assert mod_routes["mod_a"].get("/data", method="GET") is not None
        assert mod_routes["mod_a"].get("/data", method="POST") is None

    def test_get_unknown_path(self) -> None:
        """ModuleRoutes.get() for unknown path returns empty list."""
        _, mod_routes = self._make_mod_routes("mod_a")
        assert mod_routes["mod_a"].get("/nope") == []

    @pytest.mark.parametrize(
        ("register", "check_method", "expected"),
        [
            pytest.param(True, None, True, id="exists-no-method"),
            pytest.param(True, "GET", True, id="exists-matching-method"),
            pytest.param(True, "POST", False, id="exists-wrong-method"),
            pytest.param(False, None, False, id="not-exists-no-method"),
        ],
    )
    def test_exists(
        self,
        register: bool,
        check_method: str | None,
        expected: bool,
    ) -> None:
        """ModuleRoutes.exists() checks for route presence."""
        _, mod_routes = self._make_mod_routes("mod_a")
        if register:
            mod_routes["mod_a"].register("/data", _noop)
        if check_method is not None:
            assert mod_routes["mod_a"].exists("/data", method=check_method) is expected
        else:
            assert mod_routes["mod_a"].exists("/data") is expected

    def test_url_for(self) -> None:
        """ModuleRoutes.url_for() builds correct public URL."""
        _, mod_routes = self._make_mod_routes("mod_a")
        url = mod_routes["mod_a"].url_for("/list")
        assert url == "https://example.com/owlbot/mod_a/list"

    def test_url_for_normalizes_path(self) -> None:
        """ModuleRoutes.url_for() adds leading slash if missing."""
        _, mod_routes = self._make_mod_routes("mod_a")
        url = mod_routes["mod_a"].url_for("list")
        assert url == "https://example.com/owlbot/mod_a/list"

    def test_module_routes_property(self) -> None:
        """module_routes returns only this module's routes."""
        _, mod_routes = self._make_mod_routes("mod_a", "mod_b")
        mod_routes["mod_a"].register("/a", _noop)
        mod_routes["mod_b"].register("/b", _noop)
        routes = mod_routes["mod_a"].module_routes
        assert len(routes) == 1
        assert routes[0].module_name == "mod_a"

    def test_module_routes_property_empty(self) -> None:
        """module_routes returns empty list when nothing registered."""
        _, mod_routes = self._make_mod_routes("mod_a")
        assert mod_routes["mod_a"].module_routes == []


class TestRouteContext:
    """Tests RouteContext from owlbot.api.context."""

    def _make_route_context(
        self,
    ) -> tuple[RouteContext, ModuleContext]:
        """Build a RouteContext with stub parts."""
        module_ctx = make_module_context(module_name="mod_a")
        request = make_mocked_request("GET", "/owlbot/mod_a/test")
        ctx = RouteContext(
            request=request,
            module=module_ctx,
            match_info={"id": "42"},
        )
        return ctx, module_ctx

    @pytest.mark.parametrize(
        ("prop", "use_is"),
        [
            pytest.param("module_name", False, id="module-name"),
            pytest.param("storage", True, id="storage"),
            pytest.param("config", True, id="config"),
            pytest.param("logger", True, id="logger"),
            pytest.param("owncast_client", True, id="owncast-client"),
            pytest.param("http", True, id="http"),
            pytest.param("admin_client", True, id="admin-client"),
            pytest.param("commands", True, id="commands"),
            pytest.param("events", True, id="events"),
            pytest.param("routes", True, id="routes"),
            pytest.param("templates", True, id="templates"),
        ],
    )
    def test_property_proxying(self, prop: str, use_is: bool) -> None:
        """RouteContext properties proxy to the underlying ModuleContext."""
        ctx, module_ctx = self._make_route_context()
        ctx_val = getattr(ctx, prop)
        mod_val = getattr(module_ctx, prop)
        if use_is:
            assert ctx_val is mod_val
        else:
            assert ctx_val == mod_val

    def test_request_accessible(self) -> None:
        """RouteContext.request is the aiohttp Request."""
        ctx, _ = self._make_route_context()
        assert ctx.request is not None
        assert ctx.request.method == "GET"

    def test_match_info_accessible(self) -> None:
        """RouteContext.match_info contains path parameters."""
        ctx, _ = self._make_route_context()
        assert ctx.match_info == {"id": "42"}

    def test_match_info_defaults_empty(self) -> None:
        """RouteContext.match_info defaults to empty dict."""
        module_ctx = make_module_context(module_name="mod_a")
        request = make_mocked_request("GET", "/owlbot/mod_a/test")
        ctx = RouteContext(request=request, module=module_ctx)
        assert ctx.match_info == {}
