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

"""Persistence layer for timer records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .types import (
    NAME_PATTERN,
    IntervalType,
    InvalidTimerNameError,
    TimerInfo,
    TimerNameTakenError,
    TimerNotFoundError,
)

if TYPE_CHECKING:
    from owlbot.api.storage import ModuleStorage


class TimerRepository:
    """Handles all database operations for timer records."""

    def __init__(self, storage: ModuleStorage) -> None:
        """Initialize with a module storage instance.

        :param storage: The module's storage backend.
        """
        self._storage = storage

    async def setup(self) -> None:
        """Create the timers table if it does not exist."""
        await self._storage.execute("""
            CREATE TABLE IF NOT EXISTS timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                message TEXT,
                interval_value TEXT NOT NULL DEFAULT '15m',
                interval_type TEXT NOT NULL DEFAULT 'simple',
                min_chat_lines INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_fired_at TEXT
            )
        """)

    async def get(self, identifier: str) -> TimerInfo:
        """Resolve a timer by numeric ID or name.

        Tries parsing as an integer first, then falls back to a name lookup.

        :param identifier: A timer ID or name string.
        :return: The resolved TimerInfo.
        :raises TimerNotFoundError: If no timer matches the identifier.
        """
        try:
            timer_id = int(identifier)
            row = await self._storage.fetch_one(
                "SELECT * FROM timers WHERE id = ?", (timer_id,)
            )
            if row:
                return TimerInfo.from_row(row)
        except ValueError:
            pass

        row = await self._storage.fetch_one(
            "SELECT * FROM timers WHERE name = ?", (identifier.lower(),)
        )
        if row is None:
            raise TimerNotFoundError(identifier)
        return TimerInfo.from_row(row)

    async def create(self, name: str | None) -> TimerInfo:
        """Insert a new disabled timer and return its info.

        :param name: Optional timer name, or None for unnamed.
        :return: Snapshot of the newly created timer.
        :raises InvalidTimerNameError: If the name format is invalid.
        :raises TimerNameTakenError: If the name is already in use.
        """
        if name is not None:
            if not NAME_PATTERN.match(name):
                raise InvalidTimerNameError(name)
            existing = await self._storage.fetch_one(
                "SELECT id FROM timers WHERE name = ?", (name,)
            )
            if existing:
                raise TimerNameTakenError(name, existing["id"])

        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "INSERT INTO timers (name, message, interval_value, interval_type, "
            "min_chat_lines, enabled, created_at, updated_at) "
            "VALUES (?, NULL, '15m', 'simple', 0, 0, ?, ?) RETURNING *",
            (name, now, now),
        )
        if row is None:
            raise RuntimeError("INSERT RETURNING did not produce a row")
        return TimerInfo.from_row(row)

    async def delete(self, timer_id: int) -> None:
        """Delete a timer record from the database.

        :param timer_id: Database ID of the timer.
        """
        await self._storage.execute("DELETE FROM timers WHERE id = ?", (timer_id,))

    async def update_message(self, timer_id: int, message: str) -> TimerInfo:
        """Update a timer's message text.

        :param timer_id: Database ID of the timer.
        :param message: The new message text.
        :return: Snapshot of the updated timer.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE timers SET message = ?, updated_at = ? WHERE id = ? RETURNING *",
            (message, now, timer_id),
        )
        if row is None:
            raise TimerNotFoundError(timer_id)
        return TimerInfo.from_row(row)

    async def update_interval(
        self, timer_id: int, interval_type: IntervalType, interval_value: str
    ) -> TimerInfo:
        """Update a timer's interval type and value.

        :param timer_id: Database ID of the timer.
        :param interval_type: The interval type.
        :param interval_value: The interval value string.
        :return: Snapshot of the updated timer.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE timers SET interval_type = ?, interval_value = ?, "
            "updated_at = ? WHERE id = ? RETURNING *",
            (interval_type, interval_value, now, timer_id),
        )
        if row is None:
            raise TimerNotFoundError(timer_id)
        return TimerInfo.from_row(row)

    async def update_min_lines(self, timer_id: int, count: int) -> TimerInfo:
        """Update a timer's minimum chat line threshold.

        :param timer_id: Database ID of the timer.
        :param count: Minimum number of chat lines between firings.
        :return: Snapshot of the updated timer.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE timers SET min_chat_lines = ?, updated_at = ? "
            "WHERE id = ? RETURNING *",
            (count, now, timer_id),
        )
        if row is None:
            raise TimerNotFoundError(timer_id)
        return TimerInfo.from_row(row)

    async def update_enabled(self, timer_id: int, *, enabled: bool) -> TimerInfo:
        """Update a timer's enabled state.

        :param timer_id: Database ID of the timer.
        :param enabled: Whether the timer should be enabled.
        :return: Snapshot of the updated timer.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE timers SET enabled = ?, updated_at = ? WHERE id = ? RETURNING *",
            (int(enabled), now, timer_id),
        )
        if row is None:
            raise TimerNotFoundError(timer_id)
        return TimerInfo.from_row(row)

    async def update_last_fired_at(self, timer_id: int, timestamp: str) -> None:
        """Persist the last fired timestamp for a timer.

        :param timer_id: Database ID of the timer.
        :param timestamp: ISO-format timestamp string.
        """
        await self._storage.execute(
            "UPDATE timers SET last_fired_at = ? WHERE id = ?",
            (timestamp, timer_id),
        )

    async def list_all(self) -> list[TimerInfo]:
        """Return all timers ordered by ID.

        :return: List of TimerInfo snapshots.
        """
        rows = await self._storage.fetch_all("SELECT * FROM timers ORDER BY id")
        return [TimerInfo.from_row(row) for row in rows]

    async def list_startable(self) -> list[TimerInfo]:
        """Return all enabled timers that have a message set.

        :return: List of TimerInfo snapshots for startable timers.
        """
        rows = await self._storage.fetch_all(
            "SELECT * FROM timers WHERE enabled = 1 AND message IS NOT NULL"
        )
        return [TimerInfo.from_row(row) for row in rows]
