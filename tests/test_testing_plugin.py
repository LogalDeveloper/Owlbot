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

"""Tests for owlbot.testing.plugin fixtures."""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import ClientConnectionError

from owlbot.api.config import Config, ModuleConfig
from owlbot.api.event_types import EventType
from owlbot.api.events import Priority
from owlbot.api.http_client import HttpClient
from owlbot.api.storage import ModuleStorage
from owlbot.api.templates import ModuleTemplates
from owlbot.builtin_modules import quotes as quotes_pkg
from owlbot.registries.events import EventDispatcher
from owlbot.testing import (
    make_chat_event,
    make_user,
    make_user_joined_event,
)
from owlbot.testing.stubs import (
    RecordingOwncastAdminClient,
    RecordingOwncastClient,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from aiohttp.test_utils import TestClient
    from aioresponses import aioresponses

    from owlbot.api.context import CommandContext, EventContext, ModuleContext
    from owlbot.api.event_types import UserJoinedEvent
    from owlbot.registries.commands import CommandDispatcher
    from owlbot.registries.routes import RouteDispatcher


class TestConfigurationFixtures:
    """Verify default values of overridable configuration fixtures."""

    def test_module_pkg_default(self, module_pkg: ModuleType | None) -> None:
        """module_pkg defaults to None."""
        assert module_pkg is None

    def test_admin_client_enabled_default(self, *, admin_client_enabled: bool) -> None:
        """admin_client_enabled defaults to False."""
        assert admin_client_enabled is False

    def test_command_prefix_default(self, command_prefix: str) -> None:
        """command_prefix defaults to '!'."""
        assert command_prefix == "!"

    def test_handler_timeout_default(self, handler_timeout: float) -> None:
        """handler_timeout defaults to 30.0."""
        assert handler_timeout == 30.0

    def test_config_data_default(self, config_data: dict[str, Any]) -> None:
        """Config_data seeds owncast.url and a public_base_url matching Owlbot."""
        assert config_data == {
            "owncast": {"url": "http://localhost:8080"},
            "owlbot": {"public_base_url": "http://localhost:8081"},
        }


class TestConfigFixtures:
    """Verify the config_path / config fixture chain."""

    def test_config_path_file_exists(self, config_path: Path) -> None:
        """config_path writes the seed YAML to a real file."""
        assert config_path.is_file()
        assert config_path.read_text().strip()

    def test_config_is_real_instance(self, config: Config) -> None:
        """Config fixture returns a real Config backed by config_path."""
        assert isinstance(config, Config)
        assert config.owncast_url == "http://localhost:8080"

    def test_module_config_set_persists_to_disk(
        self, module_config: ModuleConfig, config_path: Path
    ) -> None:
        """module_config.set writes through to the backing YAML file."""
        module_config.set("greeting", "hi")
        assert "greeting: hi" in config_path.read_text()


class TestConfigDataOverride:
    """Overriding config_data flows into the real Config + ModuleConfig."""

    @pytest.fixture
    def config_data(self) -> dict[str, Any]:
        """Override config_data to set a custom public_base_url."""
        return {
            "owncast": {"url": "http://localhost:8080"},
            "owlbot": {"public_base_url": "https://bot.example.com"},
        }

    def test_module_config_reads_override(self, module_config: ModuleConfig) -> None:
        """module_config.public_base_url reflects the seeded YAML value."""
        assert module_config.public_base_url == "https://bot.example.com"

    def test_module_context_routes_use_override(
        self, module_context: ModuleContext
    ) -> None:
        """ModuleRoutes.url_for builds URLs from the overridden base."""
        assert module_context.routes.url_for("list").startswith(
            "https://bot.example.com/"
        )


class TestOwncastClientBaseUrl:
    """owncast_client.base_url tracks config.owncast_url, including overrides."""

    def test_default_matches_default_config(
        self, owncast_client: RecordingOwncastClient, config: Config
    ) -> None:
        """With default config_data, stub base_url equals config.owncast_url."""
        assert owncast_client.base_url == config.owncast_url

    class TestWithOverride:
        """Overriding config_data.owncast.url propagates to the stub."""

        @pytest.fixture
        def config_data(self) -> dict[str, Any]:
            """Override owncast.url to a distinct value."""
            return {
                "owncast": {"url": "https://stream.example.com"},
                "owlbot": {"public_base_url": "http://localhost:8081"},
            }

        def test_stub_base_url_follows_override(
            self, owncast_client: RecordingOwncastClient
        ) -> None:
            """Stub base_url reflects the overridden owncast.url."""
            assert owncast_client.base_url == "https://stream.example.com"

    class TestTrailingSlashStripped:
        """Stub strips trailing slashes from owncast_url, matching the real client."""

        @pytest.fixture
        def config_data(self) -> dict[str, Any]:
            """Seed owncast.url with a trailing slash."""
            return {
                "owncast": {"url": "https://stream.example.com/"},
                "owlbot": {"public_base_url": "http://localhost:8081"},
            }

        def test_stub_strips_trailing_slash(
            self, owncast_client: RecordingOwncastClient
        ) -> None:
            """Stub base_url drops the trailing slash like OwncastClient does."""
            assert owncast_client.base_url == "https://stream.example.com"


class TestModulePkgDerivation:
    """Overriding module_pkg auto-wires derived fixtures."""

    @pytest.fixture
    def module_pkg(self) -> ModuleType:
        """Override module_pkg with the quotes package."""
        return quotes_pkg

    def test_module_name_derives_from_module_pkg(
        self, module_context: ModuleContext
    ) -> None:
        """module_context.module_name is the last segment of the package name."""
        assert module_context.module_name == "quotes"

    def test_templates_renders_module_template(
        self, module_context: ModuleContext
    ) -> None:
        """module_context.templates resolves module-specific files."""
        page = module_context.templates.render("list.html", quotes=[])
        assert "No quotes have been added yet." in page


class TestTemplatesFallsBackToCore:
    """A module without its own templates/ directory still renders core templates."""

    @pytest.fixture
    def module_pkg(self, tmp_path: Any) -> ModuleType:
        """Override module_pkg with a fake package pointing at an empty tmp_path."""
        fake = types.ModuleType("fake_module_no_templates")
        fake.__file__ = str(tmp_path / "__init__.py")
        return fake

    def test_renders_core_template(self, templates: ModuleTemplates) -> None:
        """base.html resolves from the core templates directory."""
        assert templates.render("base.html")


class TestFixturesValidateConfiguration:
    """Fixtures raise pytest.UsageError when their prerequisite knobs are unset.

    Each test spins up a nested pytest run via ``pytester`` so the failure
    surfaces through the real fixture resolution path instead of calling the
    fixture function directly.
    """

    def _run_plugin_test(
        self, pytester: pytest.Pytester, body: str, *, asyncio_mode: bool = False
    ) -> pytest.RunResult:
        """Run a one-test pytester session that loads the Owlbot testing plugin."""
        ini = "[pytest]\nasyncio_default_fixture_loop_scope = function\n"
        if asyncio_mode:
            ini += "asyncio_mode = auto\n"
        pytester.makeini(ini)
        pytester.makeconftest('pytest_plugins = ["owlbot.testing.plugin"]\n')
        pytester.makepyfile(body)
        return pytester.runpytest()

    def test_registered_handlers_raises_without_module_pkg(
        self, pytester: pytest.Pytester
    ) -> None:
        """registered_handlers raises pytest.UsageError when module_pkg is None."""
        result = self._run_plugin_test(
            pytester,
            """
            def test_uses_handlers(registered_handlers):
                pass
            """,
        )
        result.assert_outcomes(errors=1)
        assert "module_pkg" in result.stdout.str()

    def test_module_lifecycle_raises_without_module_pkg(
        self, pytester: pytest.Pytester
    ) -> None:
        """module_lifecycle raises pytest.UsageError when module_pkg is None."""
        result = self._run_plugin_test(
            pytester,
            """
            async def test_uses_lifecycle(module_lifecycle):
                pass
            """,
            asyncio_mode=True,
        )
        result.assert_outcomes(errors=1)
        assert "module_pkg" in result.stdout.str()

    def test_admin_client_raises_when_disabled(self, pytester: pytest.Pytester) -> None:
        """admin_client raises pytest.UsageError when admin_client_enabled is False."""
        result = self._run_plugin_test(
            pytester,
            """
            def test_uses_admin_client(admin_client):
                pass
            """,
        )
        result.assert_outcomes(errors=1)
        assert "admin_client_enabled" in result.stdout.str()


@pytest.mark.usefixtures("registered_handlers")
class TestRegisteredHandlersWithPkg:
    """With module_pkg set, registered_handlers registers everything."""

    @pytest.fixture
    def module_pkg(self) -> ModuleType:
        """Override module_pkg with the quotes package."""
        return quotes_pkg

    def test_commands_registered(self, command_dispatcher: CommandDispatcher) -> None:
        """Quotes module commands are registered."""
        registered = command_dispatcher.get_by_module("quotes")
        assert "quote" in registered

    def test_routes_registered(self, route_dispatcher: RouteDispatcher) -> None:
        """Quotes module routes are registered."""
        assert route_dispatcher.get_by_module("quotes") != []

    def test_state_not_populated(self, module_context: ModuleContext) -> None:
        """registered_handlers skips @on_setup; ctx.state stays empty."""
        assert "manager" not in module_context.state


@pytest.mark.usefixtures("module_lifecycle")
class TestModuleLifecycleWithPkg:
    """With module_pkg set, module_lifecycle runs handlers + @on_setup."""

    @pytest.fixture
    def module_pkg(self) -> ModuleType:
        """Override module_pkg with the quotes package."""
        return quotes_pkg

    def test_commands_registered(self, command_dispatcher: CommandDispatcher) -> None:
        """Decorator-registered commands are wired (via registered_handlers)."""
        assert "quote" in command_dispatcher.get_by_module("quotes")

    def test_setup_populates_state(self, module_context: ModuleContext) -> None:
        """@on_setup ran and populated ctx.state."""
        assert module_context.state.get("manager") is not None


class TestBuildingBlockFixtures:
    """Verify building block fixtures produce usable instances."""

    async def test_storage_is_open_and_usable(self, storage: ModuleStorage) -> None:
        """Storage fixture yields an open in-memory database."""
        await storage.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        await storage.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        row = await storage.fetch_one("SELECT val FROM t WHERE id = 1")
        assert row is not None
        assert row[0] == "hello"

    def test_owncast_client_is_recording(
        self, owncast_client: RecordingOwncastClient
    ) -> None:
        """owncast_client fixture returns a RecordingOwncastClient with empty calls."""
        assert isinstance(owncast_client, RecordingOwncastClient)
        assert owncast_client.calls == []

    def test_module_config_is_real_instance(self, module_config: ModuleConfig) -> None:
        """module_config fixture returns a real ModuleConfig with enabled=True."""
        assert isinstance(module_config, ModuleConfig)
        assert module_config.enabled is True


class TestModuleContext:
    """Verify the module_context fixture wires everything together."""

    def test_module_name_defaults_to_test_module(
        self, module_context: ModuleContext
    ) -> None:
        """module_context.module_name is 'test_module' when no module_pkg is set."""
        assert module_context.module_name == "test_module"

    def test_storage_is_wired(self, module_context: ModuleContext) -> None:
        """module_context.storage is a ModuleStorage instance."""
        assert isinstance(module_context.storage, ModuleStorage)

    def test_owncast_client_is_recording(self, module_context: ModuleContext) -> None:
        """module_context.owncast_client is a RecordingOwncastClient."""
        assert isinstance(module_context.owncast_client, RecordingOwncastClient)

    def test_config_is_real_module_config(self, module_context: ModuleContext) -> None:
        """module_context.config is a real ModuleConfig."""
        assert isinstance(module_context.config, ModuleConfig)

    def test_admin_client_none_by_default(self, module_context: ModuleContext) -> None:
        """admin_client is None when admin_client_enabled is False."""
        assert module_context.admin_client is None

    def test_templates_is_core_only_renderer_by_default(
        self, module_context: ModuleContext
    ) -> None:
        """module_context.templates is a core-only renderer without module_pkg."""
        assert isinstance(module_context.templates, ModuleTemplates)
        # Core layout should resolve even without a module package.
        assert module_context.templates.env.get_template("base.html") is not None

    def test_state_is_empty_dict(self, module_context: ModuleContext) -> None:
        """module_context.state starts as an empty dict."""
        assert module_context.state == {}

    def test_commands_wired(self, module_context: ModuleContext) -> None:
        """module_context.commands is not None."""
        assert module_context.commands is not None

    def test_events_wired(self, module_context: ModuleContext) -> None:
        """module_context.events is not None."""
        assert module_context.events is not None

    def test_routes_wired(self, module_context: ModuleContext) -> None:
        """module_context.routes is not None."""
        assert module_context.routes is not None

    def test_event_dispatcher_available(
        self, event_dispatcher: EventDispatcher
    ) -> None:
        """event_dispatcher fixture is an EventDispatcher instance."""
        assert isinstance(event_dispatcher, EventDispatcher)


class TestAdminClientEnabled:
    """Tests that admin_client_enabled=True wires the admin client."""

    @pytest.fixture
    def admin_client_enabled(self) -> bool:
        """Override admin_client_enabled to True."""
        return True

    def test_admin_client_wired(self, module_context: ModuleContext) -> None:
        """admin_client is a RecordingOwncastAdminClient when enabled."""
        assert module_context.admin_client is not None
        assert isinstance(module_context.admin_client, RecordingOwncastAdminClient)


class TestHttpFixtures:
    """Tests for the http and mocked_http fixtures."""

    async def test_http_is_httpclient(self, http: HttpClient) -> None:
        """The http fixture yields a started HttpClient."""
        assert isinstance(http, HttpClient)
        assert not http.session.closed

    async def test_module_context_http_is_wired(
        self, module_context: ModuleContext, http: HttpClient
    ) -> None:
        """module_context.http is the same HttpClient as the http fixture."""
        assert module_context.http is http

    async def test_mocked_response_is_returned(
        self, http: HttpClient, mocked_http: aioresponses
    ) -> None:
        """Registered URLs return the canned response."""
        mocked_http.get("https://example.test/data", payload={"ok": True})
        async with http.get("https://example.test/data") as resp:
            assert resp.status == 200
            assert await resp.json() == {"ok": True}

    async def test_unregistered_url_raises_connection_error(
        self, http: HttpClient
    ) -> None:
        """Unregistered URLs raise ClientConnectionError rather than hitting the net."""
        with pytest.raises(ClientConnectionError):
            async with http.get("https://unregistered.test/data"):
                pass


class TestEventDispatcher:
    """Integration tests exercising the full event dispatch pipeline."""

    async def test_command_dispatch_through_events(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Dispatch a CHAT event for a registered command; verify it ran."""

        async def handler(ctx: CommandContext) -> None:
            await ctx.owncast_client.send_message("pong")

        module_context.commands.register("ping", handler)

        event = make_chat_event(raw_body="!ping")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert len(owncast_client.calls) == 1
        assert owncast_client.calls[0][0] == "send_message"
        assert owncast_client.calls[0][1]["body"] == "pong"

    async def test_event_handler_receives_event(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """Register an event handler for USER_JOINED, verify it receives the event."""
        received: list[UserJoinedEvent] = []

        async def handler(ctx: EventContext[UserJoinedEvent]) -> None:
            received.append(ctx.event)

        module_context.events.register(handler, (EventType.USER_JOINED,))

        event = make_user_joined_event()
        await event_dispatcher.dispatch(EventType.USER_JOINED, event)

        assert len(received) == 1
        assert received[0] is event

    async def test_propagation_stops_command_dispatch(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """A HIGHEST-priority event handler that stops propagation blocks commands."""

        async def blocker(ctx: EventContext[object]) -> None:
            ctx.stop_propagation("blocked")

        module_context.events.register(
            blocker, (EventType.CHAT,), priority=Priority.HIGHEST
        )

        async def cmd_handler(ctx: CommandContext) -> None:
            await ctx.owncast_client.send_message("should not run")

        module_context.commands.register("test", cmd_handler)

        event = make_chat_event(raw_body="!test")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls == []

    async def test_permission_denied_sends_system_message(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Authenticated-only command from unauth user sends denial."""

        async def handler(ctx: CommandContext) -> None:
            await ctx.owncast_client.send_message("should not run")

        module_context.commands.register("secret", handler, requires_authenticated=True)

        user = make_user(is_authenticated=False)
        event = make_chat_event(raw_body="!secret", user=user, client_id=42)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert len(owncast_client.calls) == 1
        assert owncast_client.calls[0][0] == "send_system_message_to_client"


class TestRouteClient:
    """Integration tests exercising the route_client fixture."""

    async def test_get_registered_route(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET a registered route; verify 200 and JSON body."""

        async def handler(ctx: Any) -> dict[str, int]:
            return {"total": 42}

        module_context.routes.register("/stats", handler, methods=["GET"])

        resp = await route_client.get("/owlbot/test_module/stats")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"total": 42}

    async def test_unregistered_route_returns_404(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET a path with no registered handler; verify 404."""
        resp = await route_client.get("/owlbot/test_module/nonexistent")
        assert resp.status == 404

    async def test_module_root_reachable_without_trailing_slash(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """A root-registered handler is reachable via /owlbot/{module_name}.

        Production registers two aiohttp routes for the catch-all so that
        root handlers respond to both ``/owlbot/{module_name}`` and
        ``/owlbot/{module_name}/``; the plugin must mirror that or modules
        with root routes will silently 404 in tests but work in production.
        """

        async def handler(ctx: Any) -> dict[str, str]:
            return {"status": "root"}

        module_context.routes.register("/", handler, methods=["GET"])

        resp = await route_client.get("/owlbot/test_module")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "root"}

    async def test_post_route(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """POST to a registered route; verify 200."""

        async def handler(ctx: Any) -> dict[str, str]:
            return {"status": "created"}

        module_context.routes.register("/items", handler, methods=["POST"])

        resp = await route_client.post("/owlbot/test_module/items")
        assert resp.status == 200

    async def test_method_not_allowed(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """Register a POST-only route, GET it; verify 405."""

        async def handler(ctx: Any) -> dict[str, str]:
            return {"status": "ok"}

        module_context.routes.register("/submit", handler, methods=["POST"])

        resp = await route_client.get("/owlbot/test_module/submit")
        assert resp.status == 405

    async def test_none_response_returns_204(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """Handler returning None; verify 204."""

        async def handler(ctx: Any) -> None:
            return None

        module_context.routes.register("/noop", handler, methods=["GET"])

        resp = await route_client.get("/owlbot/test_module/noop")
        assert resp.status == 204
