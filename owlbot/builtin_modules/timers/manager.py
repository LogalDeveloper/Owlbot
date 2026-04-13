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

"""Timer task management and stream-aware lifecycle for the timers module.

Provides the Timer class (per-timer async fire loop) and TimerManager (collection
management, business rules, and stream-aware lifecycle). All persistence is
delegated to TimerRepository.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from cronsim import CronSim, CronSimError

from .types import (
    IntervalType,
    NegativeLineCountError,
    TimerAlreadyDisabledError,
    TimerAlreadyEnabledError,
    TimerMessageRequiredError,
    duration_to_seconds,
)

if TYPE_CHECKING:
    from owlbot.api import ModuleContext

    from .repository import TimerRepository
    from .types import TimerInfo


class Timer:
    """Encapsulates a single timer's async task, chat counter, and fire loop."""

    def __init__(
        self, info: TimerInfo, ctx: ModuleContext, repo: TimerRepository
    ) -> None:
        """Initialize a timer with no running task.

        :param info: Snapshot of the timer from the database.
        :param ctx: The module context.
        :param repo: The timer repository for persistence.
        :raises TimerMessageRequiredError: If the timer has no message set.
        :raises ValueError: If the interval is invalid.
        """
        if info.message is None:
            raise TimerMessageRequiredError(info)

        if info.interval_type == IntervalType.SIMPLE:
            if duration_to_seconds(info.interval_value) <= 0:
                raise ValueError(
                    f"Timer {info.display} has an invalid simple interval."
                )
        else:
            try:
                next(CronSim(info.interval_value, datetime.now(UTC)))
            except (ValueError, KeyError, CronSimError):
                raise ValueError(
                    f"Timer {info.display} has an invalid cron expression."
                ) from None

        self._info = info
        self._message: str = info.message
        self._ctx = ctx
        self._repo = repo
        self._task: asyncio.Task[None] | None = None
        self._counted_messages: set[str] = set()

    @property
    def running(self) -> bool:
        """Whether the fire loop task is currently running."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Spawn the fire loop as a background task.

        No-op if already running.
        """
        if self.running:
            return
        self._task = asyncio.create_task(
            self._fire_loop(), name=f"Timers Module - Timer #{self._info.id} loop"
        )

    async def stop(self) -> None:
        """Cancel the fire loop task and clear the chat counter."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._counted_messages.clear()

    def count_message(self, message_id: str) -> None:
        """Count a chat message toward this timer's threshold.

        No-op if the timer has no minimum chat line requirement.

        :param message_id: The message ID to count.
        """
        if self._info.min_chat_lines > 0:
            self._counted_messages.add(message_id)

    def remove_messages(self, message_ids: set[str]) -> None:
        """Remove message IDs from this timer's counter.

        No-op if the timer has no minimum chat line requirement.

        :param message_ids: Set of message IDs to remove.
        """
        if self._info.min_chat_lines > 0:
            self._counted_messages.difference_update(message_ids)

    async def _fire_loop(self) -> None:
        """Background loop: sleep until due, check threshold, fire or skip.

        All timer configuration is read from the TimerInfo snapshot set at
        construction. The only database interaction is persisting last_fired_at
        so the schedule survives restarts. Config changes are handled by
        TimerManager restarting the Timer with a fresh snapshot.
        """
        ctx = self._ctx
        info = self._info
        display = info.display
        message = self._message
        last_fired_at: datetime | None = (
            datetime.fromisoformat(info.last_fired_at) if info.last_fired_at else None
        )

        if info.interval_type == IntervalType.SIMPLE:
            interval_secs = duration_to_seconds(info.interval_value)
            cron_iter: CronSim | None = None
        else:
            interval_secs = 0
            cron_iter = CronSim(info.interval_value, last_fired_at or datetime.now(UTC))

        ctx.logger.debug("Timer %s fire loop started.", display)
        while True:
            now = datetime.now(UTC)

            # Compute next fire time from the interval.
            if last_fired_at is None:
                fire_time = now
            elif cron_iter is not None:
                fire_time = next(cron_iter)
            else:
                fire_time = last_fired_at + timedelta(seconds=interval_secs)

            delay = max(0.0, (fire_time - now).total_seconds())

            if delay > 0:
                ctx.logger.debug("Timer %s sleeping for %.1fs.", display, delay)
                await asyncio.sleep(delay)

            now = datetime.now(UTC)

            # Check min_chat_lines threshold.
            if info.min_chat_lines > 0:
                counted = len(self._counted_messages)
                if counted < info.min_chat_lines:
                    last_fired_at = now
                    await self._repo.update_last_fired_at(info.id, now.isoformat())
                    ctx.logger.info(
                        "Timer %s skipped: chat threshold not met (%d/%d). "
                        "Schedule advanced.",
                        display,
                        counted,
                        info.min_chat_lines,
                    )
                    continue

            # Fire the timer.
            try:
                ctx.logger.debug("Firing timer %s.", display)
                await ctx.owncast_client.send_message(message)
                self._counted_messages.clear()
                ctx.logger.info("Timer %s fired.", display)
            except Exception:
                ctx.logger.exception("Failed to fire timer %s.", display)

            # Always advance the schedule so a persistent error doesn't
            # cause a tight retry loop.
            last_fired_at = now
            await self._repo.update_last_fired_at(info.id, now.isoformat())


class TimerManager:
    """Manages the collection of Timer instances and stream-aware lifecycle.

    Business rules (enable/disable guards, message requirements) live here.
    All persistence is delegated to the TimerRepository.
    """

    OFFLINE_STOP_DELAY: float = 300.0

    def __init__(self, ctx: ModuleContext, repo: TimerRepository) -> None:
        """Initialize an empty manager.

        :param ctx: The module context.
        :param repo: The timer repository for persistence.
        """
        self._ctx = ctx
        self._repo = repo
        self._timers: dict[int, Timer] = {}
        self._offline_stop_task: asyncio.Task[None] | None = None

    def start_timer(self, info: TimerInfo) -> None:
        """Create a Timer and start its fire loop.

        No-op if the timer already has a running task.

        :param info: Snapshot of the timer.
        """
        if info.id in self._timers:
            return
        timer = Timer(info, self._ctx, self._repo)
        timer.start()
        self._timers[info.id] = timer
        self._ctx.logger.debug("Started timer task for %s.", info.display)

    async def stop_timer(self, timer_id: int) -> None:
        """Cancel a timer's task and remove it from the collection.

        No-op if the timer has no running task.

        :param timer_id: Database ID of the timer.
        """
        timer = self._timers.pop(timer_id, None)
        if timer is not None:
            await timer.stop()
            self._ctx.logger.debug("Stopped timer task for #%d.", timer_id)

    async def restart_timer(self, info: TimerInfo) -> None:
        """Stop and re-start a timer with fresh state.

        No-op if the timer has no running task.

        :param info: Snapshot of the timer.
        """
        if info.id not in self._timers:
            return
        await self.stop_timer(info.id)
        self.start_timer(info)

    async def start_all(self) -> None:
        """Start tasks for all enabled timers that have a message set.

        Timers that already have a running task are skipped.
        """
        infos = await self._repo.list_startable()
        for info in infos:
            self.start_timer(info)
        self._ctx.logger.info("Started %d timer task(s).", len(infos))

    async def stop_all(self) -> None:
        """Cancel all running timer tasks."""
        timers = list(self._timers.values())
        self._timers.clear()
        await asyncio.gather(*(t.stop() for t in timers))

    async def create_timer(self, name: str | None) -> TimerInfo:
        """Insert a new disabled timer and return its info.

        :param name: Optional timer name, or None for unnamed.
        :return: Snapshot of the newly created timer.
        :raises InvalidTimerNameError: If the name format is invalid.
        :raises TimerNameTakenError: If the name is already in use.
        """
        info = await self._repo.create(name)
        self._ctx.logger.debug("Created timer #%d.", info.id)
        return info

    async def delete_timer(self, identifier: str) -> TimerInfo:
        """Delete a timer from the database and stop it if running.

        :param identifier: Timer ID or name string.
        :return: Snapshot of the deleted timer.
        :raises TimerNotFoundError: If no timer matches the identifier.
        """
        info = await self._repo.get(identifier)
        await self.stop_timer(info.id)
        await self._repo.delete(info.id)
        self._ctx.logger.debug("Deleted timer #%d.", info.id)
        return info

    async def set_message(self, identifier: str, message: str) -> TimerInfo:
        """Update a timer's message and restart it if running.

        :param identifier: Timer ID or name string.
        :param message: The new message text.
        :return: Snapshot of the updated timer.
        :raises TimerNotFoundError: If no timer matches the identifier.
        """
        info = await self._repo.get(identifier)
        updated = await self._repo.update_message(info.id, message.strip())
        self._ctx.logger.debug("Updated message for timer #%d.", info.id)
        await self.restart_timer(updated)
        return updated

    async def set_interval(
        self, identifier: str, interval_type: IntervalType, interval_value: str
    ) -> TimerInfo:
        """Update a timer's interval and restart it if running.

        :param identifier: Timer ID or name string.
        :param interval_type: The interval type.
        :param interval_value: The interval value string.
        :return: Snapshot of the updated timer.
        :raises TimerNotFoundError: If no timer matches the identifier.
        """
        info = await self._repo.get(identifier)
        updated = await self._repo.update_interval(
            info.id, interval_type, interval_value
        )
        self._ctx.logger.debug(
            "Updated interval for timer #%d to %s (%s).",
            info.id,
            interval_value,
            interval_type,
        )
        await self.restart_timer(updated)
        return updated

    async def set_min_chat_lines(self, identifier: str, count: int) -> TimerInfo:
        """Update a timer's minimum chat line threshold and restart if running.

        :param identifier: Timer ID or name string.
        :param count: Minimum number of chat lines between firings.
        :return: Snapshot of the updated timer.
        :raises NegativeLineCountError: If the count is negative.
        :raises TimerNotFoundError: If no timer matches the identifier.
        """
        if count < 0:
            raise NegativeLineCountError

        info = await self._repo.get(identifier)
        updated = await self._repo.update_min_lines(info.id, count)
        self._ctx.logger.debug(
            "Updated min chat lines for timer #%d to %d.", info.id, count
        )
        await self.restart_timer(updated)
        return updated

    async def enable_timer(self, identifier: str) -> TimerInfo:
        """Enable a timer in the database and start it.

        :param identifier: Timer ID or name string.
        :return: Snapshot of the enabled timer.
        :raises TimerNotFoundError: If no timer matches the identifier.
        :raises TimerAlreadyEnabledError: If the timer is already enabled.
        :raises TimerMessageRequiredError: If the timer has no message set.
        """
        info = await self._repo.get(identifier)

        if info.enabled:
            raise TimerAlreadyEnabledError(info)

        if not info.message:
            raise TimerMessageRequiredError(info)

        enabled_info = await self._repo.update_enabled(info.id, enabled=True)
        self._ctx.logger.debug("Enabled timer #%d.", info.id)
        self.start_timer(enabled_info)
        return enabled_info

    async def disable_timer(self, identifier: str) -> TimerInfo:
        """Disable a timer in the database and stop it.

        :param identifier: Timer ID or name string.
        :return: Snapshot of the disabled timer.
        :raises TimerNotFoundError: If no timer matches the identifier.
        :raises TimerAlreadyDisabledError: If the timer is already disabled.
        """
        info = await self._repo.get(identifier)

        if not info.enabled:
            raise TimerAlreadyDisabledError(info)

        await self.stop_timer(info.id)
        disabled_info = await self._repo.update_enabled(info.id, enabled=False)
        self._ctx.logger.debug("Disabled timer #%d.", info.id)
        return disabled_info

    async def list_timers(self) -> list[TimerInfo]:
        """Return all timers ordered by ID.

        :return: List of TimerInfo snapshots.
        """
        return await self._repo.list_all()

    def count_message(self, message_id: str) -> None:
        """Count a chat message toward all active timers' thresholds.

        :param message_id: The message ID to count.
        """
        for timer in self._timers.values():
            timer.count_message(message_id)

    def remove_messages(self, message_ids: set[str]) -> None:
        """Remove message IDs from all active timers' counters.

        Used when messages are hidden by moderation.

        :param message_ids: Set of message IDs to remove.
        """
        for timer in self._timers.values():
            timer.remove_messages(message_ids)

    def schedule_offline_stop(self) -> None:
        """Schedule all timers to stop after the offline grace period.

        Cancels any previously scheduled offline stop first.
        """
        self.cancel_offline_stop()
        self._offline_stop_task = asyncio.create_task(
            self._delayed_offline_stop(),
            name="Timers Module - Offline grace period timer",
        )

    def cancel_offline_stop(self) -> None:
        """Cancel a pending offline stop if one is scheduled."""
        if self._offline_stop_task is not None:
            self._offline_stop_task.cancel()
            self._offline_stop_task = None

    async def _delayed_offline_stop(self) -> None:
        """Wait for OFFLINE_STOP_DELAY seconds, then stop all timers."""
        await asyncio.sleep(self.OFFLINE_STOP_DELAY)
        await self.stop_all()
        self._offline_stop_task = None
        self._ctx.logger.info("Timers stopped after stream offline.")


def get_manager(ctx: ModuleContext) -> TimerManager:
    """Return the TimerManager stored in the module context's state.

    :param ctx: The module context.
    :return: The active TimerManager.
    :raises RuntimeError: If the manager has not been initialized.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, TimerManager):
        raise RuntimeError("TimerManager is not initialized.")  # noqa: TRY004  # state error, not a type error
    return manager
