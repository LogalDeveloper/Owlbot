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

"""Data containers, constants, and pure helper functions for the timers module."""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from cronsim import CronSim, CronSimError

if TYPE_CHECKING:
    import aiosqlite


class IntervalType(StrEnum):
    """Supported interval types for timer scheduling."""

    SIMPLE = "simple"
    CRON = "cron"


@dataclasses.dataclass(frozen=True, slots=True)
class TimerInfo:
    """Complete snapshot of a timer, returned by manager operations."""

    id: int
    name: str | None
    message: str | None
    interval_type: IntervalType
    interval_value: str
    min_chat_lines: int
    enabled: bool
    created_at: str
    updated_at: str
    last_fired_at: str | None

    @property
    def display(self) -> str:
        """Format as a human-readable identifier."""
        if self.name:
            return f"{self.name} (#{self.id})"
        return f"timer #{self.id}"

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> TimerInfo:
        """Build a TimerInfo from a database row.

        :param row: A row from the timers table (must include all columns).
        :return: A frozen TimerInfo snapshot.
        """
        return cls(
            id=row["id"],
            name=row["name"],
            message=row["message"],
            interval_type=IntervalType(row["interval_type"]),
            interval_value=row["interval_value"],
            min_chat_lines=row["min_chat_lines"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_fired_at=row["last_fired_at"],
        )


class TimerError(Exception):
    """Base class for timer domain errors."""


class TimerNotFoundError(TimerError):
    """No timer matches the given identifier."""

    def __init__(self, identifier: str | int) -> None:
        """Initialize with the identifier that was looked up.

        :param identifier: The ID or name that was looked up.
        """
        self.identifier = identifier
        super().__init__(f"timer not found: {identifier}")


class InvalidTimerNameError(TimerError):
    """Timer name does not match the required format."""

    def __init__(self, name: str) -> None:
        """Initialize with the invalid name.

        :param name: The invalid name.
        """
        self.name = name
        super().__init__(f"invalid timer name: {name}")


class TimerNameTakenError(TimerError):
    """A timer with this name already exists."""

    def __init__(self, name: str, existing_id: int) -> None:
        """Initialize with the conflicting name and existing timer ID.

        :param name: The conflicting name.
        :param existing_id: Database ID of the existing timer.
        """
        self.name = name
        self.existing_id = existing_id
        super().__init__(f"timer name taken: {name} (#{existing_id})")


class TimerAlreadyEnabledError(TimerError):
    """Timer is already enabled."""

    def __init__(self, info: TimerInfo) -> None:
        """Initialize with the timer info.

        :param info: The timer that is already enabled.
        """
        self.info = info
        super().__init__(f"already enabled: {info.display}")


class TimerAlreadyDisabledError(TimerError):
    """Timer is already disabled."""

    def __init__(self, info: TimerInfo) -> None:
        """Initialize with the timer info.

        :param info: The timer that is already disabled.
        """
        self.info = info
        super().__init__(f"already disabled: {info.display}")


class TimerMessageRequiredError(TimerError):
    """Timer cannot be enabled without a message."""

    def __init__(self, info: TimerInfo) -> None:
        """Initialize with the timer info.

        :param info: The timer that has no message set.
        """
        self.info = info
        super().__init__(f"no message set: {info.display}")


class NegativeLineCountError(TimerError):
    """Line count cannot be negative."""


# Timer names: must start with a letter or underscore to avoid ambiguity with
# numeric timer IDs. Letters, numbers, underscores, max 32 chars.
NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,31}$")

# Regex for simple duration strings like "30s", "5m", "1h", "2h30m".
_DURATION_PATTERN = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE)

_MIN_SIMPLE_SECONDS = 60


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
    :raises ValueError: If the expression is invalid.
    """
    try:
        now = datetime.now(UTC)
        it = CronSim(value, now)
        next(it)
    except CronSimError as e:
        raise ValueError(f"Invalid cron expression: {value}") from e

    return (IntervalType.CRON, value)


def duration_to_seconds(value: str) -> int:
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
