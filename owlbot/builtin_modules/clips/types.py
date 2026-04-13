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

"""Domain types and errors for the clips module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclass(frozen=True, slots=True)
class Clip:
    """Immutable snapshot of a persisted clip."""

    id: int
    title: str | None
    creator: str
    created_at: str
    duration: float | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Clip:
        """Construct a Clip from a database row.

        :param row: A database row with clip columns.
        :return: A new Clip instance.
        """
        return cls(
            id=row["id"],
            title=row["title"],
            creator=row["creator"],
            created_at=row["created_at"],
            duration=row["duration"],
        )


class ClipError(Exception):
    """Base class for clip module domain errors."""


class ClipNotFoundError(ClipError):
    """Raised when a clip ID does not exist."""

    def __init__(self, clip_id: int) -> None:
        """Initialize with the missing clip ID.

        :param clip_id: The ID that was not found.
        """
        self.clip_id = clip_id
        super().__init__(f"clip not found: {clip_id}")


class SessionNotFoundError(ClipError):
    """Raised when an editor session token is invalid or expired."""

    def __init__(self, token: str) -> None:
        """Initialize with the invalid session token.

        :param token: The token that was not found.
        """
        self.token = token
        super().__init__(f"session not found: {token}")


class CacheUnavailableError(ClipError):
    """No active HLS cache (stream is offline)."""


class InsufficientCacheError(ClipError):
    """Cache exists but does not have enough data for clipping."""


class InvalidClipParamsError(ClipError):
    """Raised when clip start/end/duration validation fails."""

    def __init__(self, reason: str) -> None:
        """Initialize with the validation failure reason.

        :param reason: Human-readable description of why validation failed.
        """
        self.reason = reason
        super().__init__(reason)
