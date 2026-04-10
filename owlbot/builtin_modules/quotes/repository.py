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

"""Persistence layer for quote records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .types import Quote, QuoteNotFoundError

if TYPE_CHECKING:
    from owlbot.api.storage import ModuleStorage


class QuoteRepository:
    """Handles all database operations for quote records."""

    def __init__(self, storage: ModuleStorage) -> None:
        """Initialize with a module storage instance.

        :param storage: The module's storage backend.
        """
        self._storage = storage

    async def setup(self) -> None:
        """Create the quotes table if it does not exist."""
        await self._storage.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                added_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

    async def create(self, text: str, added_by: str) -> Quote:
        """Insert a new quote and return its snapshot.

        :param text: The quote text.
        :param added_by: Display name of the user who added the quote.
        :return: Snapshot of the newly created quote.
        """
        now = datetime.now(UTC).isoformat()
        row = await self._storage.fetch_one(
            "INSERT INTO quotes (text, added_by, created_at) "
            "VALUES (?, ?, ?) RETURNING *",
            (text, added_by, now),
        )
        if row is None:
            raise RuntimeError("INSERT RETURNING did not produce a row")
        return Quote.from_row(row)

    async def get(self, quote_id: int) -> Quote:
        """Fetch a quote by its ID.

        :param quote_id: Database ID of the quote.
        :return: The matching Quote.
        :raises QuoteNotFoundError: If no quote matches the ID.
        """
        row = await self._storage.fetch_one(
            "SELECT * FROM quotes WHERE id = ?", (quote_id,)
        )
        if row is None:
            raise QuoteNotFoundError(quote_id)
        return Quote.from_row(row)

    async def get_random(self) -> Quote | None:
        """Fetch a random quote.

        :return: A random Quote, or None if the table is empty.
        """
        row = await self._storage.fetch_one(
            "SELECT * FROM quotes ORDER BY RANDOM() LIMIT 1"
        )
        if row is None:
            return None
        return Quote.from_row(row)

    async def delete(self, quote_id: int) -> Quote:
        """Delete a quote and return its snapshot.

        :param quote_id: Database ID of the quote.
        :return: Snapshot of the deleted quote.
        :raises QuoteNotFoundError: If no quote matches the ID.
        """
        row = await self._storage.fetch_one(
            "DELETE FROM quotes WHERE id = ? RETURNING *", (quote_id,)
        )
        if row is None:
            raise QuoteNotFoundError(quote_id)
        return Quote.from_row(row)

    async def list_all(self) -> list[Quote]:
        """Return all quotes ordered by ID.

        :return: List of Quote snapshots.
        """
        rows = await self._storage.fetch_all("SELECT * FROM quotes ORDER BY id")
        return [Quote.from_row(row) for row in rows]
