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

"""Data containers and domain errors for the quotes module."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclasses.dataclass(frozen=True, slots=True)
class Quote:
    """Immutable snapshot of a quote record."""

    id: int
    text: str
    added_by: str
    created_at: str

    @property
    def date(self) -> str:
        """Return the creation date formatted as YYYY-MM-DD."""
        return datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d")

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Quote:
        """Build a Quote from a database row.

        :param row: A row from the quotes table (must include all columns).
        :return: A Quote snapshot.
        """
        return cls(
            id=row["id"],
            text=row["text"],
            added_by=row["added_by"],
            created_at=row["created_at"],
        )


class QuoteError(Exception):
    """Base class for quote domain errors."""


class QuoteNotFoundError(QuoteError):
    """No quote matches the given ID."""

    def __init__(self, quote_id: int) -> None:
        """Initialize with the quote ID that was not found.

        :param quote_id: The ID that was not found.
        """
        self.quote_id = quote_id
        super().__init__(f"quote not found: {quote_id}")
