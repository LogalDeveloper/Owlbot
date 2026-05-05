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

"""Unit tests for the command API and command handling infrastructure.

Tests cover the @on_command decorator, CommandEvent/CommandInfo dataclasses,
CommandRegistry (trigger mapping, alias resolution, conflict detection, module
scanning), CommandDispatcher (parsing, permission checks, cooldowns,
timeout handling, built-in commands), ModuleCommands
(ownership-scoped wrapper), and CommandContext.
"""

from __future__ import annotations

import asyncio
import logging
import types
from typing import TYPE_CHECKING, Any

import pytest

from owlbot.api.commands import CommandEvent, CommandInfo, CommandMark, on_command
from owlbot.api.context import CommandContext, EventContext
from owlbot.registries.commands import (
    CommandDispatcher,
    CommandRegistry,
    ModuleCommands,
)
from owlbot.sessions import SessionManager
from owlbot.testing import RecordingOwncastClient, make_chat_event, make_user

if TYPE_CHECKING:
    from collections.abc import Callable

    from owlbot.api.context import ModuleContext
    from owlbot.api.event_types import ChatEvent
    from owlbot.api.storage import ModuleStorage


class TestOnCommandDecorator:
    """Tests the @on_command decorator from owlbot.api.commands."""

    @pytest.mark.parametrize(
        ("name", "aliases", "auth", "mod", "cooldown"),
        [
            pytest.param("ping", None, False, False, 0, id="defaults"),
            pytest.param("cmd", ["c", "cm"], False, False, 0, id="with-aliases"),
            pytest.param("secret", None, True, False, 0, id="requires-auth"),
            pytest.param("ban", None, False, True, 0, id="requires-mod"),
            pytest.param("spam", None, False, False, 30, id="with-cooldown"),
            pytest.param("full", ["f"], True, True, 10, id="all-options"),
        ],
    )
    def test_sets_command_mark(
        self,
        name: str,
        aliases: list[str] | None,
        *,
        auth: bool,
        mod: bool,
        cooldown: int,
    ) -> None:
        """Decorator attaches an _owlbot_command mark with correct fields."""

        async def handler(ctx: CommandContext) -> None:
            pass

        decorated = on_command(
            name,
            aliases=aliases,
            requires_authenticated=auth,
            requires_moderator=mod,
            cooldown=cooldown,
        )(handler)
        mark: CommandMark = decorated._owlbot_command  # type: ignore[attr-defined]
        assert mark["name"] == name
        assert mark["aliases"] == aliases
        assert mark["requires_authenticated"] is auth
        assert mark["requires_moderator"] is mod
        assert mark["cooldown"] == cooldown

    def test_returns_function_unchanged(self) -> None:
        """Decorated function is the original function object (identity check)."""

        async def handler(ctx: CommandContext) -> None:
            pass

        decorated = on_command("ping")(handler)
        assert decorated is handler


class TestCommandEvent:
    """Tests CommandEvent dataclass from owlbot.api.commands."""

    def test_fields_accessible(self) -> None:
        """Construct with known values; assert each field matches."""
        event = make_chat_event(client_id=42)
        cmd_event = CommandEvent(
            command="greet",
            args="hello world",
            args_list=["hello", "world"],
            prefix="!",
            chat_event=event,
        )
        assert cmd_event.command == "greet"
        assert cmd_event.args == "hello world"
        assert cmd_event.args_list == ["hello", "world"]
        assert cmd_event.prefix == "!"
        assert cmd_event.chat_event is event


class TestCommandInfo:
    """Tests CommandInfo from owlbot.api.commands."""

    def test_defaults(self) -> None:
        """Construct with required fields only; defaults are correct."""

        async def handler(ctx: CommandContext) -> None:
            pass

        info = CommandInfo(name="ping", handler=handler, module_name="mod_a")
        assert info.aliases == frozenset()
        assert info.requires_authenticated is False
        assert info.requires_moderator is False
        assert info.cooldown == 0

    def test_all_triggers_includes_name_and_aliases(self) -> None:
        """all_triggers contains the primary name and all aliases."""

        async def handler(ctx: CommandContext) -> None:
            pass

        info = CommandInfo(
            name="ping",
            handler=handler,
            module_name="mod_a",
            aliases=frozenset({"p", "pong"}),
        )
        assert info.all_triggers == frozenset({"ping", "p", "pong"})

    def test_all_triggers_name_only(self) -> None:
        """all_triggers contains only the name when there are no aliases."""

        async def handler(ctx: CommandContext) -> None:
            pass

        info = CommandInfo(name="ping", handler=handler, module_name="mod_a")
        assert info.all_triggers == frozenset({"ping"})


class TestCommandRegistry:
    """Tests CommandRegistry from owlbot.registries.commands."""

    def test_register_and_get(self) -> None:
        """Register 'ping' with module 'mod_a'; get('ping') returns correct info."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        info = registry.get("ping")
        assert info is not None
        assert info.name == "ping"
        assert info.handler is handler
        assert info.module_name == "mod_a"

    def test_register_lowercases_name(self) -> None:
        """Registering 'PING' allows lookup via 'ping' and 'PING'."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("PING", handler, module_name="mod_a")
        assert registry.get("ping") is not None
        assert registry.get("PING") is not None

    def test_register_with_aliases(self) -> None:
        """Aliases resolve to the same CommandInfo with canonical name."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, aliases=["p", "pong"], module_name="mod_a")
        info_p = registry.get("p")
        info_pong = registry.get("pong")
        assert info_p is not None
        assert info_p.name == "ping"
        assert info_pong is not None
        assert info_pong.name == "ping"

    def test_register_lowercases_aliases(self) -> None:
        """Alias 'P' is stored lowercase and accessible via 'p'."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, aliases=["P"], module_name="mod_a")
        assert registry.get("p") is not None

    def test_register_conflict_name(self) -> None:
        """Registering the same name twice raises ValueError."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        with pytest.raises(
            ValueError,
            match=r"Command trigger 'ping' conflicts with existing command 'ping'",
        ):
            registry.register("ping", handler, module_name="mod_b")

    def test_register_conflict_alias_with_name(self) -> None:
        """Alias that conflicts with an existing command name raises ValueError."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        with pytest.raises(
            ValueError,
            match=r"Command trigger 'ping' conflicts with existing command 'ping'",
        ):
            registry.register("other", handler, aliases=["ping"], module_name="mod_b")

    def test_register_conflict_alias_with_alias(self) -> None:
        """Alias that conflicts with an existing alias raises ValueError."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("cmd1", handler, aliases=["c"], module_name="mod_a")
        with pytest.raises(
            ValueError,
            match=r"Command trigger 'c' conflicts with existing command 'cmd1'",
        ):
            registry.register("cmd2", handler, aliases=["c"], module_name="mod_b")

    def test_exists_true(self) -> None:
        """Registered command returns True."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        assert registry.exists("ping") is True

    def test_exists_false(self) -> None:
        """Unknown command returns False."""
        registry = CommandRegistry()
        assert registry.exists("nope") is False

    def test_exists_by_alias(self) -> None:
        """Lookup by alias returns True."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, aliases=["p"], module_name="mod_a")
        assert registry.exists("p") is True

    def test_exists_case_insensitive(self) -> None:
        """exists('PING') returns True for registered 'ping'."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        assert registry.exists("PING") is True

    def test_get_all_empty(self) -> None:
        """Fresh registry returns empty dict."""
        registry = CommandRegistry()
        assert registry.get_all() == {}

    def test_get_all_returns_copy(self) -> None:
        """Mutating returned dict doesn't affect the registry."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        all_cmds = registry.get_all()
        all_cmds.clear()
        assert registry.get("ping") is not None

    def test_get_all_multiple(self) -> None:
        """Register 'ping' and 'quote'; both keys present."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        registry.register("quote", handler, module_name="mod_a")
        all_cmds = registry.get_all()
        assert "ping" in all_cmds
        assert "quote" in all_cmds
        assert len(all_cmds) == 2

    def test_unregister_by_name(self) -> None:
        """unregister('ping') returns True; get('ping') returns None."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        assert registry.unregister("ping") is True
        assert registry.get("ping") is None

    def test_unregister_removes_aliases(self) -> None:
        """After unregister, aliases also resolve to None."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, aliases=["p"], module_name="mod_a")
        registry.unregister("ping")
        assert registry.get("p") is None

    def test_unregister_by_alias(self) -> None:
        """Unregister by alias removes the primary command too."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, aliases=["p"], module_name="mod_a")
        assert registry.unregister("p") is True
        assert registry.get("ping") is None

    def test_unregister_missing(self) -> None:
        """Unregistering a non-existent command returns False."""
        registry = CommandRegistry()
        assert registry.unregister("nope") is False

    def test_unregister_by_module(self) -> None:
        """unregister_by_module removes the right module's commands."""
        registry = CommandRegistry()

        async def handler(ctx: CommandContext) -> None:
            pass

        registry.register("ping", handler, module_name="mod_a")
        registry.register("pong", handler, module_name="mod_a")
        registry.register("quote", handler, module_name="mod_b")
        count = registry.unregister_by_module("mod_a")
        assert count == 2
        assert registry.get("ping") is None
        assert registry.get("pong") is None
        assert registry.get("quote") is not None

    def test_unregister_by_module_nonexistent(self) -> None:
        """unregister_by_module returns 0 for unknown module."""
        registry = CommandRegistry()
        assert registry.unregister_by_module("no_such") == 0

    @pytest.mark.parametrize(
        ("message", "prefix", "expected"),
        [
            pytest.param("!ping", "!", ("ping", ""), id="simple-command"),
            pytest.param(
                "!quote add hello world",
                "!",
                ("quote", "add hello world"),
                id="command-with-args",
            ),
            pytest.param("!PING", "!", ("ping", ""), id="command-uppercase"),
            pytest.param("hello world", "!", None, id="not-a-command"),
            pytest.param("!", "!", None, id="empty-after-prefix"),
            pytest.param("!   ", "!", None, id="prefix-only-whitespace"),
            pytest.param("?ping", "?", ("ping", ""), id="custom-prefix"),
        ],
    )
    def test_parse(
        self,
        message: str,
        prefix: str,
        expected: tuple[str, str] | None,
    ) -> None:
        """parse() extracts command and args or returns None."""
        registry = CommandRegistry(prefix=prefix)
        assert registry.parse(message) == expected

    def test_register_from_module(self) -> None:
        """register_from_module scans for decorated functions and registers them."""
        registry = CommandRegistry()

        async def marked_handler(ctx: CommandContext) -> None:
            pass

        marked_handler._owlbot_command = CommandMark(  # type: ignore[attr-defined]
            name="ping",
            aliases=None,
            requires_authenticated=False,
            requires_moderator=False,
            cooldown=0,
        )

        async def plain_function(ctx: CommandContext) -> None:
            pass

        mod = types.ModuleType("fake_module")
        mod.marked_handler = marked_handler  # type: ignore[attr-defined]
        mod.plain_function = plain_function  # type: ignore[attr-defined]

        registry.register_from_module(mod, "mod_a")
        info = registry.get("ping")
        assert info is not None
        assert info.name == "ping"
        assert info.handler is marked_handler
        assert info.module_name == "mod_a"
        assert registry.get("plain_function") is None


class TestCommandDispatcherDispatch:
    """Tests CommandDispatcher.dispatch() from owlbot.registries.commands."""

    def _make_dispatcher(
        self,
        module_context: ModuleContext,
        *,
        owncast_client: RecordingOwncastClient | None = None,
        handler_timeout: float = 5.0,
        command_prefix: str = "!",
        session_manager: SessionManager | None = None,
        public_base_url: str | None = None,
    ) -> tuple[CommandDispatcher, ModuleContext, RecordingOwncastClient]:
        """Build a CommandDispatcher wired to the given ModuleContext."""
        stub = owncast_client or RecordingOwncastClient()
        resolved_session_manager = (
            session_manager if session_manager is not None else SessionManager()
        )
        resolved_public_base_url = (
            public_base_url
            if public_base_url is not None
            else module_context.config.public_base_url
        )
        dispatcher = CommandDispatcher(
            get_module_context=lambda _name: module_context,
            owncast_client=stub,
            handler_timeout=handler_timeout,
            loaded_modules=set(),
            command_prefix=command_prefix,
            session_manager=resolved_session_manager,
            public_base_url=resolved_public_base_url,
        )
        return dispatcher, module_context, stub

    async def test_dispatches_command(self, module_context: ModuleContext) -> None:
        """Handler receives a CommandContext with correct command and chat_event."""
        captured: list[CommandContext] = []

        async def handler(ctx: CommandContext) -> None:
            captured.append(ctx)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, module_name="mod_a")
        event = make_chat_event(client_id=42, raw_body="!ping")
        await dispatcher.dispatch(event)

        assert len(captured) == 1
        assert captured[0].command == "ping"
        assert captured[0].args == ""
        assert captured[0].chat_event is event

    async def test_dispatches_with_args(self, module_context: ModuleContext) -> None:
        """Handler receives parsed args and args_list."""
        captured: list[CommandContext] = []

        async def handler(ctx: CommandContext) -> None:
            captured.append(ctx)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("greet", handler, module_name="mod_a")
        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!greet hello world")
        )

        assert captured[0].args == "hello world"
        assert captured[0].args_list == ["hello", "world"]

    async def test_dispatches_via_alias(self, module_context: ModuleContext) -> None:
        """Alias resolves to canonical name in ctx.command."""
        captured: list[CommandContext] = []

        async def handler(ctx: CommandContext) -> None:
            captured.append(ctx)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, aliases=["p"], module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!p"))

        assert captured[0].command == "ping"

    async def test_non_command_message_ignored(
        self, module_context: ModuleContext
    ) -> None:
        """A regular message does not invoke the handler."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, module_name="mod_a")
        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="hello everyone")
        )

        assert called is False

    async def test_unknown_command_ignored(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unknown command is ignored and logged at DEBUG level."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, module_name="mod_a")
        with caplog.at_level(logging.DEBUG, logger="owlbot.commands"):
            await dispatcher.dispatch(
                make_chat_event(client_id=42, raw_body="!nonexistent")
            )

        assert called is False
        assert "Unknown command: nonexistent" in caplog.text

    async def test_requires_authenticated_allowed(
        self, module_context: ModuleContext
    ) -> None:
        """Authenticated user can invoke a requires_authenticated command."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register(
            "ping", handler, requires_authenticated=True, module_name="mod_a"
        )
        user = make_user(is_authenticated=True)
        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!ping", user=user)
        )

        assert called is True

    async def test_requires_authenticated_denied(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unauthenticated user is denied and gets a system message."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, stub = self._make_dispatcher(module_context)
        dispatcher.register(
            "ping", handler, requires_authenticated=True, module_name="mod_a"
        )
        user = make_user(display_name="Alice", is_authenticated=False)
        with caplog.at_level(logging.INFO, logger="owlbot.commands"):
            await dispatcher.dispatch(
                make_chat_event(client_id=42, raw_body="!ping", user=user)
            )

        assert called is False
        assert stub.calls[0] == (
            "send_system_message_to_client",
            {
                "client_id": 42,
                "body": "You must be authenticated to use !ping.",
                "unsanitized": False,
            },
        )
        assert "Command 'ping' denied for Alice: authentication required" in caplog.text

    async def test_requires_moderator_allowed(
        self, module_context: ModuleContext
    ) -> None:
        """Moderator user can invoke a requires_moderator command."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register(
            "ban", handler, requires_moderator=True, module_name="mod_a"
        )
        user = make_user(scopes=frozenset({"MODERATOR"}))
        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!ban", user=user)
        )

        assert called is True

    async def test_requires_moderator_denied(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-moderator user is denied and gets a system message."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, stub = self._make_dispatcher(module_context)
        dispatcher.register(
            "ping", handler, requires_moderator=True, module_name="mod_a"
        )
        user = make_user(display_name="Alice", scopes=frozenset())
        with caplog.at_level(logging.INFO, logger="owlbot.commands"):
            await dispatcher.dispatch(
                make_chat_event(client_id=42, raw_body="!ping", user=user)
            )

        assert called is False
        assert stub.calls[0] == (
            "send_system_message_to_client",
            {
                "client_id": 42,
                "body": "Only moderators can use !ping.",
                "unsanitized": False,
            },
        )
        assert "Command 'ping' denied for Alice: moderator required" in caplog.text

    async def test_cooldown_allows_first_call(
        self, module_context: ModuleContext
    ) -> None:
        """First invocation of a command with cooldown is allowed."""
        called = False

        async def handler(ctx: CommandContext) -> None:
            nonlocal called
            called = True

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, cooldown=60, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!ping"))

        assert called is True

    async def test_cooldown_blocks_second_call(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Second invocation within cooldown is blocked with a message."""
        call_count = 0

        async def handler(ctx: CommandContext) -> None:
            nonlocal call_count
            call_count += 1

        dispatcher, _, stub = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, cooldown=60, module_name="mod_a")

        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!ping"))
        with caplog.at_level(logging.INFO, logger="owlbot.commands"):
            await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!ping"))

        assert call_count == 1
        assert len(stub.calls) == 1
        assert stub.calls[0].name == "send_system_message_to_client"
        assert "!ping can be used every 60s" in stub.calls[0].kwargs["body"]
        assert "on cooldown" in caplog.text

    async def test_cooldown_zero_no_throttle(
        self, module_context: ModuleContext
    ) -> None:
        """Commands with cooldown=0 are never throttled."""
        call_count = 0

        async def handler(ctx: CommandContext) -> None:
            nonlocal call_count
            call_count += 1

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("ping", handler, cooldown=0, module_name="mod_a")

        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!ping"))
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!ping"))

        assert call_count == 2

    async def test_success_commits(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """A successful handler's database changes are committed."""

        async def handler(ctx: CommandContext) -> None:
            await ctx.storage.execute(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT)"
            )
            await ctx.storage.execute(
                "INSERT INTO items (id, name) VALUES (?, ?)", (1, "apple")
            )

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("add", handler, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!add"))

        row = await storage.fetch_one("SELECT * FROM items WHERE id = ?", (1,))
        assert row is not None
        assert row["name"] == "apple"

    async def test_exception_does_not_roll_back_committed_writes(
        self,
        module_context: ModuleContext,
        storage: ModuleStorage,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A handler that raises does not undo already-committed writes."""
        await storage.execute(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT)"
        )

        async def handler(ctx: CommandContext) -> None:
            await ctx.storage.execute(
                "INSERT INTO items (id, name) VALUES (?, ?)", (1, "apple")
            )
            raise RuntimeError("boom")

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("add", handler, module_name="mod_a")
        with caplog.at_level(logging.ERROR, logger="owlbot.commands"):
            await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!add"))

        row = await storage.fetch_one("SELECT * FROM items WHERE id = ?", (1,))
        assert row is not None
        assert row["name"] == "apple"
        assert "raised exception." in caplog.text

    async def test_timeout_is_logged(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A timed-out handler is cancelled and logged."""

        async def handler(ctx: CommandContext) -> None:
            await asyncio.sleep(10)

        dispatcher, _, _ = self._make_dispatcher(module_context, handler_timeout=0.05)
        dispatcher.register("slow", handler, module_name="mod_a")
        with caplog.at_level(logging.WARNING, logger="owlbot.commands"):
            await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!slow"))

        assert any(
            "cancelled after" in r.message and "timeout" in r.message
            for r in caplog.records
        )

    async def test_timeout_does_not_roll_back_committed_writes(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """A timed-out handler's already-committed writes persist."""
        await storage.execute(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT)"
        )

        async def handler(ctx: CommandContext) -> None:
            await ctx.storage.execute(
                "INSERT INTO items (id, name) VALUES (?, ?)", (1, "apple")
            )
            await asyncio.sleep(10)

        dispatcher, _, _ = self._make_dispatcher(module_context, handler_timeout=0.05)
        dispatcher.register("slow", handler, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!slow"))

        row = await storage.fetch_one("SELECT * FROM items WHERE id = ?", (1,))
        assert row is not None
        assert row["name"] == "apple"

    async def test_timeout_rolls_back_explicit_transaction(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """A timed-out handler's uncommitted transaction is rolled back."""
        await storage.execute(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT)"
        )

        async def handler(ctx: CommandContext) -> None:
            async with ctx.storage.transaction():
                await ctx.storage.execute(
                    "INSERT INTO items (id, name) VALUES (?, ?)", (1, "apple")
                )
                await asyncio.sleep(10)

        dispatcher, _, _ = self._make_dispatcher(module_context, handler_timeout=0.05)
        dispatcher.register("slow", handler, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!slow"))

        row = await storage.fetch_one("SELECT * FROM items WHERE id = ?", (1,))
        assert row is None

    async def test_builtin_command_dispatched(
        self, module_context: ModuleContext
    ) -> None:
        """A built-in command receives (event, owncast_client)."""
        captured: list[tuple[Any, Any]] = []

        async def handler(event: ChatEvent, owncast_client: Any) -> None:
            captured.append((event, owncast_client))

        dispatcher, _, stub = self._make_dispatcher(module_context)
        dispatcher.register_builtin("test", handler)
        event = make_chat_event(client_id=42, raw_body="!test")
        await dispatcher.dispatch(event)

        assert len(captured) == 1
        assert captured[0][0] is event
        assert captured[0][1] is stub

    async def test_builtin_command_exception_caught(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A built-in handler that raises does not propagate the exception."""

        async def handler(event: ChatEvent, owncast_client: Any) -> None:
            raise RuntimeError("builtin-fail")

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register_builtin("test", handler)
        with caplog.at_level(logging.ERROR, logger="owlbot.commands"):
            await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!test"))

        assert "raised exception." in caplog.text

    async def test_builtin_command_timeout(
        self, module_context: ModuleContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A built-in handler that times out is cancelled and logged."""

        async def handler(event: ChatEvent, owncast_client: Any) -> None:
            await asyncio.sleep(10)

        dispatcher, _, _ = self._make_dispatcher(module_context, handler_timeout=0.05)
        dispatcher.register_builtin("test", handler)
        with caplog.at_level(logging.WARNING, logger="owlbot.commands"):
            await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!test"))

        assert any(
            "cancelled after" in r.message and "timeout" in r.message
            for r in caplog.records
        )

    async def test_builtin_connect_sends_private_connect_link(
        self, module_context: ModuleContext
    ) -> None:
        """!connect hides the chat line and whispers a one-time link."""
        stub = RecordingOwncastClient()
        session_manager = SessionManager()
        dispatcher = CommandDispatcher(
            get_module_context=lambda _name: module_context,
            owncast_client=stub,
            handler_timeout=5.0,
            loaded_modules=set(),
            session_manager=session_manager,
            public_base_url=module_context.config.public_base_url,
        )
        user = make_user(id="user-123", display_name="Alice")

        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!connect", user=user)
        )

        assert len(stub.calls) == 2
        assert stub.calls[0].name == "set_message_visibility"
        assert stub.calls[0].kwargs == {
            "message_ids": ["test-msg-id"],
            "visible": False,
        }
        assert stub.calls[1].name == "send_system_message_to_client"
        assert stub.calls[1].kwargs["client_id"] == 42
        assert stub.calls[1].kwargs["unsanitized"] is True
        body = stub.calls[1].kwargs["body"]
        url = body.split('href="', 1)[1].split('"', 1)[0]
        token = url.rsplit("/", 1)[-1]
        connect_token = session_manager.get_connect_token(token)
        assert body == (
            f'<a href="{url}">'
            "<u>Click here to connect your Owncast session with Owlbot.</u>"
            "</a>"
        )
        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/connect"
        assert connect_token.user.id == user.id

    async def test_builtin_connect_can_be_reissued_immediately(
        self, module_context: ModuleContext
    ) -> None:
        """!connect is not throttled by the generic built-in cooldown."""
        stub = RecordingOwncastClient()
        session_manager = SessionManager()
        dispatcher = CommandDispatcher(
            get_module_context=lambda _name: module_context,
            owncast_client=stub,
            handler_timeout=5.0,
            loaded_modules=set(),
            session_manager=session_manager,
            public_base_url=module_context.config.public_base_url,
        )
        user = make_user(id="user-123", display_name="Alice")

        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!connect", user=user)
        )
        await dispatcher.dispatch(
            make_chat_event(client_id=42, raw_body="!connect", user=user)
        )

        assert len(stub.calls) == 4
        assert [call.name for call in stub.calls] == [
            "set_message_visibility",
            "send_system_message_to_client",
            "set_message_visibility",
            "send_system_message_to_client",
        ]
        assert all(
            call.kwargs == {"message_ids": ["test-msg-id"], "visible": False}
            for call in stub.calls[::2]
        )
        tokens: list[str] = []
        for call in stub.calls[1::2]:
            body = call.kwargs["body"]
            url = body.split('href="', 1)[1].split('"', 1)[0]
            tokens.append(url.rsplit("/", 1)[-1])
            assert body == (
                f'<a href="{url}">'
                "<u>Click here to connect your Owncast session with Owlbot.</u>"
                "</a>"
            )
        assert tokens[0] != tokens[1]
        assert session_manager.get_connect_token(tokens[0]) is None
        assert session_manager.get_connect_token(tokens[1]) is not None

    async def test_command_context_session_url_for_uses_command_user(
        self, module_context: ModuleContext
    ) -> None:
        """Dispatched command contexts can issue user-bound session URLs."""
        session_manager = SessionManager()
        captured_url = ""
        user = make_user(id="user-123", is_authenticated=True)

        async def handler(ctx: CommandContext) -> None:
            nonlocal captured_url
            captured_url = ctx.session_url_for("/panel")

        dispatcher, _, _ = self._make_dispatcher(
            module_context,
            session_manager=session_manager,
            public_base_url="https://example.com",
        )
        dispatcher.register(
            "panel",
            handler,
            module_name=module_context.module_name,
        )

        await dispatcher.dispatch(make_chat_event(raw_body="!panel", user=user))

        token = captured_url.rsplit("/", 1)[-1]
        connect_token = session_manager.get_connect_token(token)
        assert captured_url.startswith("https://example.com/owlbot/connect/")
        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/test_module/panel"
        assert connect_token.user.id == user.id

    def test_command_session_url_for_accepts_module_relative_path(
        self, module_context: ModuleContext
    ) -> None:
        """Command session URLs add the module namespace to relative paths."""
        session_manager = SessionManager()
        dispatcher, _, _ = self._make_dispatcher(
            module_context,
            session_manager=session_manager,
            public_base_url="https://example.com",
        )
        session_url_for = dispatcher._make_session_url_for(
            module_context.module_name,
            make_user(id="alice"),
        )

        url = session_url_for("panel")
        token = url.rsplit("/", 1)[-1]
        connect_token = session_manager.get_connect_token(token)

        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/test_module/panel"

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("../panel", id="relative-parent"),
            pytest.param("/../panel", id="absolute-parent"),
            pytest.param("/../../admin", id="multi-parent"),
            pytest.param("nested/../../other", id="nested-parent"),
            pytest.param("/%2e%2e/panel", id="encoded-parent"),
        ],
    )
    def test_command_session_url_for_rejects_paths_outside_module_namespace(
        self, module_context: ModuleContext, path: str
    ) -> None:
        """Module-provided session paths cannot escape their namespace."""
        session_manager = SessionManager()
        dispatcher, _, _ = self._make_dispatcher(
            module_context,
            session_manager=session_manager,
            public_base_url="https://example.com",
        )
        session_url_for = dispatcher._make_session_url_for(
            module_context.module_name,
            make_user(id="alice"),
        )

        with pytest.raises(ValueError, match="module namespace"):
            session_url_for(path)

    async def test_unregistered_builtin_does_not_shadow_later_module_command(
        self, module_context: ModuleContext
    ) -> None:
        """Removing a built-in clears its fast-path handler entry too."""
        builtin_calls: list[str] = []
        module_calls: list[str] = []

        async def builtin_handler(event: ChatEvent, owncast_client: Any) -> None:
            builtin_calls.append("builtin")

        async def module_handler(ctx: CommandContext) -> None:
            module_calls.append(ctx.command)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register_builtin("test", builtin_handler)

        assert dispatcher.unregister("test") is True

        dispatcher.register("test", module_handler, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!test"))

        assert builtin_calls == []
        assert module_calls == ["test"]

    async def test_failed_builtin_registration_does_not_poison_later_dispatch(
        self, module_context: ModuleContext
    ) -> None:
        """A built-in conflict leaves no stale fast-path handler behind."""
        builtin_calls: list[str] = []
        module_calls: list[str] = []

        async def module_handler(ctx: CommandContext) -> None:
            module_calls.append(ctx.command)

        async def builtin_handler(event: ChatEvent, owncast_client: Any) -> None:
            builtin_calls.append("builtin")

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("test", module_handler, module_name="mod_a")

        with pytest.raises(ValueError, match="conflicts with existing command 'test'"):
            dispatcher.register_builtin("test", builtin_handler)

        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!test"))

        assert builtin_calls == []
        assert module_calls == ["test"]

    async def test_bulk_unregister_builtins_clears_fast_path_handler_map(
        self, module_context: ModuleContext
    ) -> None:
        """Bulk-unregistering built-ins removes their fast-path handlers too."""
        builtin_calls: list[str] = []
        module_calls: list[str] = []

        async def builtin_handler(event: ChatEvent, owncast_client: Any) -> None:
            builtin_calls.append("builtin")

        async def module_handler(ctx: CommandContext) -> None:
            module_calls.append(ctx.command)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register_builtin("test", builtin_handler)

        assert dispatcher.unregister_by_module("__builtin__") >= 1

        dispatcher.register("test", module_handler, module_name="mod_a")
        await dispatcher.dispatch(make_chat_event(client_id=42, raw_body="!test"))

        assert builtin_calls == []
        assert module_calls == ["test"]

    async def test_command_event_fields(self, module_context: ModuleContext) -> None:
        """Dispatched CommandContext carries correct CommandEvent fields."""
        captured: list[CommandContext] = []

        async def handler(ctx: CommandContext) -> None:
            captured.append(ctx)

        dispatcher, _, _ = self._make_dispatcher(module_context)
        dispatcher.register("greet", handler, module_name="mod_a")
        event = make_chat_event(client_id=42, raw_body="!greet world")
        await dispatcher.dispatch(event)

        assert captured[0].command_event.command == "greet"
        assert captured[0].command_event.args == "world"
        assert captured[0].command_event.args_list == ["world"]
        assert captured[0].command_event.prefix == "!"
        assert captured[0].command_event.chat_event is event


class TestModuleCommands:
    """Tests ModuleCommands from owlbot.registries.commands."""

    def _make_dispatcher_and_mod_cmds(
        self,
        module_context: ModuleContext,
        *module_names: str,
    ) -> tuple[CommandDispatcher, dict[str, ModuleCommands]]:
        """Build a dispatcher and ModuleCommands wrappers for given modules.

        These tests exercise registration and ownership only; commands are
        never dispatched, so a single ``module_context`` is reused for every
        lookup.
        """
        stub = RecordingOwncastClient()
        dispatcher = CommandDispatcher(
            get_module_context=lambda _name: module_context,
            owncast_client=stub,
            handler_timeout=5.0,
            loaded_modules=set(),
            session_manager=SessionManager(),
            public_base_url=module_context.config.public_base_url,
        )
        mod_cmds = {name: ModuleCommands(dispatcher, name) for name in module_names}
        return dispatcher, mod_cmds

    def test_register_auto_supplies_module_name(
        self, module_context: ModuleContext
    ) -> None:
        """ModuleCommands.register auto-supplies module_name."""

        async def handler(ctx: CommandContext) -> None:
            pass

        dispatcher, mod_cmds = self._make_dispatcher_and_mod_cmds(
            module_context, "mod_a"
        )
        mod_cmds["mod_a"].register("ping", handler)
        info = dispatcher.get("ping")
        assert info is not None
        assert info.module_name == "mod_a"

    def test_prefix_property(self, module_context: ModuleContext) -> None:
        """ModuleCommands.prefix delegates to the dispatcher's prefix."""
        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, "mod_a")
        assert mod_cmds["mod_a"].prefix == "!"

    def test_module_commands_property(self, module_context: ModuleContext) -> None:
        """module_commands returns dict with both registered commands."""

        async def handler(ctx: CommandContext) -> None:
            pass

        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, "mod_a")
        mod_cmds["mod_a"].register("ping", handler)
        mod_cmds["mod_a"].register("pong", handler)
        result = mod_cmds["mod_a"].module_commands
        assert "ping" in result
        assert "pong" in result
        assert len(result) == 2

    def test_module_commands_property_empty(
        self, module_context: ModuleContext
    ) -> None:
        """module_commands returns empty dict when nothing registered."""
        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, "mod_a")
        assert mod_cmds["mod_a"].module_commands == {}

    @pytest.mark.parametrize(
        ("owner", "other_setup", "call_module", "trigger", "expected"),
        [
            pytest.param("mod_a", None, "mod_a", "ping", True, id="own-command"),
            pytest.param(
                "mod_a", "mod_b", "mod_a", "quote", False, id="other-modules-command"
            ),
            pytest.param("mod_a", None, "mod_a", "nope", False, id="unknown-command"),
        ],
    )
    def test_unregister(
        self,
        module_context: ModuleContext,
        owner: str,
        other_setup: str | None,
        call_module: str,
        trigger: str,
        *,
        expected: bool,
    ) -> None:
        """ModuleCommands.unregister respects ownership."""

        async def handler(ctx: CommandContext) -> None:
            pass

        modules = [owner]
        if other_setup:
            modules.append(other_setup)
        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, *modules)

        mod_cmds[owner].register("ping", handler)
        if other_setup:
            mod_cmds[other_setup].register("quote", handler)

        assert mod_cmds[call_module].unregister(trigger) is expected

    def test_get_own_command(self, module_context: ModuleContext) -> None:
        """get() returns CommandInfo for own commands."""

        async def handler(ctx: CommandContext) -> None:
            pass

        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, "mod_a")
        mod_cmds["mod_a"].register("ping", handler)
        info = mod_cmds["mod_a"].get("ping")
        assert info is not None
        assert info.name == "ping"

    def test_get_other_modules_command(self, module_context: ModuleContext) -> None:
        """get() returns None for another module's command."""

        async def handler(ctx: CommandContext) -> None:
            pass

        _, mod_cmds = self._make_dispatcher_and_mod_cmds(
            module_context, "mod_a", "mod_b"
        )
        mod_cmds["mod_b"].register("quote", handler)
        assert mod_cmds["mod_a"].get("quote") is None

    def test_get_unknown(self, module_context: ModuleContext) -> None:
        """get() returns None for unknown commands."""
        _, mod_cmds = self._make_dispatcher_and_mod_cmds(module_context, "mod_a")
        assert mod_cmds["mod_a"].get("nope") is None

    def test_exists_sees_all_modules(self, module_context: ModuleContext) -> None:
        """exists() checks across all modules, not scoped to own."""

        async def handler(ctx: CommandContext) -> None:
            pass

        _, mod_cmds = self._make_dispatcher_and_mod_cmds(
            module_context, "mod_a", "mod_b"
        )
        mod_cmds["mod_b"].register("quote", handler)
        assert mod_cmds["mod_a"].exists("quote") is True


class TestCommandContext:
    """Tests CommandContext from owlbot.api.context."""

    def _make_command_context(
        self,
        module_ctx: ModuleContext,
        *,
        session_url_for: Callable[[str], str] | None = None,
    ) -> tuple[CommandContext, CommandEvent, EventContext[ChatEvent]]:
        """Build a CommandContext wrapping the given ModuleContext."""
        event = make_chat_event(client_id=42, raw_body="!greet world")
        cmd_event = CommandEvent(
            command="greet",
            args="world",
            args_list=["world"],
            prefix="!",
            chat_event=event,
        )
        event_ctx: EventContext[ChatEvent] = EventContext(
            event=event, module=module_ctx
        )
        cmd_ctx = CommandContext(
            command_event=cmd_event,
            event_context=event_ctx,
            module=module_ctx,
            _session_url_for=(
                session_url_for
                if session_url_for is not None
                else lambda path: f"https://example.com{path}"
            ),
        )
        return cmd_ctx, cmd_event, event_ctx

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
        ],
    )
    def test_property_proxying(
        self, module_context: ModuleContext, prop: str, *, use_is: bool
    ) -> None:
        """CommandContext properties proxy to the underlying ModuleContext."""
        cmd_ctx, _, _ = self._make_command_context(module_context)
        ctx_val = getattr(cmd_ctx, prop)
        mod_val = getattr(module_context, prop)
        if use_is:
            assert ctx_val is mod_val
        else:
            assert ctx_val == mod_val

    def test_command_property(self, module_context: ModuleContext) -> None:
        """ctx.command matches the CommandEvent's command."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.command == cmd_event.command

    def test_args_property(self, module_context: ModuleContext) -> None:
        """ctx.args matches the CommandEvent's args."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.args == cmd_event.args

    def test_args_list_property(self, module_context: ModuleContext) -> None:
        """ctx.args_list matches the CommandEvent's args_list."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.args_list == cmd_event.args_list

    def test_prefix_property(self, module_context: ModuleContext) -> None:
        """ctx.prefix matches the CommandEvent's prefix."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.prefix == cmd_event.prefix

    def test_chat_event_property(self, module_context: ModuleContext) -> None:
        """ctx.chat_event is the CommandEvent's chat_event."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.chat_event is cmd_event.chat_event

    def test_user_property(self, module_context: ModuleContext) -> None:
        """ctx.user is the chat_event's user."""
        cmd_ctx, cmd_event, _ = self._make_command_context(module_context)
        assert cmd_ctx.user is cmd_event.chat_event.user

    def test_event_context_attribute(self, module_context: ModuleContext) -> None:
        """ctx.event_context is the EventContext passed at construction."""
        cmd_ctx, _, event_ctx = self._make_command_context(module_context)
        assert cmd_ctx.event_context is event_ctx

    def test_session_url_for(self, module_context: ModuleContext) -> None:
        """ctx.session_url_for() delegates to the command-scoped URL builder."""
        cmd_ctx, _, _ = self._make_command_context(
            module_context,
            session_url_for=lambda path: f"https://example.com{path}",
        )

        assert cmd_ctx.session_url_for("/panel") == "https://example.com/panel"
