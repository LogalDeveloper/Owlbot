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

"""Background scheduler and interval parsing for the timers module.

Manages the scheduler task that sleeps until the next timer is due and fires
messages at the right moment. Also provides interval parsing utilities and
shared state for chat line counting.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from cronsim import CronSim, CronSimError

if TYPE_CHECKING:
    import aiosqlite

    from owlbot.api import ModuleContext


class IntervalType(StrEnum):
    """Supported interval types for timer scheduling."""

    SIMPLE = "simple"
    CRON = "cron"


# Regex for simple duration strings like "30s", "5m", "1h", "2h30m".
_DURATION_PATTERN = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE)

_MIN_SIMPLE_SECONDS = 30
_MIN_CRON_MINUTES = 1


def parse_interval(value: str) -> tuple[IntervalType, str]:
    """Parse and validate an interval string.

    Simple durations (no spaces) are parsed with a regex. Cron expressions
    (contain spaces) are validated with cronsim. Returns the detected type
    and the normalized value.

    :param value: Raw interval string from the user.
    :return: Tuple of (interval_type, normalized_value).
    :raises ValueError: If the value is not a valid interval.
    """
    value = value.strip()
    if not value:
        raise ValueError("Interval cannot be empty.")

    if " " in value:
        return _parse_cron(value)
    return _parse_simple(value)


def _parse_simple(value: str) -> tuple[IntervalType, str]:
    """Parse and validate a simple duration string.

    :param value: Duration string like "30s", "5m", "1h30m".
    :return: Tuple of (IntervalType.SIMPLE, value).
    :raises ValueError: If the format is invalid or below minimum.
    """
    match = _DURATION_PATTERN.match(value)
    if not match or not any(match.groups()):
        raise ValueError(
            f"Invalid duration: {value}. Use a format like 30s, 5m, 1h, or 2h30m."
        )

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds

    if total < _MIN_SIMPLE_SECONDS:
        raise ValueError(
            f"Minimum interval is {_MIN_SIMPLE_SECONDS} seconds. Got {total}s."
        )

    return (IntervalType.SIMPLE, value.lower())


def _parse_cron(value: str) -> tuple[IntervalType, str]:
    """Validate a cron expression.

    :param value: Cron expression string (5 fields).
    :return: Tuple of (IntervalType.CRON, value).
    :raises ValueError: If the expression is invalid or fires too frequently.
    """
    try:
        now = datetime.now(UTC)
        it = CronSim(value, now)
        first = next(it)
        second = next(it)
    except CronSimError as e:
        raise ValueError(f"Invalid cron expression: {value}") from e

    gap = (second - first).total_seconds()

    if gap < _MIN_CRON_MINUTES * 60:
        raise ValueError(
            f"Cron interval too frequent. Minimum gap is {_MIN_CRON_MINUTES} minute(s)."
        )

    return (IntervalType.CRON, value)


def _duration_to_seconds(value: str) -> int:
    """Convert a validated simple duration string to total seconds.

    :param value: A previously validated duration string.
    :return: Total seconds.
    """
    match = _DURATION_PATTERN.match(value)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _next_fire_time(row: aiosqlite.Row, now: datetime) -> datetime | None:
    """Compute when a timer will next be time-due.

    :param row: Database row with interval_type, interval_value, last_fired_at.
    :param now: Current UTC time.
    :return: The datetime when this timer is next due, or None if it can't fire.
    """
    last_fired = row["last_fired_at"]
    last_fired_dt = datetime.fromisoformat(last_fired) if last_fired else None

    # Never fired before: immediately due.
    if last_fired_dt is None:
        return now

    if row["interval_type"] == IntervalType.SIMPLE:
        interval_secs = _duration_to_seconds(row["interval_value"])
        if interval_secs <= 0:
            return None
        return last_fired_dt + timedelta(seconds=interval_secs)

    # Cron timer: wait for the next scheduled tick.
    try:
        return next(CronSim(row["interval_value"], last_fired_dt))
    except (ValueError, KeyError, CronSimError):
        return None


def _is_timer_due(row: aiosqlite.Row, now: datetime) -> bool:
    """Check whether a timer should fire based on its interval and last fire time.

    :param row: Database row with interval_type, interval_value, last_fired_at.
    :param now: Current UTC time.
    :return: True if the timer is due to fire.
    """
    fire_time = _next_fire_time(row, now)
    if fire_time is None:
        return False
    return fire_time <= now


class TimerScheduler:
    """Encapsulates the mutable runtime state for the timer scheduler.

    Holds the background task, wake event, and per-timer chat line counters.
    """

    def __init__(self) -> None:
        """Initialize an empty scheduler with no running task."""
        self._task: asyncio.Task[None] | None = None
        self._counted_ids: dict[int, set[str]] = {}
        self._wake_event: asyncio.Event | None = None

    @property
    def counted_ids(self) -> dict[int, set[str]]:
        """Per-timer sets of counted message IDs."""
        return self._counted_ids

    def init_counted_ids(self, timer_ids: list[int]) -> None:
        """Initialize empty counter sets for the given timer IDs.

        Called during module setup to prepare tracking for enabled timers.

        :param timer_ids: List of enabled timer IDs to track.
        """
        self._counted_ids = {tid: set() for tid in timer_ids}

    def start(self, ctx: ModuleContext) -> None:
        """Start the background scheduler task.

        :param ctx: The module context.
        """
        if self._task is not None:
            return
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._scheduler_loop(ctx, self._wake_event))
        ctx.logger.debug("Timer scheduler started.")

    async def stop(self, ctx: ModuleContext) -> None:
        """Cancel the background scheduler task and wait for it to exit.

        :param ctx: The module context.
        """
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            self._wake_event = None
            ctx.logger.info("Timer scheduler stopped.")

    def reschedule(self) -> None:
        """Wake the scheduler so it recalculates the next fire time.

        Called by timer management commands when timer state changes.
        """
        if self._wake_event is not None:
            self._wake_event.set()

    async def _scheduler_loop(self, ctx: ModuleContext, wake: asyncio.Event) -> None:
        """Background loop that sleeps until the next timer is due and fires it.

        Uses an asyncio.Event to allow early wake-ups when timer state changes.

        :param ctx: The module context.
        :param wake: Event used to signal early wake-ups.
        """
        ctx.logger.debug("Scheduler loop running with precise sleep.")
        while True:
            delay, display = await _compute_next_delay(ctx)
            wake.clear()
            try:
                if delay is not None:
                    ctx.logger.debug(
                        "Next timer due in %.1fs (timer %s). Sleeping until then.",
                        delay,
                        display,
                    )
                    await asyncio.wait_for(wake.wait(), timeout=delay)
                    ctx.logger.debug(
                        "Woken early by reschedule event. "
                        "Recalculating next fire time.",
                    )
                else:
                    ctx.logger.debug(
                        "No timers scheduled. Sleeping until woken by a timer change.",
                    )
                    await wake.wait()
                    ctx.logger.debug(
                        "Woken early by reschedule event. "
                        "Recalculating next fire time.",
                    )
            except TimeoutError:
                ctx.logger.debug("Sleep finished. Checking for due timers.")
            try:
                await self._tick(ctx)
            except Exception:
                ctx.logger.exception("Scheduler tick failed.")

    async def _tick(self, ctx: ModuleContext) -> None:
        """Single scheduler tick: query enabled timers and fire any that are due.

        :param ctx: The module context.
        """
        rows = await ctx.storage.fetch_all(
            "SELECT id, name, message, interval_type, interval_value, "
            "min_chat_lines, last_fired_at "
            "FROM timers WHERE enabled = 1 AND message IS NOT NULL"
        )

        now = datetime.now(UTC)
        ctx.logger.debug("Tick: %d enabled timer(s) to check.", len(rows))

        for row in rows:
            timer_id = row["id"]
            display = row["name"] or f"#{timer_id}"

            if not _is_timer_due(row, now):
                ctx.logger.debug("Timer %s is not due yet.", display)
                continue

            min_lines = row["min_chat_lines"]
            if min_lines > 0:
                counted = len(self._counted_ids.get(timer_id, set()))
                if counted < min_lines:
                    now_iso = now.isoformat()
                    await ctx.storage.execute(
                        "UPDATE timers SET last_fired_at = ? WHERE id = ?",
                        (now_iso, timer_id),
                    )
                    ctx.logger.info(
                        "Timer %s skipped: chat line threshold not met (%d/%d). "
                        "Schedule advanced.",
                        display,
                        counted,
                        min_lines,
                    )
                    continue

            # Fire the timer. Wrapped in try/except so a single failing timer
            # (e.g. network error) does not prevent other timers from firing.
            try:
                ctx.logger.debug("Firing timer %s.", display)
                await ctx.owncast_client.send_message(row["message"])

                now_iso = now.isoformat()
                await ctx.storage.execute(
                    "UPDATE timers SET last_fired_at = ? WHERE id = ?",
                    (now_iso, timer_id),
                )

                self._counted_ids[timer_id] = set()
                ctx.logger.info(f"Timer {display} fired.")
            except Exception:
                ctx.logger.exception("Failed to fire timer %s.", display)


async def _compute_next_delay(ctx: ModuleContext) -> tuple[float | None, str | None]:
    """Query enabled timers and return seconds until the soonest one is time-due.

    :param ctx: The module context.
    :return: Tuple of (seconds until next timer, display name of that timer),
             or (None, None) if no timers are scheduled.
    """
    rows = await ctx.storage.fetch_all(
        "SELECT id, name, interval_type, interval_value, last_fired_at "
        "FROM timers WHERE enabled = 1 AND message IS NOT NULL"
    )

    now = datetime.now(UTC)
    soonest_delay: float | None = None
    soonest_display: str | None = None

    for row in rows:
        timer_id = row["id"]
        display = row["name"] or f"#{timer_id}"

        fire_time = _next_fire_time(row, now)
        if fire_time is None:
            continue

        delay = (fire_time - now).total_seconds()
        ctx.logger.debug(
            "Timer %s next due at %s (in %.1fs).",
            display,
            fire_time,
            delay,
        )

        if soonest_delay is None or delay < soonest_delay:
            soonest_delay = delay
            soonest_display = display

    if soonest_delay is not None:
        soonest_delay = max(0.0, soonest_delay)
        ctx.logger.debug(
            "Soonest timer is %s in %.1fs.",
            soonest_display,
            soonest_delay,
        )

    return soonest_delay, soonest_display


def get_scheduler(ctx: ModuleContext) -> TimerScheduler:
    """Return the scheduler stored in the module context's state.

    :param ctx: The module context.
    :return: The active TimerScheduler.
    :raises RuntimeError: If the scheduler has not been initialized.
    """
    scheduler = ctx.state.get("scheduler")
    if not isinstance(scheduler, TimerScheduler):
        raise RuntimeError("TimerScheduler is not initialized.")
    return scheduler
