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

"""Unit tests for event registration, dispatching, and context infrastructure.

Tests cover the @on_event decorator, Priority enum, EventRegistry,
EventDispatcher (priority sorting, propagation control, timeout/rollback,
command dispatch phase), ModuleEvents (ownership checks), and
EventContext/PropagationState.
"""

import asyncio
import logging
import types
from typing import TYPE_CHECKING, Any

import pytest

from owlbot.api.context import EventContext, ModuleContext, PropagationState
from owlbot.api.event_types import (
    ChatEvent,
    EventType,
    User,
    UserJoinedEvent,
)
from owlbot.api.events import EventMark, Priority, on_event
from owlbot.registries.events import (
    EventDispatcher,
    EventRegistry,
    HandlerEntry,
    ModuleEvents,
)

if TYPE_CHECKING:
    from owlbot.api.storage import ModuleStorage


def _make_user(
    *,
    user_id: str = "user-1",
    display_name: str = "TestUser",
) -> User:
    """Build a minimal User for test events."""
    return User(
        id=user_id,
        display_name=display_name,
        display_color=0,
        created_at=None,
        previous_names=[],
        name_changed_at=None,
        is_bot=False,
        is_authenticated=False,
        scopes=[],
    )


def _make_chat_event(
    *,
    body: str = "hello",
    user: User | None = None,
) -> ChatEvent:
    """Build a minimal ChatEvent for test dispatches."""
    return ChatEvent(
        user=user or _make_user(),
        client_id=1,
        body=body,
        raw_body=f"<p>{body}</p>",
        message_id="msg-001",
        is_visible=True,
        timestamp=None,
    )


def _make_module_context(
    storage: ModuleStorage | None, module_name: str = "mod_a"
) -> ModuleContext:
    """Build a stub ModuleContext backed by real or None storage."""
    return ModuleContext(
        module_name=module_name,
        config=None,  # type: ignore[arg-type]
        owncast_client=None,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        commands=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        routes=None,  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        admin_client=None,
    )


def _unused_get_module_context(name: str) -> ModuleContext:
    """Stub for EventDispatcher tests that never invoke get_module_context."""
    raise AssertionError("get_module_context should not be called")


class TestOnEventDecorator:
    """Tests the @on_event decorator from owlbot.api.events."""

    @pytest.mark.parametrize(
        "event_types,priority,expected_types,expected_priority",
        [
            pytest.param(
                (EventType.CHAT,),
                Priority.NORMAL,
                (EventType.CHAT,),
                50,
                id="single-type",
            ),
            pytest.param(
                (EventType.CHAT, EventType.USER_JOINED),
                Priority.NORMAL,
                (EventType.CHAT, EventType.USER_JOINED),
                50,
                id="multiple-types",
            ),
            pytest.param(
                (EventType.CHAT,),
                Priority.HIGH,
                (EventType.CHAT,),
                75,
                id="custom-enum-priority",
            ),
            pytest.param(
                (EventType.CHAT,),
                42,
                (EventType.CHAT,),
                42,
                id="custom-int-priority",
            ),
        ],
    )
    def test_sets_event_mark(
        self,
        event_types: tuple[EventType, ...],
        priority: int,
        expected_types: tuple[EventType, ...],
        expected_priority: int,
    ) -> None:
        """Decorator attaches an _owlbot_event mark with correct types and priority."""

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        decorated = on_event(*event_types, priority=priority)(handler)
        mark: EventMark = decorated._owlbot_event  # type: ignore[attr-defined]
        assert mark["event_types"] == expected_types
        assert mark["priority"] == expected_priority

    def test_returns_function_unchanged(self) -> None:
        """Decorated function is the original function object (identity check)."""

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        decorated = on_event(EventType.CHAT)(handler)
        assert decorated is handler


class TestPriority:
    """Tests the Priority IntEnum from owlbot.api.events."""

    @pytest.mark.parametrize(
        "member,value",
        [
            pytest.param(Priority.HIGHEST, 100, id="highest"),
            pytest.param(Priority.HIGH, 75, id="high"),
            pytest.param(Priority.NORMAL, 50, id="normal"),
            pytest.param(Priority.LOW, 25, id="low"),
            pytest.param(Priority.LOWEST, 0, id="lowest"),
        ],
    )
    def test_member_value(self, member: Priority, value: int) -> None:
        """Each Priority member has the expected integer value."""
        assert member == value

    def test_ordering(self) -> None:
        """Priority members are ordered HIGHEST > HIGH > NORMAL > LOW > LOWEST."""
        assert Priority.HIGHEST > Priority.HIGH > Priority.NORMAL
        assert Priority.NORMAL > Priority.LOW > Priority.LOWEST

    def test_is_int(self) -> None:
        """Priority members are instances of int."""
        assert isinstance(Priority.NORMAL, int) is True


class TestEventRegistry:
    """Tests EventRegistry from owlbot.registries.events."""

    def test_register_single_type(self) -> None:
        """A handler registered for one type appears in get() with correct fields."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT,), "mod_a", 50)
        entries = registry.get(EventType.CHAT)
        assert len(entries) == 1
        assert entries[0].handler is handler
        assert entries[0].module_name == "mod_a"
        assert entries[0].priority == 50

    def test_register_multiple_types(self) -> None:
        """A handler registered for two types appears in get() for both."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT, EventType.USER_JOINED), "mod_a", 50)
        chat_entries = registry.get(EventType.CHAT)
        joined_entries = registry.get(EventType.USER_JOINED)
        assert len(chat_entries) == 1
        assert chat_entries[0].handler is handler
        assert len(joined_entries) == 1
        assert joined_entries[0].handler is handler

    def test_register_multiple_handlers_same_type(self) -> None:
        """Two handlers for the same type both appear in get()."""
        registry = EventRegistry()

        async def handler_a(ctx: EventContext[Any]) -> None:
            pass

        async def handler_b(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler_a, (EventType.CHAT,), "mod_a", 50)
        registry.register(handler_b, (EventType.CHAT,), "mod_b", 75)
        entries = registry.get(EventType.CHAT)
        assert len(entries) == 2
        assert entries[0].handler is handler_a
        assert entries[1].handler is handler_b

    def test_get_empty(self) -> None:
        """get() on a fresh registry returns an empty list."""
        registry = EventRegistry()
        assert registry.get(EventType.CHAT) == []

    def test_get_all_returns_copy(self) -> None:
        """Mutating the dict returned by get_all() does not affect the registry."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT,), "mod_a", 50)
        all_handlers = registry.get_all()
        all_handlers.clear()
        assert len(registry.get(EventType.CHAT)) == 1

    def test_get_all_handler_lists_are_isolated(self) -> None:
        """Mutating handler lists from get_all() does not affect the registry."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT,), "mod_a", 50)
        all_handlers = registry.get_all()
        all_handlers[EventType.CHAT.value].clear()
        assert len(registry.get(EventType.CHAT)) == 1

    def test_get_handler_module_found(self) -> None:
        """get_handler_module() returns the module name for a registered handler."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT,), "mod_a", 50)
        assert registry.get_handler_module(handler) == "mod_a"

    def test_get_handler_module_not_found(self) -> None:
        """get_handler_module() returns None for an unknown handler."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        assert registry.get_handler_module(handler) is None

    @pytest.mark.parametrize(
        "setup_types,expected_return,expected_remaining",
        [
            pytest.param(
                (EventType.CHAT,),
                True,
                0,
                id="found",
            ),
            pytest.param(
                None,
                False,
                0,
                id="missing",
            ),
            pytest.param(
                (EventType.CHAT, EventType.USER_JOINED),
                True,
                0,
                id="multi-type",
            ),
        ],
    )
    def test_unregister(
        self,
        setup_types: tuple[EventType, ...] | None,
        expected_return: bool,
        expected_remaining: int,
    ) -> None:
        """unregister() removes handler from all registered types."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        async def unknown(ctx: EventContext[Any]) -> None:
            pass

        if setup_types is not None:
            registry.register(handler, setup_types, "mod_a", 50)
            result = registry.unregister(handler)
        else:
            result = registry.unregister(unknown)

        assert result is expected_return
        if setup_types is not None:
            for et in setup_types:
                assert len(registry.get(et)) == expected_remaining

    def test_unregister_leaves_other_handlers(self) -> None:
        """unregister() removes only the target; other handlers remain."""
        registry = EventRegistry()

        async def target(ctx: EventContext[Any]) -> None:
            pass

        async def bystander(ctx: EventContext[Any]) -> None:
            pass

        registry.register(target, (EventType.CHAT,), "mod_a", 50)
        registry.register(bystander, (EventType.CHAT,), "mod_b", 50)
        result = registry.unregister(target)
        assert result is True
        entries = registry.get(EventType.CHAT)
        assert len(entries) == 1
        assert entries[0].handler is bystander
        assert entries[0].module_name == "mod_b"

    def test_unregister_by_module(self) -> None:
        """unregister_by_module() removes mod_a's handlers and leaves mod_b's."""
        registry = EventRegistry()

        async def handler_a(ctx: EventContext[Any]) -> None:
            pass

        async def handler_b(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler_a, (EventType.CHAT,), "mod_a", 50)
        registry.register(handler_b, (EventType.CHAT,), "mod_b", 50)
        count = registry.unregister_by_module("mod_a")
        assert count == 1
        entries = registry.get(EventType.CHAT)
        assert len(entries) == 1
        assert entries[0].handler is handler_b
        assert entries[0].module_name == "mod_b"

    def test_unregister_by_module_counts_unique(self) -> None:
        """Handler registered for 2 types is counted once (set dedup)."""
        registry = EventRegistry()

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        registry.register(handler, (EventType.CHAT, EventType.USER_JOINED), "mod_a", 50)
        count = registry.unregister_by_module("mod_a")
        assert count == 1

    def test_unregister_by_module_nonexistent(self) -> None:
        """unregister_by_module() returns 0 for a module with no handlers."""
        registry = EventRegistry()
        assert registry.unregister_by_module("no_such_module") == 0

    def test_register_from_module(self) -> None:
        """register_from_module() scans for decorated functions and registers them."""
        registry = EventRegistry()

        async def marked_handler(ctx: EventContext[Any]) -> None:
            pass

        marked_handler._owlbot_event = EventMark(  # type: ignore[attr-defined]
            event_types=(EventType.CHAT,), priority=75
        )

        async def plain_function(ctx: EventContext[Any]) -> None:
            pass

        mod = types.ModuleType("fake_module")
        mod.marked_handler = marked_handler  # type: ignore[attr-defined]
        mod.plain_function = plain_function  # type: ignore[attr-defined]
        mod.not_callable = "just a string"  # type: ignore[attr-defined]

        registry.register_from_module(mod, "mod_a")
        entries = registry.get(EventType.CHAT)
        assert len(entries) == 1
        assert entries[0].handler is marked_handler
        assert entries[0].module_name == "mod_a"
        assert entries[0].priority == 75

    def test_handler_entry_fields(self) -> None:
        """HandlerEntry NamedTuple fields are accessible by name."""

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        entry = HandlerEntry(handler=handler, module_name="mod_a", priority=50)
        assert entry.handler is handler
        assert entry.module_name == "mod_a"
        assert entry.priority == 50


class TestEventDispatcherDispatch:
    """Tests dispatch() from EventDispatcher."""

    async def test_calls_handler(self, storage: ModuleStorage) -> None:
        """Handler receives an EventContext where ctx.event is the dispatched event."""
        received: list[Any] = []

        async def handler(ctx: EventContext[Any]) -> None:
            received.append(ctx.event)

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(handler, (EventType.CHAT,), "mod_a")
        event = _make_chat_event()
        await dispatcher.dispatch(EventType.CHAT, event)
        assert len(received) == 1
        assert received[0] is event

    async def test_priority_order(self, storage: ModuleStorage) -> None:
        """Handlers execute in priority order: HIGH before NORMAL before LOW."""
        call_order: list[str] = []

        async def high_handler(ctx: EventContext[Any]) -> None:
            call_order.append("high")

        async def normal_handler(ctx: EventContext[Any]) -> None:
            call_order.append("normal")

        async def low_handler(ctx: EventContext[Any]) -> None:
            call_order.append("low")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(high_handler, (EventType.CHAT,), "mod_a", Priority.HIGH)
        dispatcher.register(normal_handler, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        dispatcher.register(low_handler, (EventType.CHAT,), "mod_a", Priority.LOW)
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["high", "normal", "low"]

    async def test_stable_order_same_priority(self, storage: ModuleStorage) -> None:
        """Handlers at the same priority run in registration order."""
        call_order: list[str] = []

        async def first(ctx: EventContext[Any]) -> None:
            call_order.append("first")

        async def second(ctx: EventContext[Any]) -> None:
            call_order.append("second")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(first, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        dispatcher.register(second, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["first", "second"]

    async def test_no_handlers(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dispatch with no handlers does not raise; debug log emitted."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        module_ctx = _make_module_context(storage, "mod_a")
        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        with caplog.at_level(logging.DEBUG, logger="owlbot.events"):
            await dispatcher.dispatch(
                EventType.USER_JOINED,
                UserJoinedEvent(
                    user=_make_user(),
                    client_id=1,
                    event_id="evt-001",
                    timestamp=None,
                ),
            )
        assert any(
            "No handlers registered for event type" in r.message for r in caplog.records
        )

    async def test_logs_event(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dispatch logs the event via log_event() at INFO level."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        module_ctx = _make_module_context(storage, "mod_a")
        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        chat_event = _make_chat_event(body="hello")
        with caplog.at_level(logging.INFO, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, chat_event)
        assert any(
            r.message == "[CHAT] TestUser (msg-001): hello" for r in caplog.records
        )

    async def test_propagation_stops_remaining(self, storage: ModuleStorage) -> None:
        """Handler A stops propagation; handler B is never called."""
        call_order: list[str] = []

        async def handler_a(ctx: EventContext[Any]) -> None:
            call_order.append("a")
            ctx.stop_propagation("blocked")

        async def handler_b(ctx: EventContext[Any]) -> None:
            call_order.append("b")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(handler_a, (EventType.CHAT,), "mod_a", Priority.HIGH)
        dispatcher.register(handler_b, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["a"]

    async def test_propagation_reason_logged(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Propagation stop reason appears in debug log."""

        async def handler_a(ctx: EventContext[Any]) -> None:
            ctx.stop_propagation("blocked")

        async def handler_b(ctx: EventContext[Any]) -> None:
            pass

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(handler_a, (EventType.CHAT,), "mod_a", Priority.HIGH)
        dispatcher.register(handler_b, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        with caplog.at_level(logging.DEBUG, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert any("Propagation stopped: blocked" in r.message for r in caplog.records)

    async def test_independent_dispatches(self, storage: ModuleStorage) -> None:
        """Stop propagation in first dispatch; second dispatch runs all."""
        call_order: list[str] = []
        stop_once = True

        async def stopper(ctx: EventContext[Any]) -> None:
            nonlocal stop_once
            call_order.append("stopper")
            if stop_once:
                ctx.stop_propagation("done")
                stop_once = False

        async def follower(ctx: EventContext[Any]) -> None:
            call_order.append("follower")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(stopper, (EventType.CHAT,), "mod_a", Priority.HIGH)
        dispatcher.register(follower, (EventType.CHAT,), "mod_a", Priority.NORMAL)

        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["stopper"]

        call_order.clear()
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["stopper", "follower"]


class TestEventDispatcherTransactions:
    """Tests _call_handler() commit/rollback behaviour in EventDispatcher."""

    async def test_success_commits(self, storage: ModuleStorage) -> None:
        """A successful handler's database changes are committed."""

        async def handler(ctx: EventContext[Any]) -> None:
            await ctx.storage.execute("CREATE TABLE IF NOT EXISTS test_tbl (val TEXT)")
            await ctx.storage.execute(
                "INSERT INTO test_tbl (val) VALUES (?)", ("committed",)
            )

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(handler, (EventType.CHAT,), "mod_a")
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())

        row = await storage.fetch_one("SELECT val FROM test_tbl")
        assert row is not None
        assert row[0] == "committed"

    async def test_exception_rolls_back(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A handler that raises has its database changes rolled back."""
        # Pre-create the table outside the handler's transaction.
        await storage.execute("CREATE TABLE IF NOT EXISTS test_tbl (val TEXT)")

        async def handler(ctx: EventContext[Any]) -> None:
            await ctx.storage.execute(
                "INSERT INTO test_tbl (val) VALUES (?)", ("rolled_back",)
            )
            raise RuntimeError("boom")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(handler, (EventType.CHAT,), "mod_a")
        with caplog.at_level(logging.ERROR, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, _make_chat_event())

        row = await storage.fetch_one("SELECT val FROM test_tbl")
        assert row is None
        assert any("raised exception: boom" in r.message for r in caplog.records)

    async def test_exception_does_not_stop_next_handler(
        self, storage: ModuleStorage
    ) -> None:
        """A handler that raises does not prevent subsequent handlers from running."""
        call_order: list[str] = []

        async def failing_handler(ctx: EventContext[Any]) -> None:
            call_order.append("failing")
            raise RuntimeError("fail")

        async def second_handler(ctx: EventContext[Any]) -> None:
            call_order.append("second")

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(failing_handler, (EventType.CHAT,), "mod_a", Priority.HIGH)
        dispatcher.register(second_handler, (EventType.CHAT,), "mod_a", Priority.NORMAL)
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert call_order == ["failing", "second"]

    async def test_timeout_rolls_back(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A handler that exceeds the timeout is cancelled and changes rolled back."""

        async def slow_handler(ctx: EventContext[Any]) -> None:
            await asyncio.sleep(10)

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=0.05,
        )
        dispatcher.register(slow_handler, (EventType.CHAT,), "mod_a")
        with caplog.at_level(logging.WARNING, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert any(
            "cancelled after" in r.message and "0.05s timeout" in r.message
            for r in caplog.records
        )

    async def test_timeout_rolls_back_db_changes(self, storage: ModuleStorage) -> None:
        """A timed-out handler's database writes are rolled back."""
        await storage.execute("CREATE TABLE IF NOT EXISTS test_tbl (val TEXT)")

        async def slow_handler(ctx: EventContext[Any]) -> None:
            await ctx.storage.execute("INSERT INTO test_tbl (val) VALUES (?)", ("tmp",))
            await asyncio.sleep(10)

        module_ctx = _make_module_context(storage, "mod_a")

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=0.05,
        )
        dispatcher.register(slow_handler, (EventType.CHAT,), "mod_a")
        await dispatcher.dispatch(EventType.CHAT, _make_chat_event())

        row = await storage.fetch_one("SELECT val FROM test_tbl")
        assert row is None


class TestCommandDispatchPhase:
    """Tests Phase 2 (command dispatch) in EventDispatcher.dispatch()."""

    @pytest.mark.parametrize(
        "event_type,event_factory,expected_calls",
        [
            pytest.param(
                EventType.CHAT,
                lambda: _make_chat_event(),
                1,
                id="chat-triggers",
            ),
            pytest.param(
                EventType.USER_JOINED,
                lambda: UserJoinedEvent(
                    user=_make_user(),
                    client_id=1,
                    event_id="evt-001",
                    timestamp=None,
                ),
                0,
                id="non-chat-skips",
            ),
        ],
    )
    async def test_command_dispatch_trigger(
        self,
        storage: ModuleStorage,
        event_type: EventType,
        event_factory: Any,
        expected_calls: int,
    ) -> None:
        """Command dispatch is called for CHAT events but not for other types."""
        command_calls: list[Any] = []

        async def command_dispatch(event: ChatEvent) -> None:
            command_calls.append(event)

        module_ctx = _make_module_context(storage, "mod_a")
        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        event = event_factory()
        await dispatcher.dispatch(event_type, event)
        assert len(command_calls) == expected_calls
        if expected_calls == 1:
            assert command_calls[0] is event

    async def test_propagation_skips_commands(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stopped propagation skips command dispatch."""
        command_calls: list[Any] = []

        async def command_dispatch(event: ChatEvent) -> None:
            command_calls.append(event)

        async def stopper(ctx: EventContext[Any]) -> None:
            ctx.stop_propagation("spam")

        module_ctx = _make_module_context(storage, "mod_a")
        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        dispatcher.register(stopper, (EventType.CHAT,), "mod_a")
        with caplog.at_level(logging.DEBUG, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert command_calls == []
        assert any(
            "Command dispatch skipped: spam" in r.message for r in caplog.records
        )

    async def test_command_dispatch_exception_caught(
        self, storage: ModuleStorage, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An exception in command_dispatch is caught and logged, not propagated."""

        async def command_dispatch(event: ChatEvent) -> None:
            raise RuntimeError("fail")

        module_ctx = _make_module_context(storage, "mod_a")
        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=lambda name: module_ctx,
            handler_timeout=5.0,
        )
        with caplog.at_level(logging.ERROR, logger="owlbot.events"):
            await dispatcher.dispatch(EventType.CHAT, _make_chat_event())
        assert any("Command dispatch failed: fail" in r.message for r in caplog.records)


class TestModuleEvents:
    """Tests ModuleEvents from owlbot.registries.events."""

    def test_register_auto_supplies_name(self) -> None:
        """After register, the dispatcher knows the handler belongs to the module."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=_unused_get_module_context,
            handler_timeout=5.0,
        )
        mod_events = ModuleEvents(dispatcher, "mod_a")

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        mod_events.register(handler, (EventType.CHAT,))
        assert dispatcher.get_handler_module(handler) == "mod_a"

    def test_module_events_property(self) -> None:
        """module_events returns dict with correct keys and handler entries."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=_unused_get_module_context,
            handler_timeout=5.0,
        )
        mod_events = ModuleEvents(dispatcher, "mod_a")

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        mod_events.register(handler, (EventType.CHAT,))
        result = mod_events.module_events
        assert EventType.CHAT.value in result
        assert len(result[EventType.CHAT.value]) == 1
        assert result[EventType.CHAT.value][0].handler is handler

    def test_module_events_property_empty(self) -> None:
        """module_events returns empty dict when nothing is registered."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=_unused_get_module_context,
            handler_timeout=5.0,
        )
        mod_events = ModuleEvents(dispatcher, "mod_a")
        assert mod_events.module_events == {}

    @pytest.mark.parametrize(
        "register_on,unregister_from,expected",
        [
            pytest.param("mod_a", "mod_a", True, id="own-handler"),
            pytest.param("mod_b", "mod_a", False, id="other-modules-handler"),
            pytest.param(None, "mod_a", False, id="unknown-handler"),
        ],
    )
    def test_unregister(
        self, register_on: str | None, unregister_from: str, expected: bool
    ) -> None:
        """ModuleEvents.unregister() respects ownership checks."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=_unused_get_module_context,
            handler_timeout=5.0,
        )

        async def handler(ctx: EventContext[Any]) -> None:
            pass

        if register_on is not None:
            mod_reg = ModuleEvents(dispatcher, register_on)
            mod_reg.register(handler, (EventType.CHAT,))

        mod_unreg = ModuleEvents(dispatcher, unregister_from)
        assert mod_unreg.unregister(handler) is expected

    def test_get_filters_by_module(self) -> None:
        """ModuleEvents.get() returns only the owning module's handlers."""

        async def command_dispatch(event: ChatEvent) -> None:
            pass

        dispatcher = EventDispatcher(
            command_dispatch=command_dispatch,
            get_module_context=_unused_get_module_context,
            handler_timeout=5.0,
        )
        mod_a = ModuleEvents(dispatcher, "mod_a")
        mod_b = ModuleEvents(dispatcher, "mod_b")

        async def handler_a(ctx: EventContext[Any]) -> None:
            pass

        async def handler_b(ctx: EventContext[Any]) -> None:
            pass

        mod_a.register(handler_a, (EventType.CHAT,))
        mod_b.register(handler_b, (EventType.CHAT,))

        entries = mod_a.get(EventType.CHAT)
        assert len(entries) == 1
        assert entries[0].handler is handler_a
        assert entries[0].module_name == "mod_a"


class TestPropagationState:
    """Tests PropagationState from owlbot.api.context."""

    def test_defaults(self) -> None:
        """Fresh PropagationState has stopped=False and reason=''."""
        state = PropagationState()
        assert state.stopped is False
        assert state.reason == ""

    def test_shared_reference(self) -> None:
        """Two variables referencing the same instance see mutations."""
        state = PropagationState()
        alias = state
        alias.stopped = True
        alias.reason = "test"
        assert state.stopped is True
        assert state.reason == "test"


class TestEventContext:
    """Tests EventContext from owlbot.api.context."""

    @pytest.mark.parametrize(
        "prop,use_is",
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
        self, storage: ModuleStorage, prop: str, use_is: bool
    ) -> None:
        """EventContext properties proxy to the underlying ModuleContext."""
        module_ctx = _make_module_context(storage, "mod_a")
        event = _make_chat_event()
        ctx: EventContext[ChatEvent] = EventContext(event=event, module=module_ctx)
        ctx_val = getattr(ctx, prop)
        mod_val = getattr(module_ctx, prop)
        if use_is:
            assert ctx_val is mod_val
        else:
            assert ctx_val == mod_val

    def test_event_attribute(self) -> None:
        """ctx.event is the event object passed at construction."""
        module_ctx = _make_module_context(None, "mod_a")
        event = _make_chat_event()
        ctx: EventContext[ChatEvent] = EventContext(event=event, module=module_ctx)
        assert ctx.event is event

    def test_propagation_stopped_default(self) -> None:
        """propagation_stopped is False by default."""
        module_ctx = _make_module_context(None, "mod_a")
        ctx: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx
        )
        assert ctx.propagation_stopped is False

    def test_stop_propagation(self) -> None:
        """After stop_propagation(), propagation_stopped is True."""
        module_ctx = _make_module_context(None, "mod_a")
        ctx: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx
        )
        ctx.stop_propagation()
        assert ctx.propagation_stopped is True

    def test_stop_propagation_stores_reason(self) -> None:
        """stop_propagation() stores the reason string."""
        module_ctx = _make_module_context(None, "mod_a")
        ctx: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx
        )
        ctx.stop_propagation("blocked")
        assert ctx._propagation.reason == "blocked"

    def test_first_reason_wins(self) -> None:
        """Only the first non-empty reason is stored."""
        module_ctx = _make_module_context(None, "mod_a")
        ctx: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx
        )
        ctx.stop_propagation("first")
        ctx.stop_propagation("second")
        assert ctx._propagation.reason == "first"

    def test_none_reason_leaves_empty(self) -> None:
        """stop_propagation(None) leaves reason as empty string."""
        module_ctx = _make_module_context(None, "mod_a")
        ctx: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx
        )
        ctx.stop_propagation(None)
        assert ctx._propagation.reason == ""

    def test_shared_propagation_across_contexts(self) -> None:
        """Two contexts sharing a PropagationState see each other's mutations."""
        module_ctx = _make_module_context(None, "mod_a")
        propagation = PropagationState()
        ctx_a: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx, _propagation=propagation
        )
        ctx_b: EventContext[ChatEvent] = EventContext(
            event=_make_chat_event(), module=module_ctx, _propagation=propagation
        )
        ctx_a.stop_propagation("blocked")
        assert ctx_b.propagation_stopped is True
        assert ctx_b._propagation.reason == "blocked"
