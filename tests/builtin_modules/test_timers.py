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

"""Tests for the timers module."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from owlbot.api import OwncastError
from owlbot.api.event_types import EventType
from owlbot.builtin_modules import timers as timers_pkg
from owlbot.builtin_modules.timers import setup, teardown
from owlbot.builtin_modules.timers.manager import Timer, TimerManager, get_manager
from owlbot.builtin_modules.timers.repository import TimerRepository
from owlbot.builtin_modules.timers.types import (
    NAME_PATTERN,
    IntervalType,
    InvalidTimerNameError,
    NegativeLineCountError,
    TimerAlreadyDisabledError,
    TimerAlreadyEnabledError,
    TimerError,
    TimerInfo,
    TimerMessageRequiredError,
    TimerNameTakenError,
    TimerNotFoundError,
    duration_to_seconds,
    parse_interval,
)
from owlbot.testing import (
    make_chat_event,
    make_stream_started_event,
    make_stream_stopped_event,
    make_user,
    make_visibility_update_event,
)

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Any

    from aiohttp.test_utils import TestClient

    from owlbot.api.context import ModuleContext
    from owlbot.api.storage import ModuleStorage
    from owlbot.registries.events import EventDispatcher
    from owlbot.testing import RecordingOwncastClient


_REAL_ASYNCIO_SLEEP = asyncio.sleep


@pytest.fixture
def module_pkg() -> ModuleType:
    """Auto-wire module_name, templates, and handler registration for timers."""
    return timers_pkg


@pytest.fixture
async def repo(storage: ModuleStorage) -> TimerRepository:
    """TimerRepository backed by storage, with schema initialized."""
    repo = TimerRepository(storage)
    await repo.setup()
    return repo


@pytest.fixture
def manager(module_context: ModuleContext, repo: TimerRepository) -> TimerManager:
    """TimerManager built directly for unit tests; does not run @on_setup."""
    return TimerManager(module_context, repo)


@pytest.fixture
def _suppress_timer_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Timer.start so enabling a timer does not spawn a background task."""
    monkeypatch.setattr(Timer, "start", lambda _self: None)


def _send_bodies(client: RecordingOwncastClient) -> list[str]:
    """Extract body strings from recorded send_message calls, in order."""
    return [c.kwargs["body"] for c in client.calls if c.name == "send_message"]


def _make_timer_info(
    *,
    id: int = 1,  # noqa: A002
    name: str | None = None,
    message: str | None = None,
    interval_type: IntervalType = IntervalType.SIMPLE,
    interval_value: str = "15m",
    min_chat_lines: int = 0,
    enabled: bool = False,
    created_at: str = "2026-04-17T10:00:00+00:00",
    updated_at: str = "2026-04-17T10:00:00+00:00",
    last_fired_at: str | None = None,
) -> TimerInfo:
    """Build a TimerInfo with sensible test defaults."""
    return TimerInfo(
        id=id,
        name=name,
        message=message,
        interval_type=interval_type,
        interval_value=interval_value,
        min_chat_lines=min_chat_lines,
        enabled=enabled,
        created_at=created_at,
        updated_at=updated_at,
        last_fired_at=last_fired_at,
    )


async def _seed_enabled(
    repo: TimerRepository,
    *,
    name: str | None = "greet",
    message: str = "Hello!",
    interval_value: str = "15m",
    interval_type: IntervalType = IntervalType.SIMPLE,
    min_chat_lines: int = 0,
) -> TimerInfo:
    """Create, configure, and enable a timer. Returns the final TimerInfo."""
    info = await repo.create(name)
    await repo.update_message(info.id, message)
    await repo.update_interval(info.id, interval_type, interval_value)
    if min_chat_lines:
        await repo.update_min_lines(info.id, min_chat_lines)
    return await repo.update_enabled(info.id, enabled=True)


class TestTypes:
    """Types layer: TimerInfo, TimerError hierarchy, NAME_PATTERN, interval helpers."""

    @pytest.mark.parametrize(
        ("member", "expected_value"),
        [
            pytest.param(IntervalType.SIMPLE, "simple", id="simple"),
            pytest.param(IntervalType.CRON, "cron", id="cron"),
        ],
    )
    def test_interval_type_values(
        self, member: IntervalType, expected_value: str
    ) -> None:
        """IntervalType members serialize to the strings the DB column expects."""
        assert member.value == expected_value

    def test_timer_info_from_row(self) -> None:
        """from_row populates all fields and coerces enabled to bool."""
        row = {
            "id": 7,
            "name": "greet",
            "message": "Hello",
            "interval_type": "simple",
            "interval_value": "15m",
            "min_chat_lines": 3,
            "enabled": 1,
            "created_at": "2026-04-17T10:00:00+00:00",
            "updated_at": "2026-04-17T11:00:00+00:00",
            "last_fired_at": "2026-04-17T10:30:00+00:00",
        }
        info = TimerInfo.from_row(row)  # type: ignore[arg-type]
        assert info.id == 7
        assert info.name == "greet"
        assert info.message == "Hello"
        assert info.interval_type == IntervalType.SIMPLE
        assert info.interval_value == "15m"
        assert info.min_chat_lines == 3
        assert info.enabled is True
        assert info.created_at == "2026-04-17T10:00:00+00:00"
        assert info.updated_at == "2026-04-17T11:00:00+00:00"
        assert info.last_fired_at == "2026-04-17T10:30:00+00:00"

    def test_timer_info_display_named(self) -> None:
        """Display uses '<name> (#<id>)' when named."""
        info = _make_timer_info(id=3, name="greet")
        assert info.display == "greet (#3)"

    def test_timer_info_display_unnamed(self) -> None:
        """Display uses 'timer #<id>' when unnamed."""
        info = _make_timer_info(id=3, name=None)
        assert info.display == "timer #3"

    @pytest.mark.parametrize(
        "error_cls",
        [
            pytest.param(TimerNotFoundError, id="not-found"),
            pytest.param(InvalidTimerNameError, id="invalid-name"),
            pytest.param(TimerNameTakenError, id="name-taken"),
            pytest.param(TimerAlreadyEnabledError, id="already-enabled"),
            pytest.param(TimerAlreadyDisabledError, id="already-disabled"),
            pytest.param(TimerMessageRequiredError, id="message-required"),
            pytest.param(NegativeLineCountError, id="negative-line-count"),
        ],
    )
    def test_error_subclasses_timer_error(self, error_cls: type[Exception]) -> None:
        """Every domain error subclasses TimerError."""
        assert issubclass(error_cls, TimerError)

    def test_not_found_stores_identifier(self) -> None:
        """TimerNotFoundError stores the identifier."""
        err = TimerNotFoundError("greet")
        assert err.identifier == "greet"

    def test_invalid_name_stores_name(self) -> None:
        """InvalidTimerNameError stores the invalid name."""
        err = InvalidTimerNameError("1bad")
        assert err.name == "1bad"

    def test_name_taken_stores_context(self) -> None:
        """TimerNameTakenError stores name and existing_id."""
        err = TimerNameTakenError("greet", existing_id=7)
        assert err.name == "greet"
        assert err.existing_id == 7

    def test_already_enabled_stores_info(self) -> None:
        """TimerAlreadyEnabledError stores the TimerInfo."""
        info = _make_timer_info(id=1, name="greet", message="m", enabled=True)
        err = TimerAlreadyEnabledError(info)
        assert err.info is info

    def test_already_disabled_stores_info(self) -> None:
        """TimerAlreadyDisabledError stores the TimerInfo."""
        info = _make_timer_info(id=1, message="m")
        err = TimerAlreadyDisabledError(info)
        assert err.info is info

    def test_message_required_stores_info(self) -> None:
        """TimerMessageRequiredError stores the TimerInfo."""
        info = _make_timer_info(id=1)
        err = TimerMessageRequiredError(info)
        assert err.info is info

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("greet", id="alpha"),
            pytest.param("_x", id="leading-underscore"),
            pytest.param("a1_b", id="alphanumeric-underscore"),
            pytest.param("a" * 32, id="max-length"),
            pytest.param("UPPER", id="uppercase-allowed"),
        ],
    )
    def test_name_pattern_valid(self, name: str) -> None:
        """NAME_PATTERN matches valid timer names."""
        assert NAME_PATTERN.match(name) is not None

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("1bad", id="leading-digit"),
            pytest.param("a" * 33, id="length-overflow"),
            pytest.param("has space", id="space"),
            pytest.param("has-dash", id="hyphen"),
            pytest.param("café", id="non-ascii"),
        ],
    )
    def test_name_pattern_invalid(self, name: str) -> None:
        """NAME_PATTERN rejects invalid timer names."""
        assert NAME_PATTERN.match(name) is None

    @pytest.mark.parametrize(
        ("value", "expected_type"),
        [
            pytest.param("60s", IntervalType.SIMPLE, id="seconds"),
            pytest.param("5m", IntervalType.SIMPLE, id="minutes"),
            pytest.param("1h", IntervalType.SIMPLE, id="hour"),
            pytest.param("2h30m", IntervalType.SIMPLE, id="hour-minutes"),
            pytest.param("1h1m1s", IntervalType.SIMPLE, id="combined"),
            pytest.param("* * * * *", IntervalType.CRON, id="cron-every-minute"),
            pytest.param("*/15 * * * *", IntervalType.CRON, id="cron-every-15"),
        ],
    )
    def test_parse_interval_valid(
        self, value: str, expected_type: IntervalType
    ) -> None:
        """parse_interval returns the correct type for valid inputs."""
        interval_type, _ = parse_interval(value)
        assert interval_type == expected_type

    def test_parse_interval_normalizes_simple(self) -> None:
        """parse_interval lowercases simple duration strings."""
        _, normalized = parse_interval("5M")
        assert normalized == "5m"

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            pytest.param("", "cannot be empty", id="empty"),
            pytest.param("   ", "cannot be empty", id="whitespace-only"),
            pytest.param("abc", "Invalid duration", id="not-a-duration"),
            pytest.param("10", "Invalid duration", id="no-suffix"),
            pytest.param("-5m", "Invalid duration", id="negative"),
            pytest.param("1.5m", "Invalid duration", id="decimal"),
            pytest.param("5s5m", "Invalid duration", id="wrong-order"),
            pytest.param("5m5m", "Invalid duration", id="duplicate-units"),
            pytest.param("10s", "Minimum interval", id="below-minimum"),
            pytest.param("59s", "Minimum interval", id="just-below-minimum"),
            pytest.param("0h0m0s", "Minimum interval", id="zero-total"),
            pytest.param("* * * *", "Invalid cron", id="cron-too-few-fields"),
            pytest.param("x * * * *", "Invalid cron", id="cron-bad-field"),
        ],
    )
    def test_parse_interval_invalid(self, value: str, match: str) -> None:
        """parse_interval raises ValueError; the message identifies the failure mode."""
        with pytest.raises(ValueError, match=match):
            parse_interval(value)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("30s", 30, id="seconds"),
            pytest.param("5m", 300, id="minutes"),
            pytest.param("2h30m", 9000, id="hour-minutes"),
            pytest.param("1h1m1s", 3661, id="combined"),
        ],
    )
    def test_duration_to_seconds(self, value: str, expected: int) -> None:
        """duration_to_seconds converts a valid duration string to seconds."""
        assert duration_to_seconds(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("abc", id="letters"),
            pytest.param("1x", id="invalid-unit"),
            pytest.param("5m5m", id="repeated-unit"),
        ],
    )
    def test_duration_to_seconds_invalid_returns_zero(self, value: str) -> None:
        """duration_to_seconds returns 0 for strings that do not match the pattern."""
        assert duration_to_seconds(value) == 0


class TestRepository:
    """TimerRepository CRUD behavior against real storage."""

    async def test_create_unnamed(self, repo: TimerRepository) -> None:
        """create() with name=None returns a populated disabled TimerInfo."""
        info = await repo.create(None)
        assert info.id == 1
        assert info.name is None
        assert info.message is None
        assert info.interval_type == IntervalType.SIMPLE
        assert info.interval_value == "15m"
        assert info.min_chat_lines == 0
        assert info.enabled is False
        assert info.created_at is not None
        assert info.last_fired_at is None

    async def test_create_named(self, repo: TimerRepository) -> None:
        """create() persists the provided name verbatim."""
        info = await repo.create("greet")
        assert info.name == "greet"

    async def test_create_auto_increments(self, repo: TimerRepository) -> None:
        """Sequential creates get sequential IDs."""
        first = await repo.create(None)
        second = await repo.create(None)
        assert second.id == first.id + 1

    async def test_create_invalid_name(self, repo: TimerRepository) -> None:
        """create() raises InvalidTimerNameError for a bad name."""
        with pytest.raises(InvalidTimerNameError) as exc_info:
            await repo.create("1bad")
        assert exc_info.value.name == "1bad"

    async def test_create_duplicate_name(self, repo: TimerRepository) -> None:
        """create() raises TimerNameTakenError for a duplicate name."""
        first = await repo.create("greet")
        with pytest.raises(TimerNameTakenError) as exc_info:
            await repo.create("greet")
        assert exc_info.value.name == "greet"
        assert exc_info.value.existing_id == first.id

    async def test_get_by_id(self, repo: TimerRepository) -> None:
        """get() returns the matching timer by integer ID string."""
        created = await repo.create("greet")
        fetched = await repo.get(str(created.id))
        assert fetched.id == created.id
        assert fetched.name == "greet"

    async def test_get_by_name(self, repo: TimerRepository) -> None:
        """get() returns the matching timer by name."""
        created = await repo.create("greet")
        fetched = await repo.get("greet")
        assert fetched.id == created.id

    async def test_get_not_found(self, repo: TimerRepository) -> None:
        """get() raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError) as exc_info:
            await repo.get("nobody")
        assert exc_info.value.identifier == "nobody"

    async def test_delete_removes_row(self, repo: TimerRepository) -> None:
        """delete() removes the row; a subsequent get() raises."""
        created = await repo.create("greet")
        await repo.delete(created.id)
        with pytest.raises(TimerNotFoundError):
            await repo.get(str(created.id))

    async def test_update_message(self, repo: TimerRepository) -> None:
        """update_message() returns updated TimerInfo and bumps updated_at."""
        created = await repo.create(None)
        updated = await repo.update_message(created.id, "Hello!")
        assert updated.message == "Hello!"
        assert updated.updated_at != created.updated_at

    async def test_update_message_not_found(self, repo: TimerRepository) -> None:
        """update_message() raises TimerNotFoundError for an unknown ID."""
        with pytest.raises(TimerNotFoundError):
            await repo.update_message(999, "Hello!")

    async def test_update_interval(self, repo: TimerRepository) -> None:
        """update_interval() persists type and value."""
        created = await repo.create(None)
        updated = await repo.update_interval(
            created.id, IntervalType.CRON, "*/15 * * * *"
        )
        assert updated.interval_type == IntervalType.CRON
        assert updated.interval_value == "*/15 * * * *"

    async def test_update_interval_not_found(self, repo: TimerRepository) -> None:
        """update_interval() raises TimerNotFoundError for an unknown ID."""
        with pytest.raises(TimerNotFoundError):
            await repo.update_interval(999, IntervalType.SIMPLE, "5m")

    async def test_update_min_lines(self, repo: TimerRepository) -> None:
        """update_min_lines() persists the count."""
        created = await repo.create(None)
        updated = await repo.update_min_lines(created.id, 5)
        assert updated.min_chat_lines == 5

    async def test_update_min_lines_not_found(self, repo: TimerRepository) -> None:
        """update_min_lines() raises TimerNotFoundError for an unknown ID."""
        with pytest.raises(TimerNotFoundError):
            await repo.update_min_lines(999, 5)

    async def test_update_enabled(self, repo: TimerRepository) -> None:
        """update_enabled() flips the enabled flag."""
        created = await repo.create(None)
        enabled = await repo.update_enabled(created.id, enabled=True)
        assert enabled.enabled is True
        disabled = await repo.update_enabled(created.id, enabled=False)
        assert disabled.enabled is False

    async def test_update_enabled_not_found(self, repo: TimerRepository) -> None:
        """update_enabled() raises TimerNotFoundError for an unknown ID."""
        with pytest.raises(TimerNotFoundError):
            await repo.update_enabled(999, enabled=True)

    async def test_update_last_fired_at(self, repo: TimerRepository) -> None:
        """update_last_fired_at() persists an ISO timestamp."""
        created = await repo.create(None)
        await repo.update_last_fired_at(created.id, "2026-04-17T12:00:00+00:00")
        fetched = await repo.get(str(created.id))
        assert fetched.last_fired_at == "2026-04-17T12:00:00+00:00"

    async def test_list_all(self, repo: TimerRepository) -> None:
        """list_all() returns timers ordered by ID."""
        first = await repo.create(None)
        second = await repo.create(None)
        rows = await repo.list_all()
        assert [r.id for r in rows] == [first.id, second.id]

    async def test_list_all_empty(self, repo: TimerRepository) -> None:
        """list_all() returns an empty list when no timers exist."""
        assert await repo.list_all() == []

    async def test_list_startable_filters(self, repo: TimerRepository) -> None:
        """list_startable() returns only enabled timers that have a message."""
        # enabled + message => startable
        startable = await repo.create("ready")
        await repo.update_message(startable.id, "Hello!")
        await repo.update_enabled(startable.id, enabled=True)

        # enabled + no message => excluded
        no_message = await repo.create("no_message")
        await repo.update_enabled(no_message.id, enabled=True)

        # disabled + message => excluded
        disabled = await repo.create("disabled")
        await repo.update_message(disabled.id, "Hello!")

        rows = await repo.list_startable()
        assert [r.id for r in rows] == [startable.id]

    async def test_list_startable_empty(self, repo: TimerRepository) -> None:
        """list_startable() returns an empty list when nothing qualifies."""
        await repo.create("disabled")
        assert await repo.list_startable() == []


class TestTimer:
    """Timer construction guards, start/stop/running, counters, and fire loop."""

    def test_construction_requires_message(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Constructing a Timer without a message raises TimerMessageRequiredError."""
        info = _make_timer_info(message=None)
        with pytest.raises(TimerMessageRequiredError) as exc_info:
            Timer(info, module_context, repo)
        assert exc_info.value.info is info

    def test_construction_rejects_zero_simple_interval(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Constructing a simple-interval Timer with empty value raises ValueError."""
        info = _make_timer_info(message="Hello!", interval_value="")
        with pytest.raises(ValueError, match="invalid simple interval"):
            Timer(info, module_context, repo)

    def test_construction_rejects_invalid_cron(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Constructing a cron Timer with a bad expression raises ValueError."""
        info = _make_timer_info(
            message="Hello!",
            interval_type=IntervalType.CRON,
            interval_value="not a cron",
        )
        with pytest.raises(ValueError, match="invalid cron expression"):
            Timer(info, module_context, repo)

    @pytest.mark.usefixtures("fast_sleep")
    async def test_start_and_stop(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Start spawns a running task; stop cancels it."""
        info = _make_timer_info(message="Hello!")
        timer = Timer(info, module_context, repo)
        assert not timer.running
        timer.start()
        running_after_start: bool = timer.running
        assert running_after_start
        await timer.stop()
        running_after_stop: bool = timer.running
        assert not running_after_stop

    @pytest.mark.usefixtures("fast_sleep")
    async def test_start_is_idempotent(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Calling start twice does not spawn a second task."""
        info = _make_timer_info(message="Hello!")
        timer = Timer(info, module_context, repo)
        timer.start()
        first_task = timer._task  # intentional test of internal state
        timer.start()
        assert timer._task is first_task
        await timer.stop()

    async def test_stop_noop_when_never_started(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Stop is a no-op when start was never called."""
        info = _make_timer_info(message="Hello!")
        timer = Timer(info, module_context, repo)
        await timer.stop()  # must not raise
        assert not timer.running

    def test_count_message_when_threshold_on(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """count_message adds to the set when min_chat_lines > 0."""
        info = _make_timer_info(message="Hello!", min_chat_lines=3)
        timer = Timer(info, module_context, repo)
        timer.count_message("m1")
        assert "m1" in timer._counted_messages

    def test_count_message_when_threshold_off(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """count_message is a no-op when min_chat_lines == 0."""
        info = _make_timer_info(message="Hello!", min_chat_lines=0)
        timer = Timer(info, module_context, repo)
        timer.count_message("m1")
        assert timer._counted_messages == set()

    def test_remove_messages_when_threshold_on(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """remove_messages subtracts the given IDs when min_chat_lines > 0."""
        info = _make_timer_info(message="Hello!", min_chat_lines=3)
        timer = Timer(info, module_context, repo)
        timer.count_message("m1")
        timer.count_message("m2")
        timer.remove_messages({"m1"})
        assert timer._counted_messages == {"m2"}

    def test_remove_messages_when_threshold_off(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """remove_messages is a no-op when min_chat_lines == 0."""
        info = _make_timer_info(message="Hello!", min_chat_lines=0)
        timer = Timer(info, module_context, repo)
        timer.remove_messages({"m1"})
        assert timer._counted_messages == set()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_fire_loop_simple_fires(
        self,
        repo: TimerRepository,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """A simple-interval timer fires send_message and persists last_fired_at."""
        created = await repo.create(None)
        await repo.update_message(created.id, "Hello!")
        info = await repo.update_enabled(created.id, enabled=True)
        timer = Timer(info, module_context, repo)
        timer.start()
        await _REAL_ASYNCIO_SLEEP(0.02)
        await timer.stop()

        assert "Hello!" in _send_bodies(owncast_client)
        refetched = await repo.get(str(created.id))
        assert refetched.last_fired_at is not None

    @pytest.mark.usefixtures("fast_sleep")
    async def test_fire_loop_cron_fires(
        self,
        repo: TimerRepository,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """A cron-interval timer fires on its first iteration."""
        created = await repo.create(None)
        await repo.update_message(created.id, "Cron fired!")
        await repo.update_interval(created.id, IntervalType.CRON, "* * * * *")
        info = await repo.update_enabled(created.id, enabled=True)
        timer = Timer(info, module_context, repo)
        timer.start()
        await _REAL_ASYNCIO_SLEEP(0.02)
        await timer.stop()

        assert "Cron fired!" in _send_bodies(owncast_client)

    @pytest.mark.usefixtures("fast_sleep")
    async def test_fire_loop_below_threshold_skips(
        self,
        repo: TimerRepository,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """When min_chat_lines is not met, the loop advances schedule but skips send."""
        created = await repo.create(None)
        await repo.update_message(created.id, "Threshold!")
        await repo.update_min_lines(created.id, 3)
        info = await repo.update_enabled(created.id, enabled=True)
        timer = Timer(info, module_context, repo)
        timer.start()
        await _REAL_ASYNCIO_SLEEP(0.02)
        await timer.stop()

        assert "Threshold!" not in _send_bodies(owncast_client)
        refetched = await repo.get(str(created.id))
        assert refetched.last_fired_at is not None

    @pytest.mark.usefixtures("fast_sleep")
    async def test_fire_loop_at_threshold_fires_and_clears_counter(
        self,
        repo: TimerRepository,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """When min_chat_lines is met, the loop fires and clears the counter."""
        created = await repo.create(None)
        await repo.update_message(created.id, "Met!")
        await repo.update_min_lines(created.id, 2)
        info = await repo.update_enabled(created.id, enabled=True)
        timer = Timer(info, module_context, repo)
        timer.count_message("m1")
        timer.count_message("m2")
        timer.start()
        await _REAL_ASYNCIO_SLEEP(0.02)
        await timer.stop()

        assert "Met!" in _send_bodies(owncast_client)
        assert timer._counted_messages == set()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_fire_loop_advances_schedule_on_send_error(
        self,
        repo: TimerRepository,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Send failure is swallowed and last_fired_at still advances."""

        async def _raise(*_args: object, **_kwargs: object) -> str:
            await asyncio.sleep(0)  # yield so cancellation can propagate
            raise OwncastError(500, "boom")

        monkeypatch.setattr(owncast_client, "send_message", _raise)

        created = await repo.create(None)
        await repo.update_message(created.id, "Hello!")
        info = await repo.update_enabled(created.id, enabled=True)
        timer = Timer(info, module_context, repo)
        timer.start()
        await _REAL_ASYNCIO_SLEEP(0.02)
        running_while_failing: bool = timer.running
        await timer.stop()

        assert running_while_failing
        refetched = await repo.get(str(created.id))
        assert refetched.last_fired_at is not None


class TestTimerManager:
    """TimerManager collection ops, CRUD, setters, enable/disable, offline stop."""

    @pytest.mark.usefixtures("fast_sleep")
    async def test_start_timer_adds_to_collection(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Start_timer adds to _timers and the Timer is running."""
        info = await _seed_enabled(repo)
        manager.start_timer(info)
        assert info.id in manager._timers
        assert manager._timers[info.id].running is True
        await manager.stop_all()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_start_timer_idempotent(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Start_timer called twice keeps the same Timer instance."""
        info = await _seed_enabled(repo)
        manager.start_timer(info)
        first = manager._timers[info.id]
        manager.start_timer(info)
        assert manager._timers[info.id] is first
        await manager.stop_all()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_stop_timer_removes_and_cancels(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Stop_timer removes from _timers and cancels the task."""
        info = await _seed_enabled(repo)
        manager.start_timer(info)
        await manager.stop_timer(info.id)
        assert info.id not in manager._timers

    async def test_stop_timer_unknown_id_is_noop(self, manager: TimerManager) -> None:
        """Stop_timer for an unknown ID does not raise."""
        await manager.stop_timer(999)  # must not raise

    @pytest.mark.usefixtures("fast_sleep")
    async def test_restart_timer_replaces_instance(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Restart_timer replaces the tracked Timer instance."""
        info = await _seed_enabled(repo)
        manager.start_timer(info)
        first = manager._timers[info.id]
        await manager.restart_timer(info)
        second = manager._timers[info.id]
        assert second is not first
        await manager.stop_all()

    async def test_restart_timer_noop_when_not_running(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Restart_timer is a no-op when the timer is not in _timers."""
        info = await _seed_enabled(repo)
        await manager.restart_timer(info)
        assert info.id not in manager._timers

    @pytest.mark.usefixtures("fast_sleep")
    async def test_start_all_starts_only_startable(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Start_all starts exactly the timers that are enabled and have a message."""
        startable = await _seed_enabled(repo, name="ready")
        no_message = await repo.create("no_message")
        await repo.update_enabled(no_message.id, enabled=True)
        disabled = await repo.create("disabled")
        await repo.update_message(disabled.id, "Hello")

        await manager.start_all()
        assert set(manager._timers.keys()) == {startable.id}
        await manager.stop_all()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_stop_all_empties_collection(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Stop_all cancels every tracked timer and empties _timers."""
        info_a = await _seed_enabled(repo, name="a")
        info_b = await _seed_enabled(repo, name="b")
        manager.start_timer(info_a)
        manager.start_timer(info_b)
        await manager.stop_all()
        assert manager._timers == {}

    async def test_create_timer_returns_info(self, manager: TimerManager) -> None:
        """Create_timer delegates to the repo and returns the new TimerInfo."""
        info = await manager.create_timer("greet")
        assert info.name == "greet"

    async def test_create_timer_invalid_name_bubbles(
        self, manager: TimerManager
    ) -> None:
        """Create_timer propagates InvalidTimerNameError."""
        with pytest.raises(InvalidTimerNameError):
            await manager.create_timer("1bad")

    async def test_create_timer_duplicate_name_bubbles(
        self, manager: TimerManager
    ) -> None:
        """Create_timer propagates TimerNameTakenError."""
        await manager.create_timer("greet")
        with pytest.raises(TimerNameTakenError):
            await manager.create_timer("greet")

    @pytest.mark.usefixtures("fast_sleep")
    async def test_delete_timer_stops_running(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Delete_timer stops the task, deletes the row, returns the snapshot."""
        info = await _seed_enabled(repo, name="doomed")
        manager.start_timer(info)
        deleted = await manager.delete_timer("doomed")
        assert deleted.name == "doomed"
        assert info.id not in manager._timers
        assert await manager.list_timers() == []

    async def test_delete_timer_not_found(self, manager: TimerManager) -> None:
        """Delete_timer raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError):
            await manager.delete_timer("nobody")

    @pytest.mark.usefixtures("fast_sleep")
    async def test_set_message_persists_and_restarts(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Set_message persists and restarts the timer when running."""
        info = await _seed_enabled(repo, name="greet")
        manager.start_timer(info)
        first = manager._timers[info.id]
        updated = await manager.set_message("greet", "Updated!")
        assert updated.message == "Updated!"
        assert manager._timers[info.id] is not first
        await manager.stop_all()

    async def test_set_message_does_not_spawn_task(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Set_message on a non-running timer updates the DB only."""
        created = await repo.create("greet")
        await manager.set_message("greet", "Hello!")
        assert created.id not in manager._timers

    @pytest.mark.usefixtures("fast_sleep")
    async def test_set_interval_persists_and_restarts(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Set_interval persists and restarts the timer when running."""
        info = await _seed_enabled(repo, name="greet")
        manager.start_timer(info)
        updated = await manager.set_interval("greet", IntervalType.CRON, "*/15 * * * *")
        assert updated.interval_type == IntervalType.CRON
        assert updated.interval_value == "*/15 * * * *"
        await manager.stop_all()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_set_min_chat_lines_persists_and_restarts(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Set_min_chat_lines persists the count and restarts the running timer."""
        info = await _seed_enabled(repo, name="greet")
        manager.start_timer(info)
        updated = await manager.set_min_chat_lines("greet", 5)
        assert updated.min_chat_lines == 5
        await manager.stop_all()

    async def test_set_min_chat_lines_rejects_negative(
        self, manager: TimerManager
    ) -> None:
        """Set_min_chat_lines raises NegativeLineCountError before touching the DB."""
        await manager.create_timer("greet")
        with pytest.raises(NegativeLineCountError):
            await manager.set_min_chat_lines("greet", -1)

    async def test_setters_bubble_not_found(self, manager: TimerManager) -> None:
        """Every setter raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError):
            await manager.set_message("nobody", "Hello!")
        with pytest.raises(TimerNotFoundError):
            await manager.set_interval("nobody", IntervalType.SIMPLE, "5m")
        with pytest.raises(TimerNotFoundError):
            await manager.set_min_chat_lines("nobody", 3)

    @pytest.mark.usefixtures("fast_sleep")
    async def test_enable_timer_starts_task(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Enable_timer flips enabled=True and starts a task."""
        created = await repo.create("greet")
        await repo.update_message(created.id, "Hello!")
        info = await manager.enable_timer("greet")
        assert info.enabled is True
        assert info.id in manager._timers
        await manager.stop_all()

    async def test_enable_timer_already_enabled(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Enable_timer raises TimerAlreadyEnabledError without mutating the DB."""
        info = await _seed_enabled(repo, name="greet")
        with pytest.raises(TimerAlreadyEnabledError) as exc_info:
            await manager.enable_timer("greet")
        assert exc_info.value.info.id == info.id

    async def test_enable_timer_requires_message(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Enable_timer raises TimerMessageRequiredError for a message-less timer."""
        created = await repo.create("greet")
        with pytest.raises(TimerMessageRequiredError) as exc_info:
            await manager.enable_timer("greet")
        assert exc_info.value.info.id == created.id

    async def test_enable_timer_not_found(self, manager: TimerManager) -> None:
        """Enable_timer raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError):
            await manager.enable_timer("nobody")

    @pytest.mark.usefixtures("fast_sleep")
    async def test_disable_timer_stops_task(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Disable_timer flips enabled=False and stops the task."""
        info = await _seed_enabled(repo, name="greet")
        manager.start_timer(info)
        disabled = await manager.disable_timer("greet")
        assert disabled.enabled is False
        assert info.id not in manager._timers

    async def test_disable_timer_already_disabled(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Disable_timer raises TimerAlreadyDisabledError for a disabled timer."""
        await repo.create("greet")
        with pytest.raises(TimerAlreadyDisabledError):
            await manager.disable_timer("greet")

    async def test_disable_timer_not_found(self, manager: TimerManager) -> None:
        """Disable_timer raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError):
            await manager.disable_timer("nobody")

    async def test_list_timers_ordered(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """List_timers returns every row ordered by ID."""
        await repo.create("first")
        await repo.create("second")
        rows = await manager.list_timers()
        assert [r.name for r in rows] == ["first", "second"]

    @pytest.mark.usefixtures("fast_sleep")
    async def test_count_message_distributes_to_all(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Count_message distributes to every live Timer."""
        info_a = await _seed_enabled(repo, name="a", min_chat_lines=5)
        info_b = await _seed_enabled(repo, name="b", min_chat_lines=5)
        manager.start_timer(info_a)
        manager.start_timer(info_b)
        manager.count_message("m1")
        assert "m1" in manager._timers[info_a.id]._counted_messages
        assert "m1" in manager._timers[info_b.id]._counted_messages
        await manager.stop_all()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_remove_messages_distributes_to_all(
        self, repo: TimerRepository, manager: TimerManager
    ) -> None:
        """Remove_messages distributes to every live Timer."""
        info = await _seed_enabled(repo, name="a", min_chat_lines=5)
        manager.start_timer(info)
        manager.count_message("m1")
        manager.count_message("m2")
        manager.remove_messages({"m1"})
        assert manager._timers[info.id]._counted_messages == {"m2"}
        await manager.stop_all()

    async def test_schedule_offline_stop_creates_task(
        self, manager: TimerManager
    ) -> None:
        """Schedule_offline_stop populates _offline_stop_task."""
        manager.schedule_offline_stop()
        assert manager._offline_stop_task is not None
        manager.cancel_offline_stop()

    async def test_cancel_offline_stop_clears_task(self, manager: TimerManager) -> None:
        """Cancel_offline_stop clears _offline_stop_task."""
        manager.schedule_offline_stop()
        manager.cancel_offline_stop()
        assert manager._offline_stop_task is None

    async def test_schedule_offline_stop_cancels_previous(
        self, manager: TimerManager
    ) -> None:
        """Schedule_offline_stop called twice cancels the prior task."""
        manager.schedule_offline_stop()
        first = manager._offline_stop_task
        manager.schedule_offline_stop()
        second = manager._offline_stop_task
        assert second is not first
        assert first is not None
        await _REAL_ASYNCIO_SLEEP(0)
        assert first.cancelled() or first.done()
        manager.cancel_offline_stop()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_delayed_offline_stop_stops_all(
        self,
        repo: TimerRepository,
        manager: TimerManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_delayed_offline_stop stops every tracked timer after the grace period."""
        monkeypatch.setattr(TimerManager, "OFFLINE_STOP_DELAY", 0.0)
        info = await _seed_enabled(repo, name="greet")
        manager.start_timer(info)
        manager.schedule_offline_stop()
        await _REAL_ASYNCIO_SLEEP(0.02)
        assert manager._timers == {}


class TestGetManager:
    """get_manager() helper."""

    def test_returns_stored_manager(
        self, module_context: ModuleContext, repo: TimerRepository
    ) -> None:
        """Get_manager returns the TimerManager stored in ctx.state."""
        mgr = TimerManager(module_context, repo)
        module_context.state["manager"] = mgr
        assert get_manager(module_context) is mgr

    def test_raises_if_not_initialized(self, module_context: ModuleContext) -> None:
        """Get_manager raises RuntimeError when no manager is stored."""
        with pytest.raises(RuntimeError, match="TimerManager is not initialized"):
            get_manager(module_context)


@pytest.mark.usefixtures("_suppress_timer_tasks")
class TestModuleLifecycle:
    """@on_setup and @on_teardown hooks."""

    async def test_setup_creates_table_and_manager(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """Setup creates the timers table and stashes a TimerManager in state."""
        await setup(module_context)

        row = await storage.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='timers'"
        )
        assert row is not None
        assert row["name"] == "timers"

        mgr = module_context.state["manager"]
        assert isinstance(mgr, TimerManager)

        await teardown(module_context)

    async def test_setup_offline_does_not_start_timers(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
    ) -> None:
        """Setup with stream offline does not populate manager._timers."""
        await _seed_enabled(repo, name="greet")
        await setup(module_context)

        mgr = module_context.state["manager"]
        assert mgr._timers == {}

        await teardown(module_context)

    async def test_setup_online_starts_timers(
        self,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
        repo: TimerRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setup with stream online starts every startable timer."""
        seeded = await _seed_enabled(repo, name="greet")
        monkeypatch.setattr(
            owncast_client,
            "get_status",
            AsyncMock(return_value={"online": True}),
        )
        await setup(module_context)

        mgr = module_context.state["manager"]
        assert seeded.id in mgr._timers

        await teardown(module_context)

    async def test_setup_status_error_is_non_fatal(
        self,
        module_context: ModuleContext,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setup does not raise when get_status fails."""
        monkeypatch.setattr(
            owncast_client,
            "get_status",
            AsyncMock(side_effect=OwncastError(500, "unavailable")),
        )
        await setup(module_context)  # must not raise

        mgr = module_context.state["manager"]
        assert mgr._timers == {}

        await teardown(module_context)

    async def test_teardown_cancels_offline_stop_and_clears_manager(
        self, module_context: ModuleContext
    ) -> None:
        """Teardown cancels offline stop and sets state['manager'] to None."""
        await setup(module_context)
        mgr = module_context.state["manager"]
        mgr.schedule_offline_stop()
        await teardown(module_context)

        assert module_context.state["manager"] is None


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestAddTimerCommand:
    """!addtimer command handler."""

    async def test_addtimer_unnamed(
        self,
        manager: TimerManager,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addtimer with no args creates an unnamed timer."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addtimer", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer timer #1 created."
        rows = await manager.list_timers()
        assert len(rows) == 1
        assert rows[0].name is None

    async def test_addtimer_named(
        self,
        manager: TimerManager,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addtimer <name> creates a named timer."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addtimer greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer greet (#1) created."
        rows = await manager.list_timers()
        assert rows[0].name == "greet"

    async def test_addtimer_invalid_name(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addtimer with an invalid name sends the full error text."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addtimer 1bad", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Invalid timer name. Must start with a letter or underscore, "
            "followed by letters, numbers, or underscores "
            "(max 32 characters)."
        )

    async def test_addtimer_duplicate_name(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addtimer with a duplicate name reports the conflict."""
        user = make_user(is_moderator=True)
        first = make_chat_event(raw_body="!addtimer greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, first)
        second = make_chat_event(raw_body="!addtimer greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, second)

        bodies = _send_bodies(owncast_client)
        assert bodies[1] == "A timer named 'greet' already exists (#1)."


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestSetTimerMessageCommand:
    """!settimermessage command handler."""

    async def test_sets_message(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimermessage <id> <text> persists and confirms."""
        created = await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(
            raw_body=f"!settimermessage {created.id} Hello there",
            user=user,
        )
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Message set for timer #1."
        refetched = await repo.get(str(created.id))
        assert refetched.message == "Hello there"

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimermessage with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimermessage 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Usage: !settimermessage <id|name> <message>"
        )

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimermessage for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimermessage 999 x", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestSetTimerIntervalCommand:
    """!settimerinterval command handler."""

    async def test_sets_simple(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerinterval <id> <simple> persists and confirms."""
        created = await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(
            raw_body=f"!settimerinterval {created.id} 1h",
            user=user,
        )
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Interval for timer #1 set to 1h (simple)."
        )
        refetched = await repo.get(str(created.id))
        assert refetched.interval_value == "1h"
        assert refetched.interval_type == IntervalType.SIMPLE

    async def test_sets_cron(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerinterval <id> <cron> persists and confirms."""
        created = await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(
            raw_body=f"!settimerinterval {created.id} */15 * * * *",
            user=user,
        )
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Interval for timer #1 set to */15 * * * * (cron)."
        )
        refetched = await repo.get(str(created.id))
        assert refetched.interval_type == IntervalType.CRON

    async def test_invalid_duration(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerinterval with an invalid duration echoes the ValueError text."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerinterval 1 abc", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        body = _send_bodies(owncast_client)[0]
        assert "Invalid duration" in body
        assert "abc" in body

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerinterval with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerinterval 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Usage: !settimerinterval <id|name> <interval>"
        )

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerinterval for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerinterval 999 1h", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestSetTimerLinesCommand:
    """!settimerlines command handler."""

    async def test_sets_positive(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines <id> <n> persists and confirms."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 1 5", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Minimum chat lines for timer #1 set to 5 line(s)."
        )
        refetched = await repo.get("1")
        assert refetched.min_chat_lines == 5

    async def test_sets_zero_formats_disabled(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines <id> 0 formats 'disabled' in the response."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 1 0", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Minimum chat lines for timer #1 set to disabled."
        )

    async def test_non_int_count(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines with a non-numeric count reports the error."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 1 abc", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Line count must be a number."

    async def test_negative_count(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines with a negative count reports the error."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 1 -1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Line count cannot be negative."

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0] == "Usage: !settimerlines <id|name> <count>"
        )

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!settimerlines for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!settimerlines 999 5", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestEnableTimerCommand:
    """!enabletimer command handler."""

    async def test_enables(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!enabletimer flips enabled=True and tracks the timer in the manager."""
        created = await repo.create(None)
        await repo.update_message(created.id, "Hello!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!enabletimer 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer timer #1 enabled."
        refetched = await repo.get("1")
        assert refetched.enabled is True
        mgr = get_manager(module_context)
        assert created.id in mgr._timers

    async def test_requires_message(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!enabletimer without a message sends the explanatory error."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!enabletimer 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Cannot enable timer #1: no message set. "
            "Use !settimermessage to set one first."
        )

    async def test_already_enabled(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!enabletimer on an already-enabled timer reports the conflict."""
        await _seed_enabled(repo, name=None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!enabletimer 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer timer #1 is already enabled."

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!enabletimer for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!enabletimer 999", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!enabletimer with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!enabletimer", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !enabletimer <id|name>"


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestDisableTimerCommand:
    """!disabletimer command handler."""

    async def test_disables(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!disabletimer flips enabled=False and removes from manager tracking."""
        seeded = await _seed_enabled(repo, name="greet")
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)

        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!disabletimer greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer greet (#1) disabled."
        refetched = await repo.get("greet")
        assert refetched.enabled is False
        assert seeded.id not in mgr._timers

    async def test_already_disabled(
        self,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!disabletimer on a disabled timer reports the conflict."""
        await repo.create(None)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!disabletimer 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer timer #1 is already disabled."

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!disabletimer for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!disabletimer 999", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!disabletimer with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!disabletimer", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !disabletimer <id|name>"


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestDeleteTimerCommand:
    """!deletetimer command handler."""

    async def test_deletes(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletetimer removes the row and untracks the timer."""
        seeded = await _seed_enabled(repo, name="greet")
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)

        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletetimer greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer greet (#1) deleted."
        assert await mgr.list_timers() == []
        assert seeded.id not in mgr._timers

    async def test_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletetimer for an unknown id reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletetimer 999", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Timer '999' not found."

    async def test_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletetimer with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletetimer", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !deletetimer <id|name>"


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestListTimersCommand:
    """!listtimers command handler."""

    async def test_sends_url(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!listtimers sends the public URL of the /list route."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!listtimers", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Timers: http://localhost:8081/owlbot/timers/list"
        )


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestTimerListRoute:
    """/list HTML route."""

    async def test_list_empty(self, route_client: TestClient[Any, Any]) -> None:
        """GET /list with no timers serves the empty-state message."""
        resp = await route_client.get("/owlbot/timers/list")
        assert resp.status == 200
        assert resp.content_type == "text/html"
        body = await resp.text()
        assert "No timers have been created yet." in body

    async def test_list_populated(
        self,
        repo: TimerRepository,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /list renders each timer's fields."""
        enabled = await _seed_enabled(repo, name="greet", message="Hello!")
        await repo.update_last_fired_at(enabled.id, "2026-04-17T12:00:00+00:00")
        await repo.create("disabled")

        resp = await route_client.get("/owlbot/timers/list")
        assert resp.status == 200
        body = await resp.text()
        assert "greet" in body
        assert "Hello!" in body
        assert "15m (simple)" in body
        assert "Enabled" in body
        assert "Disabled" in body
        assert "2026-04-17 12:00:00 UTC" in body
        assert "Never" in body
        assert "(not set)" in body

    async def test_list_renders_unnamed_timer(
        self,
        repo: TimerRepository,
        route_client: TestClient[Any, Any],
    ) -> None:
        """Unnamed timers render without raising."""
        await repo.create(None)
        resp = await route_client.get("/owlbot/timers/list")
        assert resp.status == 200


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestStreamEvents:
    """handle_stream_started and handle_stream_stopped."""

    async def test_stream_started_starts_timers(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """STREAM_STARTED dispatch populates manager._timers for startable rows."""
        info_a = await _seed_enabled(repo, name="a")
        info_b = await _seed_enabled(repo, name="b")
        await event_dispatcher.dispatch(
            EventType.STREAM_STARTED, make_stream_started_event()
        )

        mgr = get_manager(module_context)
        assert {info_a.id, info_b.id} <= set(mgr._timers.keys())

    async def test_stream_stopped_schedules_offline_stop(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """STREAM_STOPPED dispatch populates _offline_stop_task."""
        await event_dispatcher.dispatch(
            EventType.STREAM_STOPPED, make_stream_stopped_event()
        )

        mgr = get_manager(module_context)
        assert mgr._offline_stop_task is not None
        mgr.cancel_offline_stop()

    async def test_stream_started_cancels_offline_stop(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """STREAM_STARTED clears a pending _offline_stop_task."""
        await event_dispatcher.dispatch(
            EventType.STREAM_STOPPED, make_stream_stopped_event()
        )
        await event_dispatcher.dispatch(
            EventType.STREAM_STARTED, make_stream_started_event()
        )

        mgr = get_manager(module_context)
        assert mgr._offline_stop_task is None


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestChatCountingEvent:
    """count_chat_message event handler."""

    async def test_counts_visible_user_message(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """A normal chat message increments the threshold counter."""
        seeded = await _seed_enabled(repo, name="greet", min_chat_lines=5)
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)

        event = make_chat_event(raw_body="hi", message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert "m1" in mgr._timers[seeded.id]._counted_messages

    async def test_ignores_bot_user(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """Bot messages are not counted."""
        seeded = await _seed_enabled(repo, name="greet", min_chat_lines=5)
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)

        bot = make_user(is_bot=True)
        event = make_chat_event(raw_body="beep", user=bot, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert mgr._timers[seeded.id]._counted_messages == set()

    async def test_ignores_hidden_message(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """Hidden messages are not counted."""
        seeded = await _seed_enabled(repo, name="greet", min_chat_lines=5)
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)

        event = make_chat_event(raw_body="oops", is_visible=False, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert mgr._timers[seeded.id]._counted_messages == set()


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestVisibilityUpdateEvent:
    """handle_visibility_update event handler."""

    async def test_hide_removes_counted_messages(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """A hide event removes the given message IDs from every timer counter."""
        seeded = await _seed_enabled(repo, name="greet", min_chat_lines=5)
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)
        mgr.count_message("m1")
        mgr.count_message("m2")
        mgr.count_message("m3")

        event = make_visibility_update_event(message_ids=["m1", "m2"], is_visible=False)
        await event_dispatcher.dispatch(EventType.VISIBILITY_UPDATE, event)

        assert mgr._timers[seeded.id]._counted_messages == {"m3"}

    async def test_show_is_noop(
        self,
        module_context: ModuleContext,
        repo: TimerRepository,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """A show event does not re-add anything to the counter."""
        seeded = await _seed_enabled(repo, name="greet", min_chat_lines=5)
        mgr = get_manager(module_context)
        mgr.start_timer(seeded)
        mgr.count_message("m1")

        event = make_visibility_update_event(message_ids=["m1"], is_visible=True)
        await event_dispatcher.dispatch(EventType.VISIBILITY_UPDATE, event)

        assert mgr._timers[seeded.id]._counted_messages == {"m1"}


@pytest.mark.usefixtures("_suppress_timer_tasks", "module_lifecycle")
class TestModeratorPermissions:
    """Moderator-required commands deny non-moderators."""

    @pytest.mark.parametrize(
        "command",
        [
            "addtimer",
            "settimermessage",
            "settimerinterval",
            "settimerlines",
            "enabletimer",
            "disabletimer",
            "deletetimer",
            "listtimers",
        ],
    )
    async def test_non_moderator_denied(
        self,
        command: str,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Non-moderator invocation triggers the dispatcher denial."""
        user = make_user(is_moderator=False)
        event = make_chat_event(raw_body=f"!{command}", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        denials = [
            c.kwargs["body"]
            for c in owncast_client.calls
            if c.name == "send_system_message_to_client"
        ]
        assert denials == [f"Only moderators can use !{command}."]
