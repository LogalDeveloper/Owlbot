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

"""Data containers and domain errors for the custom_commands module."""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import aiosqlite

NAME_RE = re.compile(r"^[a-z0-9_]+$")


@dataclasses.dataclass(frozen=True, slots=True)
class Command:
    """Immutable snapshot of a custom command record."""

    name: str
    response: str
    use_count: int
    created_at: str
    updated_at: str
    requires_moderator: bool
    cooldown: int
    aliases: frozenset[str] = dataclasses.field(default_factory=frozenset)

    @classmethod
    def from_row(
        cls,
        row: aiosqlite.Row,
        aliases: frozenset[str] = frozenset(),
    ) -> Command:
        """Build a Command from a database row.

        :param row: A row from the commands table.
        :param aliases: Aliases for this command (populated via JOIN).
        :return: A Command snapshot.
        """
        return cls(
            name=row["name"],
            response=row["response"],
            use_count=row["use_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            requires_moderator=bool(row["requires_moderator"]),
            cooldown=row["cooldown"],
            aliases=aliases,
        )


class CounterAccessor(Protocol):
    """Interface for counter read/write operations.

    Used by the placeholder system to access counters without coupling
    to the repository directly.
    """

    async def get_counter(self, name: str) -> int:
        """Get a counter's value (0 if not found)."""
        ...

    async def set_counter(self, name: str, value: int) -> int:
        """Set a counter to an absolute value.

        :param name: Counter name.
        :param value: Absolute value to set.
        """
        ...

    async def adjust_counter(self, name: str, delta: int) -> int:
        """Adjust a counter by a relative delta.

        :param name: Counter name.
        :param delta: Amount to add (can be negative).
        """
        ...


class CommandError(Exception):
    """Base class for custom command domain errors."""


class CommandNotFoundError(CommandError):
    """No command matches the given name or alias."""

    def __init__(self, identifier: str) -> None:
        """Initialize with the identifier that was not found.

        :param identifier: The name or alias that was not found.
        """
        self.identifier = identifier
        super().__init__(f"command not found: {identifier}")


class InvalidNameError(CommandError):
    """Name does not match the allowed pattern."""

    def __init__(self, name: str) -> None:
        """Initialize with the invalid name.

        :param name: The name that failed validation.
        """
        self.name = name
        super().__init__(f"invalid name: {name}")


class CommandAlreadyExistsError(CommandError):
    """A command with this name or trigger already exists."""

    def __init__(self, name: str) -> None:
        """Initialize with the conflicting name.

        :param name: The name that already exists.
        """
        self.name = name
        super().__init__(f"command already exists: {name}")


class NotCustomCommandError(CommandError):
    """The command exists but is not a custom command."""

    def __init__(self, name: str) -> None:
        """Initialize with the non-custom command name.

        :param name: The name of the built-in command.
        """
        self.name = name
        super().__init__(f"not a custom command: {name}")


class AliasAlreadyExistsError(CommandError):
    """The alias is already assigned to a command."""

    def __init__(self, alias: str, owner: str) -> None:
        """Initialize with the conflicting alias and its owner.

        :param alias: The alias that already exists.
        :param owner: The command that owns the alias.
        """
        self.alias = alias
        self.owner = owner
        super().__init__(f"alias already exists: {alias} (owned by {owner})")


class AliasIsCanonicalNameError(CommandError):
    """Cannot alias a command to its own canonical name."""

    def __init__(self, alias: str) -> None:
        """Initialize with the alias that matches the canonical name.

        :param alias: The alias that is the same as the command name.
        """
        self.alias = alias
        super().__init__(f"alias is the canonical name: {alias}")


class CannotDeleteByAliasError(CommandError):
    """Cannot delete a command via its alias name."""

    def __init__(self, alias: str, canonical: str) -> None:
        """Initialize with the alias and its canonical command name.

        :param alias: The alias that was targeted for deletion.
        :param canonical: The canonical command name that owns the alias.
        """
        self.alias = alias
        self.canonical = canonical
        super().__init__(f"cannot delete alias {alias}, belongs to {canonical}")


class InactiveCommandError(CommandError):
    """The command exists in the database but is not registered."""

    def __init__(self, name: str) -> None:
        """Initialize with the inactive command name.

        :param name: The name of the inactive command.
        """
        self.name = name
        super().__init__(f"command is inactive: {name}")
