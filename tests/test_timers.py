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

"""Tests for the timers module: types, repository, and manager."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from owlbot.api.context import ModuleContext
from owlbot.api.storage import ModuleStorage
from owlbot.builtin_modules.timers.manager import Timer, TimerManager, get_manager
from owlbot.builtin_modules.timers.repository import TimerRepository
from owlbot.builtin_modules.timers.types import (
    NAME_PATTERN,
    IntervalType,
    InvalidTimerNameError,
    NegativeLineCountError,
    TimerAlreadyDisabledError,
    TimerAlreadyEnabledError,
    TimerInfo,
    TimerMessageRequiredError,
    TimerNameTakenError,
    TimerNotFoundError,
    duration_to_seconds,
    parse_interval,
)
from tests.conftest import make_module_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _StubOwncastClient:
    """Minimal OwncastClient stand-in that records sent messages."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send_message(self, body: str, **_kwargs: bool) -> str:
        """Record *body* or raise if configured to fail."""
        if self._fail:
            raise RuntimeError("network error")
        self.sent.append(body)
        return "ok"


@pytest.fixture
async def timer_storage() -> AsyncIterator[ModuleStorage]:
    """Yield an open in-memory ModuleStorage."""
    async with ModuleStorage(None, "timers") as storage:
        yield storage


@pytest.fixture
async def repo(timer_storage: ModuleStorage) -> TimerRepository:
    """TimerRepository backed by timer_storage, with schema initialized."""
    r = TimerRepository(timer_storage)
    await r.setup()
    return r


@pytest.fixture
def timer_ctx(timer_storage: ModuleStorage) -> ModuleContext:
    """ModuleContext backed by timer_storage and a stub OwncastClient."""
    return ModuleContext(
        module_name="timers",
        config=None,  # type: ignore[arg-type]
        owncast_client=_StubOwncastClient(),  # type: ignore[arg-type]
        storage=timer_storage,
        commands=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        routes=None,  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        templates=None,  # type: ignore[arg-type]
        admin_client=None,
    )


@pytest.fixture
async def manager(
    timer_ctx: ModuleContext, repo: TimerRepository
) -> AsyncIterator[TimerManager]:
    """TimerManager that auto-stops on teardown."""
    mgr = TimerManager(timer_ctx, repo)
    yield mgr
    mgr.cancel_offline_stop()
    await mgr.stop_all()


def _make_timer_info(
    *,
    timer_id: int = 1,
    name: str | None = "test",
    message: str | None = "hello",
    interval_type: IntervalType = IntervalType.SIMPLE,
    interval_value: str = "1m",
    min_chat_lines: int = 0,
    enabled: bool = True,
    created_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-01T00:00:00+00:00",
    last_fired_at: str | None = None,
) -> TimerInfo:
    """Build a TimerInfo for testing."""
    return TimerInfo(
        id=timer_id,
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


class TestIntervalType:
    """IntervalType enum values."""

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            pytest.param(IntervalType.SIMPLE, "simple", id="simple"),
            pytest.param(IntervalType.CRON, "cron", id="cron"),
        ],
    )
    def test_values(self, member: IntervalType, value: str) -> None:
        """Enum members have the expected string values."""
        assert member == value
        assert member.value == value


class TestTimerInfoDisplay:
    """TimerInfo.display property formatting."""

    @pytest.mark.parametrize(
        ("name", "timer_id", "expected"),
        [
            pytest.param("hello", 1, "hello (#1)", id="named"),
            pytest.param(None, 42, "timer #42", id="unnamed"),
        ],
    )
    def test_display(self, name: str | None, timer_id: int, expected: str) -> None:
        """Display formats named and unnamed timers correctly."""
        info = _make_timer_info(timer_id=timer_id, name=name)
        assert info.display == expected


class TestTimerInfoFromRow:
    """TimerInfo.from_row() database row conversion."""

    async def test_builds_from_row(
        self, repo: TimerRepository, timer_storage: ModuleStorage
    ) -> None:
        """from_row constructs a TimerInfo with correct field types."""
        info = await repo.create("check")
        await repo.update_message(info.id, "msg")
        await repo.update_interval(info.id, IntervalType.CRON, "5m")
        await repo.update_min_lines(info.id, 3)

        row = await timer_storage.fetch_one(
            "SELECT * FROM timers WHERE id = ?", (info.id,)
        )
        assert row is not None
        result = TimerInfo.from_row(row)
        assert result.id == info.id
        assert result.name == "check"
        assert result.message == "msg"
        assert result.interval_type == IntervalType.CRON
        assert result.interval_value == "5m"
        assert result.min_chat_lines == 3
        assert result.enabled is False
        assert result.created_at is not None
        assert result.updated_at is not None
        assert result.last_fired_at is None


class TestNamePattern:
    """NAME_PATTERN regex for timer names."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("a", id="single-letter"),
            pytest.param("_a", id="underscore-start"),
            pytest.param("Timer1", id="mixed-case-digits"),
            pytest.param("a" * 32, id="max-length"),
            pytest.param("__init", id="double-underscore"),
        ],
    )
    def test_valid(self, name: str) -> None:
        """Valid names match the pattern."""
        assert NAME_PATTERN.match(name)

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("1start", id="digit-start"),
            pytest.param("", id="empty"),
            pytest.param("a" * 33, id="too-long"),
            pytest.param("has-dash", id="dash"),
            pytest.param("has space", id="space"),
            pytest.param("has.dot", id="dot"),
        ],
    )
    def test_invalid(self, name: str) -> None:
        """Invalid names do not match the pattern."""
        assert not NAME_PATTERN.match(name)


class TestParseInterval:
    """parse_interval() validation and normalization."""

    @pytest.mark.parametrize(
        ("value", "expected_type", "expected_value"),
        [
            pytest.param("60s", IntervalType.SIMPLE, "60s", id="seconds"),
            pytest.param("5m", IntervalType.SIMPLE, "5m", id="minutes"),
            pytest.param("1m", IntervalType.SIMPLE, "1m", id="exact-minimum"),
            pytest.param("1h", IntervalType.SIMPLE, "1h", id="hours"),
            pytest.param("2h30m", IntervalType.SIMPLE, "2h30m", id="hours-minutes"),
            pytest.param("5m30s", IntervalType.SIMPLE, "5m30s", id="minutes-seconds"),
            pytest.param("1h30s", IntervalType.SIMPLE, "1h30s", id="hours-seconds"),
            pytest.param("1H30M15S", IntervalType.SIMPLE, "1h30m15s", id="uppercase"),
        ],
    )
    def test_valid_simple(
        self, value: str, expected_type: IntervalType, expected_value: str
    ) -> None:
        """Valid simple durations are parsed and lowercased."""
        result_type, result_value = parse_interval(value)
        assert result_type == expected_type
        assert result_value == expected_value

    @pytest.mark.parametrize(
        ("value", "match"),
        [
            pytest.param("abc", "Invalid duration", id="not-a-duration"),
            pytest.param("10s", "Minimum interval", id="below-minimum"),
            pytest.param("59s", "Minimum interval", id="just-below-minimum"),
            pytest.param("0h0m0s", "Minimum interval", id="zero-total"),
            pytest.param("10", "Invalid duration", id="no-suffix"),
            pytest.param("-5m", "Invalid duration", id="negative"),
            pytest.param("1.5m", "Invalid duration", id="decimal"),
            pytest.param("5s5m", "Invalid duration", id="wrong-order"),
            pytest.param("5m5m", "Invalid duration", id="duplicate-units"),
        ],
    )
    def test_invalid_simple(self, value: str, match: str) -> None:
        """Invalid simple durations raise ValueError."""
        with pytest.raises(ValueError, match=match):
            parse_interval(value)

    def test_empty_raises(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_interval("")

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_interval("   ")

    def test_valid_cron(self) -> None:
        """A valid cron expression is accepted."""
        result_type, result_value = parse_interval("*/5 * * * *")
        assert result_type == IntervalType.CRON
        assert result_value == "*/5 * * * *"

    def test_invalid_cron(self) -> None:
        """An invalid cron expression raises ValueError."""
        with pytest.raises(ValueError, match="Invalid cron"):
            parse_interval("bad cron expr here now")


class TestDurationToSeconds:
    """duration_to_seconds() conversion."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("30s", 30, id="seconds"),
            pytest.param("5m", 300, id="minutes"),
            pytest.param("1h", 3600, id="hours"),
            pytest.param("2h30m", 9000, id="hours-minutes"),
            pytest.param("1h30m15s", 5415, id="all-units"),
            pytest.param("invalid", 0, id="invalid-returns-zero"),
        ],
    )
    def test_conversion(self, value: str, expected: int) -> None:
        """Durations are converted to the expected number of seconds."""
        assert duration_to_seconds(value) == expected


class TestTimerErrors:
    """Timer domain error classes."""

    def test_not_found_stores_identifier(self) -> None:
        """TimerNotFoundError stores the lookup identifier."""
        err = TimerNotFoundError("foo")
        assert err.identifier == "foo"
        assert "foo" in str(err)

    def test_not_found_with_int(self) -> None:
        """TimerNotFoundError accepts integer identifiers."""
        err = TimerNotFoundError(42)
        assert err.identifier == 42

    def test_invalid_name_stores_name(self) -> None:
        """InvalidTimerNameError stores the invalid name."""
        err = InvalidTimerNameError("bad!")
        assert err.name == "bad!"

    def test_name_taken_stores_details(self) -> None:
        """TimerNameTakenError stores name and existing ID."""
        err = TimerNameTakenError("test", 5)
        assert err.name == "test"
        assert err.existing_id == 5

    def test_already_enabled_stores_info(self) -> None:
        """TimerAlreadyEnabledError stores the timer info."""
        info = _make_timer_info()
        err = TimerAlreadyEnabledError(info)
        assert err.info is info

    def test_already_disabled_stores_info(self) -> None:
        """TimerAlreadyDisabledError stores the timer info."""
        info = _make_timer_info()
        err = TimerAlreadyDisabledError(info)
        assert err.info is info

    def test_message_required_stores_info(self) -> None:
        """TimerMessageRequiredError stores the timer info."""
        info = _make_timer_info(message=None)
        err = TimerMessageRequiredError(info)
        assert err.info is info


class TestRepositoryCreate:
    """TimerRepository.create() inserts new timers."""

    async def test_create_unnamed(self, repo: TimerRepository) -> None:
        """Creating without a name inserts a disabled timer with defaults."""
        info = await repo.create(None)
        assert info.id == 1
        assert info.name is None
        assert info.message is None
        assert info.interval_value == "15m"
        assert info.interval_type == IntervalType.SIMPLE
        assert info.enabled is False

    async def test_create_named(self, repo: TimerRepository) -> None:
        """Creating with a valid name stores it."""
        info = await repo.create("my_timer")
        assert info.name == "my_timer"

    async def test_create_invalid_name(self, repo: TimerRepository) -> None:
        """Creating with an invalid name raises InvalidTimerNameError."""
        with pytest.raises(InvalidTimerNameError):
            await repo.create("1bad")

    async def test_create_duplicate_name(self, repo: TimerRepository) -> None:
        """Creating with an existing name raises TimerNameTakenError."""
        await repo.create("dupe")
        with pytest.raises(TimerNameTakenError) as exc_info:
            await repo.create("dupe")
        assert exc_info.value.name == "dupe"
        assert exc_info.value.existing_id == 1


class TestRepositoryGet:
    """TimerRepository.get() resolves by ID or name."""

    async def test_get_by_id(self, repo: TimerRepository) -> None:
        """Resolves a timer by its numeric ID string."""
        info = await repo.create("foo")
        result = await repo.get(str(info.id))
        assert result.id == info.id
        assert result.name == "foo"

    async def test_get_by_name(self, repo: TimerRepository) -> None:
        """Resolves a timer by its name."""
        await repo.create("bar")
        info = await repo.get("bar")
        assert info.name == "bar"

    async def test_get_not_found(self, repo: TimerRepository) -> None:
        """Raises TimerNotFoundError for an unknown identifier."""
        with pytest.raises(TimerNotFoundError):
            await repo.get("nope")


class TestRepositoryUpdate:
    """TimerRepository update methods."""

    async def test_update_message(self, repo: TimerRepository) -> None:
        """update_message persists the new message text."""
        info = await repo.create("test")
        updated = await repo.update_message(info.id, "new msg")
        assert updated.message == "new msg"

    async def test_update_interval(self, repo: TimerRepository) -> None:
        """update_interval persists type and value."""
        info = await repo.create("test")
        updated = await repo.update_interval(info.id, IntervalType.CRON, "*/5 * * * *")
        assert updated.interval_type == IntervalType.CRON
        assert updated.interval_value == "*/5 * * * *"

    async def test_update_min_lines(self, repo: TimerRepository) -> None:
        """update_min_lines persists the threshold."""
        info = await repo.create("test")
        updated = await repo.update_min_lines(info.id, 10)
        assert updated.min_chat_lines == 10

    async def test_update_enabled(self, repo: TimerRepository) -> None:
        """update_enabled toggles the enabled flag."""
        info = await repo.create("test")
        updated = await repo.update_enabled(info.id, enabled=True)
        assert updated.enabled is True

    async def test_update_last_fired_at(self, repo: TimerRepository) -> None:
        """update_last_fired_at persists the timestamp."""
        info = await repo.create("test")
        ts = "2026-06-15T12:00:00+00:00"
        await repo.update_last_fired_at(info.id, ts)
        updated = await repo.get(str(info.id))
        assert updated.last_fired_at == ts


class TestRepositoryDelete:
    """TimerRepository.delete() removes records."""

    async def test_delete(self, repo: TimerRepository) -> None:
        """Deleted timer is no longer retrievable."""
        info = await repo.create("test")
        await repo.delete(info.id)
        with pytest.raises(TimerNotFoundError):
            await repo.get(str(info.id))


class TestRepositoryList:
    """TimerRepository list methods."""

    async def test_list_all(self, repo: TimerRepository) -> None:
        """list_all returns all timers ordered by ID."""
        await repo.create("a")
        await repo.create("b")
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].name == "a"
        assert result[1].name == "b"

    async def test_list_all_empty(self, repo: TimerRepository) -> None:
        """list_all returns an empty list when no timers exist."""
        assert await repo.list_all() == []

    async def test_list_startable(self, repo: TimerRepository) -> None:
        """list_startable returns only enabled timers with messages."""
        info_a = await repo.create("a")
        await repo.update_message(info_a.id, "m1")
        await repo.update_enabled(info_a.id, enabled=True)

        info_b = await repo.create("b")
        await repo.update_enabled(info_b.id, enabled=True)

        info_c = await repo.create("c")
        await repo.update_message(info_c.id, "m3")

        result = await repo.list_startable()
        assert len(result) == 1
        assert result[0].name == "a"


class TestTimer:
    """Tests for the Timer fire loop and counter."""

    async def test_start_sets_running(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """A started Timer reports running=True."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        timer = Timer(info, timer_ctx, repo)
        timer.start()
        assert timer.running
        await timer.stop()

    async def test_stop_clears_running_and_counter(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """A stopped Timer reports running=False and clears its counter."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        timer = Timer(info, timer_ctx, repo)
        timer.count_message("msg-1")
        timer.start()
        await timer.stop()
        assert not timer.running
        assert len(timer._counted_messages) == 0

    async def test_start_noop_if_running(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Calling start() twice does not spawn a second task."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        timer = Timer(info, timer_ctx, repo)
        timer.start()
        task_1 = timer._task
        timer.start()
        assert timer._task is task_1
        await timer.stop()

    async def test_count_message(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """count_message adds to the internal set, deduplicating IDs."""
        info = _make_timer_info(min_chat_lines=3)
        timer = Timer(info, timer_ctx, repo)
        timer.count_message("msg-1")
        timer.count_message("msg-2")
        timer.count_message("msg-1")
        assert len(timer._counted_messages) == 2

    async def test_count_message_noop_without_threshold(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """count_message is a no-op when min_chat_lines is 0."""
        info = _make_timer_info(min_chat_lines=0)
        timer = Timer(info, timer_ctx, repo)
        timer.count_message("msg-1")
        assert len(timer._counted_messages) == 0

    async def test_remove_messages(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """remove_messages removes specified IDs from the counter."""
        info = _make_timer_info(min_chat_lines=3)
        timer = Timer(info, timer_ctx, repo)
        timer.count_message("msg-1")
        timer.count_message("msg-2")
        timer.count_message("msg-3")
        timer.remove_messages({"msg-1", "msg-3"})
        assert timer._counted_messages == {"msg-2"}

    async def test_remove_messages_noop_without_threshold(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """remove_messages is a no-op when min_chat_lines is 0."""
        info = _make_timer_info(min_chat_lines=0)
        timer = Timer(info, timer_ctx, repo)
        timer.remove_messages({"msg-1"})
        assert len(timer._counted_messages) == 0

    def test_no_message_raises(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer raises TimerMessageRequiredError when message is None."""
        info = _make_timer_info(message=None)
        with pytest.raises(TimerMessageRequiredError):
            Timer(info, timer_ctx, repo)

    async def test_fires_when_never_fired(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer with no last_fired_at fires immediately."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello world")
        timer = Timer(info, timer_ctx, repo)
        timer.start()
        await asyncio.sleep(0.1)

        client: _StubOwncastClient = timer_ctx.owncast_client  # type: ignore[assignment]
        assert client.sent == ["hello world"]

        updated = await repo.get(str(info.id))
        assert updated.last_fired_at is not None
        assert timer.running
        await timer.stop()

    async def test_skips_when_threshold_not_met(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer advances schedule without firing when threshold isn't met."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        info = await repo.update_min_lines(info.id, 5)
        timer = Timer(info, timer_ctx, repo)
        timer.start()
        await asyncio.sleep(0.1)

        client: _StubOwncastClient = timer_ctx.owncast_client  # type: ignore[assignment]
        assert client.sent == []

        updated = await repo.get(str(info.id))
        assert updated.last_fired_at is not None
        await timer.stop()

    async def test_fires_when_threshold_met(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer fires when chat line threshold is satisfied."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        info = await repo.update_min_lines(info.id, 2)
        timer = Timer(info, timer_ctx, repo)
        timer.count_message("msg-1")
        timer.count_message("msg-2")
        timer.start()
        await asyncio.sleep(0.1)

        client: _StubOwncastClient = timer_ctx.owncast_client  # type: ignore[assignment]
        assert client.sent == ["hello"]
        assert len(timer._counted_messages) == 0
        await timer.stop()

    async def test_continues_after_send_error(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Fire loop keeps running after a send_message failure."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")

        client: _StubOwncastClient = timer_ctx.owncast_client  # type: ignore[assignment]
        client._fail = True

        timer = Timer(info, timer_ctx, repo)
        timer.start()
        await asyncio.sleep(0.1)

        assert timer.running
        await timer.stop()

    def test_rejects_invalid_simple_interval(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer refuses to initialize with a simple interval of zero seconds."""
        info = _make_timer_info(interval_type=IntervalType.SIMPLE, interval_value="bad")
        with pytest.raises(ValueError, match="invalid simple interval"):
            Timer(info, timer_ctx, repo)

    def test_rejects_invalid_cron_interval(
        self, timer_ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Timer refuses to initialize with an invalid cron expression."""
        info = _make_timer_info(
            interval_type=IntervalType.CRON, interval_value="not valid"
        )
        with pytest.raises(ValueError, match="invalid cron expression"):
            Timer(info, timer_ctx, repo)


class TestTimerManagerLifecycle:
    """TimerManager timer start/stop/restart operations."""

    async def test_start_timer(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """start_timer creates a running Timer in the collection."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        assert info.id in manager._timers
        assert manager._timers[info.id].running

    async def test_start_timer_noop_if_running(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """start_timer is a no-op when the timer already has a task."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        first = manager._timers[info.id]
        manager.start_timer(info)
        assert manager._timers[info.id] is first

    async def test_stop_timer(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """stop_timer cancels the task and removes it from the collection."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        await manager.stop_timer(info.id)
        assert info.id not in manager._timers

    async def test_stop_timer_noop_if_not_running(self, manager: TimerManager) -> None:
        """stop_timer on an unknown ID does not raise."""
        await manager.stop_timer(999)

    async def test_restart_timer(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """restart_timer replaces the Timer with a fresh instance."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        first = manager._timers[info.id]
        await manager.restart_timer(info)
        assert info.id in manager._timers
        assert manager._timers[info.id] is not first

    async def test_restart_noop_if_not_running(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """restart_timer is a no-op when the timer has no task."""
        info = await repo.create("test")
        await manager.restart_timer(info)
        assert info.id not in manager._timers

    async def test_start_all(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """start_all starts only enabled timers with messages."""
        info_a = await repo.create("a")
        await repo.update_message(info_a.id, "m1")
        await repo.update_enabled(info_a.id, enabled=True)

        info_b = await repo.create("b")
        await repo.update_message(info_b.id, "m2")
        await repo.update_enabled(info_b.id, enabled=True)

        info_c = await repo.create("c")
        await repo.update_message(info_c.id, "m3")

        info_d = await repo.create("d")
        await repo.update_enabled(info_d.id, enabled=True)

        await manager.start_all()
        assert info_a.id in manager._timers
        assert info_b.id in manager._timers
        assert info_c.id not in manager._timers
        assert info_d.id not in manager._timers

    async def test_stop_all(self, manager: TimerManager, repo: TimerRepository) -> None:
        """stop_all cancels every active timer."""
        info_a = await repo.create("a")
        await repo.update_message(info_a.id, "m1")
        await repo.update_enabled(info_a.id, enabled=True)

        info_b = await repo.create("b")
        await repo.update_message(info_b.id, "m2")
        await repo.update_enabled(info_b.id, enabled=True)

        await manager.start_all()
        assert len(manager._timers) == 2
        await manager.stop_all()
        assert len(manager._timers) == 0


class TestTimerManagerCrud:
    """TimerManager create, delete, and setter operations."""

    async def test_create_timer(self, manager: TimerManager) -> None:
        """create_timer inserts a new disabled timer."""
        info = await manager.create_timer("new_timer")
        assert info.name == "new_timer"
        assert info.enabled is False
        assert info.message is None

    async def test_create_timer_unnamed(self, manager: TimerManager) -> None:
        """create_timer with None creates an unnamed timer."""
        info = await manager.create_timer(None)
        assert info.name is None

    async def test_delete_timer(self, manager: TimerManager) -> None:
        """delete_timer removes the timer from the DB."""
        await manager.create_timer("doomed")
        info = await manager.delete_timer("doomed")
        assert info.name == "doomed"
        assert await manager.list_timers() == []

    async def test_delete_stops_running_timer(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """delete_timer stops a running timer task."""
        info = await repo.create("running")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        assert info.id in manager._timers
        await manager.delete_timer("running")
        assert info.id not in manager._timers

    async def test_set_message(self, manager: TimerManager) -> None:
        """set_message updates the timer's message."""
        await manager.create_timer("test")
        info = await manager.set_message("test", "new message")
        assert info.message == "new message"

    async def test_set_message_restarts_running(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """set_message restarts a running timer with fresh state."""
        info = await repo.create("test")
        info = await repo.update_message(info.id, "hello")
        manager.start_timer(info)
        first = manager._timers[info.id]
        await manager.set_message("test", "new msg")
        assert manager._timers[info.id] is not first

    async def test_set_interval(self, manager: TimerManager) -> None:
        """set_interval updates the timer's interval."""
        await manager.create_timer("test")
        info = await manager.set_interval("test", IntervalType.CRON, "*/5 * * * *")
        assert info.interval_type == IntervalType.CRON
        assert info.interval_value == "*/5 * * * *"

    async def test_set_min_chat_lines(self, manager: TimerManager) -> None:
        """set_min_chat_lines updates the threshold."""
        await manager.create_timer("test")
        info = await manager.set_min_chat_lines("test", 10)
        assert info.min_chat_lines == 10

    async def test_set_min_chat_lines_negative_raises(
        self, manager: TimerManager
    ) -> None:
        """set_min_chat_lines raises NegativeLineCountError for negatives."""
        await manager.create_timer("test")
        with pytest.raises(NegativeLineCountError):
            await manager.set_min_chat_lines("test", -1)

    async def test_list_timers(self, manager: TimerManager) -> None:
        """list_timers returns all timers."""
        await manager.create_timer("a")
        await manager.create_timer("b")
        result = await manager.list_timers()
        assert len(result) == 2


class TestTimerManagerEnableDisable:
    """TimerManager enable and disable with business rule guards."""

    async def test_enable(self, manager: TimerManager) -> None:
        """enable_timer enables a disabled timer and starts it."""
        await manager.create_timer("test")
        await manager.set_message("test", "hi")
        info = await manager.enable_timer("test")
        assert info.enabled is True
        assert info.id in manager._timers

    async def test_enable_already_enabled(self, manager: TimerManager) -> None:
        """enable_timer raises when the timer is already enabled."""
        await manager.create_timer("test")
        await manager.set_message("test", "hello")
        await manager.enable_timer("test")
        with pytest.raises(TimerAlreadyEnabledError):
            await manager.enable_timer("test")

    async def test_enable_no_message(self, manager: TimerManager) -> None:
        """enable_timer raises when the timer has no message set."""
        await manager.create_timer("test")
        with pytest.raises(TimerMessageRequiredError):
            await manager.enable_timer("test")

    async def test_disable(self, manager: TimerManager) -> None:
        """disable_timer disables an enabled timer and stops it."""
        await manager.create_timer("test")
        await manager.set_message("test", "hello")
        info = await manager.enable_timer("test")
        result = await manager.disable_timer("test")
        assert result.enabled is False
        assert info.id not in manager._timers

    async def test_disable_already_disabled(self, manager: TimerManager) -> None:
        """disable_timer raises when the timer is already disabled."""
        await manager.create_timer("test")
        with pytest.raises(TimerAlreadyDisabledError):
            await manager.disable_timer("test")


class TestTimerManagerMessageCounting:
    """TimerManager chat message counting delegation."""

    async def test_count_message(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """count_message forwards to all active timers with a threshold."""
        info_a = await repo.create("a")
        await repo.update_message(info_a.id, "m1")
        await repo.update_min_lines(info_a.id, 5)
        await repo.update_enabled(info_a.id, enabled=True)

        info_b = await repo.create("b")
        await repo.update_message(info_b.id, "m2")
        await repo.update_min_lines(info_b.id, 5)
        await repo.update_enabled(info_b.id, enabled=True)

        await manager.start_all()
        manager.count_message("msg-1")
        for timer in manager._timers.values():
            assert "msg-1" in timer._counted_messages

    async def test_count_message_skips_no_threshold(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """count_message skips timers with no minimum chat line requirement."""
        info = await repo.create("a")
        await repo.update_message(info.id, "m1")
        await repo.update_enabled(info.id, enabled=True)

        await manager.start_all()
        manager.count_message("msg-1")
        timer = manager._timers[info.id]
        assert len(timer._counted_messages) == 0

    async def test_remove_messages(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """remove_messages removes IDs from all active timers with a threshold."""
        info = await repo.create("a")
        await repo.update_message(info.id, "m1")
        await repo.update_min_lines(info.id, 5)
        await repo.update_enabled(info.id, enabled=True)

        await manager.start_all()
        manager.count_message("msg-1")
        manager.count_message("msg-2")
        manager.remove_messages({"msg-1"})
        timer = manager._timers[info.id]
        assert "msg-1" not in timer._counted_messages
        assert "msg-2" in timer._counted_messages


class TestTimerManagerOfflineStop:
    """TimerManager stream offline stop scheduling."""

    async def test_offline_stop_after_delay(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """Offline stop task stops all timers after the configured delay."""
        info = await repo.create("test")
        await repo.update_message(info.id, "hello")
        await repo.update_enabled(info.id, enabled=True)
        manager.OFFLINE_STOP_DELAY = 0.1
        await manager.start_all()
        assert len(manager._timers) == 1
        manager.schedule_offline_stop()
        await asyncio.sleep(0.2)
        assert len(manager._timers) == 0

    async def test_cancel_offline_stop(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """Cancelling offline stop keeps timers running."""
        info = await repo.create("test")
        await repo.update_message(info.id, "hello")
        await repo.update_enabled(info.id, enabled=True)
        manager.OFFLINE_STOP_DELAY = 0.2
        await manager.start_all()
        manager.schedule_offline_stop()
        await asyncio.sleep(0.05)
        manager.cancel_offline_stop()
        await asyncio.sleep(0.3)
        assert len(manager._timers) == 1

    async def test_schedule_replaces_previous(
        self, manager: TimerManager, repo: TimerRepository
    ) -> None:
        """A new schedule_offline_stop cancels the previous one."""
        info = await repo.create("test")
        await repo.update_message(info.id, "hello")
        await repo.update_enabled(info.id, enabled=True)
        manager.OFFLINE_STOP_DELAY = 0.1
        await manager.start_all()
        manager.schedule_offline_stop()
        manager.schedule_offline_stop()
        await asyncio.sleep(0.2)
        assert len(manager._timers) == 0


class TestGetManager:
    """get_manager() helper."""

    def test_returns_manager(self) -> None:
        """get_manager returns the TimerManager from ctx.state."""
        ctx = make_module_context()
        repo = TimerRepository(ctx.storage)
        mgr = TimerManager(ctx, repo)
        ctx.state["manager"] = mgr
        assert get_manager(ctx) is mgr

    def test_raises_if_not_initialized(self) -> None:
        """get_manager raises RuntimeError when manager is missing."""
        ctx = make_module_context()
        with pytest.raises(RuntimeError, match="TimerManager is not initialized"):
            get_manager(ctx)
