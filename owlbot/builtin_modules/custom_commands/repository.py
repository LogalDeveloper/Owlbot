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

"""Persistence layer for custom command, alias, and counter records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .types import (
    AliasAlreadyExistsError,
    Command,
    CommandNotFoundError,
)

if TYPE_CHECKING:
    import aiosqlite

    from owlbot.api.storage import ModuleStorage


def _command_from_row(row: aiosqlite.Row) -> Command:
    """Build a Command snapshot from a row with alias_list."""
    alias_str: str | None = row["alias_list"]
    aliases = frozenset(alias_str.split(",")) if alias_str else frozenset()
    return Command.from_row(row, aliases)


class CommandRepository:
    """Handles all database operations for custom commands."""

    def __init__(self, storage: ModuleStorage) -> None:
        """Initialize with a module storage instance.

        :param storage: The module's storage backend.
        """
        self._storage = storage

    async def setup(self) -> None:
        """Create tables if they do not exist."""
        await self._storage.execute("""
            CREATE TABLE IF NOT EXISTS commands (
                name TEXT PRIMARY KEY NOT NULL,
                response TEXT NOT NULL,
                use_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                requires_moderator INTEGER DEFAULT 0,
                cooldown INTEGER DEFAULT 0
            )
        """)
        await self._storage.execute("""
            CREATE TABLE IF NOT EXISTS command_aliases (
                alias TEXT PRIMARY KEY NOT NULL,
                command_name TEXT NOT NULL,
                FOREIGN KEY (command_name)
                    REFERENCES commands(name) ON DELETE CASCADE
            )
        """)
        await self._storage.execute("""
            CREATE TABLE IF NOT EXISTS counters (
                name TEXT PRIMARY KEY NOT NULL,
                value INTEGER DEFAULT 0
            )
        """)

    async def create(self, name: str, response: str, cooldown: int) -> Command:
        """Insert a new command and return its snapshot.

        :param name: Command name.
        :param response: Response template.
        :param cooldown: Cooldown in seconds.
        :return: Snapshot of the newly created command.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "INSERT INTO commands "
            "(name, response, use_count, created_at, updated_at, "
            "requires_moderator, cooldown) "
            "VALUES (?, ?, 0, ?, ?, 0, ?) RETURNING *",
            (name, response, now, now, cooldown),
        )
        if row is None:
            raise RuntimeError("INSERT RETURNING did not produce a row")
        return Command.from_row(row)

    async def get(self, name: str) -> Command:
        """Fetch a command by name or alias.

        Resolves name-or-alias and fetches all columns plus aggregated
        aliases in a single query.

        :param name: Command name or alias.
        :return: The matching Command (resolved to canonical name).
        :raises CommandNotFoundError: If no command matches.
        """
        row = await self._storage.fetch_one(
            "SELECT c.*, GROUP_CONCAT(ca.alias) AS alias_list "
            "FROM commands c "
            "LEFT JOIN command_aliases ca ON c.name = ca.command_name "
            "WHERE c.name = ? OR c.name = ("
            "  SELECT command_name FROM command_aliases WHERE alias = ? LIMIT 1"
            ") "
            "GROUP BY c.name LIMIT 1",
            (name, name),
        )
        if row is None:
            raise CommandNotFoundError(name)
        alias_str: str | None = row["alias_list"]
        aliases = frozenset(alias_str.split(",")) if alias_str else frozenset()
        return Command.from_row(row, aliases)

    async def delete(self, name: str) -> Command:
        """Delete a command and return its snapshot.

        :param name: Canonical command name.
        :return: Snapshot of the deleted command (with empty aliases).
        :raises CommandNotFoundError: If no command matches.
        """
        row = await self._storage.fetch_one(
            "DELETE FROM commands WHERE name = ? RETURNING *", (name,)
        )
        if row is None:
            raise CommandNotFoundError(name)
        return Command.from_row(row)

    async def update_response(self, name: str, response: str) -> Command:
        """Update a command's response template.

        :param name: Canonical command name.
        :param response: New response template.
        :return: Updated command snapshot.
        :raises CommandNotFoundError: If no command matches.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE commands SET response = ?, updated_at = ? "
            "WHERE name = ? "
            "RETURNING *, ("
            "  SELECT GROUP_CONCAT(alias) FROM command_aliases"
            "  WHERE command_name = commands.name"
            ") AS alias_list",
            (response, now, name),
        )
        if row is None:
            raise CommandNotFoundError(name)
        return _command_from_row(row)

    async def update_moderator_flag(self, name: str, *, enabled: bool) -> Command:
        """Update a command's moderator-only access flag.

        :param name: Canonical command name.
        :param enabled: True for moderator-only, False for public.
        :return: Updated command snapshot.
        :raises CommandNotFoundError: If no command matches.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE commands SET requires_moderator = ?, updated_at = ? "
            "WHERE name = ? "
            "RETURNING *, ("
            "  SELECT GROUP_CONCAT(alias) FROM command_aliases"
            "  WHERE command_name = commands.name"
            ") AS alias_list",
            (int(enabled), now, name),
        )
        if row is None:
            raise CommandNotFoundError(name)
        return _command_from_row(row)

    async def update_cooldown(self, name: str, seconds: int) -> Command:
        """Update a command's cooldown duration.

        :param name: Canonical command name.
        :param seconds: Cooldown in seconds.
        :return: Updated command snapshot.
        :raises CommandNotFoundError: If no command matches.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE commands SET cooldown = ?, updated_at = ? "
            "WHERE name = ? "
            "RETURNING *, ("
            "  SELECT GROUP_CONCAT(alias) FROM command_aliases"
            "  WHERE command_name = commands.name"
            ") AS alias_list",
            (seconds, now, name),
        )
        if row is None:
            raise CommandNotFoundError(name)
        return _command_from_row(row)

    async def reset_use_count(self, name: str) -> Command:
        """Reset a command's use count to zero.

        :param name: Canonical command name.
        :return: Updated command snapshot.
        :raises CommandNotFoundError: If no command matches.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "UPDATE commands SET use_count = 0, updated_at = ? "
            "WHERE name = ? "
            "RETURNING *, ("
            "  SELECT GROUP_CONCAT(alias) FROM command_aliases"
            "  WHERE command_name = commands.name"
            ") AS alias_list",
            (now, name),
        )
        if row is None:
            raise CommandNotFoundError(name)
        return _command_from_row(row)

    async def increment_use_count(self, name: str) -> Command:
        """Increment a command's use count by one.

        :param name: Canonical command name.
        :return: Updated command snapshot with new use_count.
        :raises CommandNotFoundError: If no command matches.
        """
        row = await self._storage.fetch_one(
            "UPDATE commands SET use_count = use_count + 1 "
            "WHERE name = ? "
            "RETURNING *, ("
            "  SELECT GROUP_CONCAT(alias) FROM command_aliases"
            "  WHERE command_name = commands.name"
            ") AS alias_list",
            (name,),
        )
        if row is None:
            raise CommandNotFoundError(name)
        return _command_from_row(row)

    async def list_all(self) -> list[Command]:
        """Return all commands ordered by name, with aliases populated.

        :return: List of Command snapshots.
        """
        rows = await self._storage.fetch_all(
            "SELECT c.*, GROUP_CONCAT(ca.alias) AS alias_list "
            "FROM commands c "
            "LEFT JOIN command_aliases ca ON c.name = ca.command_name "
            "GROUP BY c.name ORDER BY c.name"
        )
        result: list[Command] = []
        for row in rows:
            alias_str: str | None = row["alias_list"]
            aliases = frozenset(alias_str.split(",")) if alias_str else frozenset()
            result.append(Command.from_row(row, aliases))
        return result

    async def add_alias(self, command_name: str, alias: str) -> Command:
        """Add an alias to a command.

        :param command_name: Canonical command name.
        :param alias: Alias to add.
        :return: Refreshed command snapshot with updated aliases.
        :raises AliasAlreadyExistsError: If alias is already taken.
        """
        existing = await self._storage.fetch_one(
            "SELECT alias, command_name FROM command_aliases WHERE alias = ?",
            (alias,),
        )
        if existing:
            raise AliasAlreadyExistsError(alias, existing["command_name"])
        await self._storage.execute(
            "INSERT INTO command_aliases (alias, command_name) VALUES (?, ?)",
            (alias, command_name),
        )
        return await self.get(command_name)

    async def remove_alias(self, alias: str) -> tuple[str, Command]:
        """Remove an alias and return its owner.

        :param alias: Alias to remove.
        :return: Tuple of (removed alias, refreshed owner command).
        :raises CommandNotFoundError: If alias does not exist.
        """
        row = await self._storage.fetch_one(
            "DELETE FROM command_aliases WHERE alias = ? RETURNING command_name",
            (alias,),
        )
        if row is None:
            raise CommandNotFoundError(alias)
        cmd = await self.get(row["command_name"])
        return alias, cmd

    async def get_counter(self, name: str) -> int:
        """Get a counter's value, defaulting to 0 if it does not exist.

        :param name: Counter name.
        :return: Current value.
        """
        row = await self._storage.fetch_one(
            "SELECT value FROM counters WHERE name = ?", (name,)
        )
        return row["value"] if row else 0

    async def set_counter(self, name: str, value: int) -> int:
        """Set a counter to an absolute value (upsert).

        :param name: Counter name.
        :param value: Absolute value to set.
        :return: The new value.
        """
        result = await self._storage.fetch_value(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = ? RETURNING value",
            (name, value, value),
        )
        if result is None:
            raise RuntimeError("UPSERT RETURNING did not produce a value")
        return int(result)

    async def update_counter(self, name: str, delta: int) -> int:
        """Adjust a counter by a relative delta (upsert).

        :param name: Counter name.
        :param delta: Amount to add (can be negative).
        :return: The new value.
        """
        result = await self._storage.fetch_value(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + ? "
            "RETURNING value",
            (name, delta, delta),
        )
        if result is None:
            raise RuntimeError("UPSERT RETURNING did not produce a value")
        return int(result)
