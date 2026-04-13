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

"""Persistence layer for the clips module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from owlbot.api import StorageError

from .types import Clip, ClipNotFoundError

if TYPE_CHECKING:
    from owlbot.api import ModuleStorage


class ClipRepository:
    """All database operations for clip entities.

    :param storage: Module-scoped SQLite storage instance.
    """

    def __init__(self, storage: ModuleStorage) -> None:
        """Initialize with a module-scoped storage instance.

        :param storage: The SQLite storage for this module.
        """
        self._storage = storage

    async def setup(self) -> None:
        """Create the clips table if it does not exist."""
        await self._storage.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                creator TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration REAL
            )
            """
        )

    async def create(
        self,
        title: str | None,
        creator: str,
        created_at: str,
        duration: float,
    ) -> Clip:
        """Insert a new clip and return its snapshot.

        :param title: Optional clip title.
        :param creator: Display name of the clip creator.
        :param created_at: ISO 8601 timestamp.
        :param duration: Clip duration in seconds.
        :return: The created Clip.
        """
        row = await self._storage.fetch_one(
            "INSERT INTO clips (title, creator, created_at, duration) "
            "VALUES (?, ?, ?, ?) RETURNING *",
            (title, creator, created_at, duration),
        )
        if row is None:
            msg = "INSERT ... RETURNING * returned no row."
            raise StorageError(msg)
        return Clip.from_row(row)

    async def get(self, clip_id: int) -> Clip:
        """Fetch a clip by ID.

        :param clip_id: The clip's primary key.
        :return: The Clip snapshot.
        :raises ClipNotFoundError: If no clip with that ID exists.
        """
        row = await self._storage.fetch_one(
            "SELECT * FROM clips WHERE id = ?", (clip_id,)
        )
        if row is None:
            raise ClipNotFoundError(clip_id)
        return Clip.from_row(row)

    async def delete(self, clip_id: int) -> Clip:
        """Delete a clip by ID and return its snapshot.

        :param clip_id: The clip's primary key.
        :return: The deleted Clip snapshot.
        :raises ClipNotFoundError: If no clip with that ID exists.
        """
        row = await self._storage.fetch_one(
            "DELETE FROM clips WHERE id = ? RETURNING *", (clip_id,)
        )
        if row is None:
            raise ClipNotFoundError(clip_id)
        return Clip.from_row(row)

    async def list_all(self) -> list[Clip]:
        """Return all clips ordered by creation date descending.

        :return: List of Clip snapshots.
        """
        rows = await self._storage.fetch_all(
            "SELECT * FROM clips ORDER BY created_at DESC"
        )
        return [Clip.from_row(row) for row in rows]
