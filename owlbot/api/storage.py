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

"""SQLite storage API for Owlbot modules."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class StorageError(Exception):
    """Raised when a storage operation fails."""


class ModuleStorage:
    """Module-scoped async SQLite storage with a single lazy connection.

    Each module gets its own isolated database file. The connection is
    created lazily on the first storage operation and reused for all
    subsequent calls. WAL mode is enabled for crash resilience.

    Pass ``None`` as ``storage_dir`` to use an in-memory database
    instead of a file, which is useful for testing.

    Each storage operation (execute, fetch_one, etc.) auto-commits on
    success and rolls back on failure. No transaction state is held
    between calls.

    For operations that must succeed or fail together, use the
    transaction() context manager to group them into a single atomic
    unit.
    """

    def __init__(self, storage_dir: Path | None, module_name: str) -> None:
        """Initialize the storage API.

        :param storage_dir: Directory where module databases are stored.
            Pass ``None`` to use an in-memory database (useful for tests).
        :param module_name: Name of the module this storage belongs to.
        """
        self._db_path: Path | str = (
            storage_dir / f"{module_name}.db" if storage_dir is not None else ":memory:"
        )
        self._module_name = module_name
        self._conn: aiosqlite.Connection | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._txn_conn: contextvars.ContextVar[aiosqlite.Connection | None] = (
            contextvars.ContextVar(f"_txn_conn_{module_name}", default=None)
        )
        self._logger = logging.getLogger(f"owlbot.modules.{module_name}.storage")

    async def __aenter__(self) -> Self:
        """Enter an async context manager that closes the storage on exit.

        Enables ``async with ModuleStorage(...) as s:`` for scoped usage
        (e.g. test fixtures).

        In production, ``ModuleLoader`` manages storage lifecycle with explicit
        ``_close()`` calls because the storage is created during module load
        but closed in separate code paths (unload, load failure, setup failure),
        so there is no single scope an ``async with`` block could wrap.
        """
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the storage when leaving the ``async with`` block."""
        await self._close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ModuleStorage]:
        """Context manager for explicit transaction control.

        Use this when you need multiple operations to succeed or fail
        together. Shares the single connection across all operations
        within the block, skipping per-operation auto-commit. Commits on
        success, rolls back on exception.

        Nesting ``transaction()`` calls is not supported and raises
        ``RuntimeError``.

        Example:
            async with ctx.storage.transaction():
                await ctx.storage.execute("UPDATE scores SET score = score - ?", (10,))
                await ctx.storage.execute("UPDATE scores SET score = score + ?", (10,))
                # Both committed together, or both rolled back on error

        :return: This ModuleStorage instance.
        :raises RuntimeError: If called while already inside a transaction.

        """
        if self._txn_conn.get() is not None:
            raise RuntimeError(
                "transaction() cannot be nested. Already inside an active transaction."
            )
        async with self._lock:
            conn = await self._ensure_connection()
            token = self._txn_conn.set(conn)
            self._logger.debug("Explicit transaction started.")
            try:
                yield self
                await conn.commit()
                self._logger.debug("Transaction committed.")
            except BaseException:
                await conn.rollback()
                self._logger.debug("Transaction rolled back.")
                raise
            finally:
                self._txn_conn.reset(token)

    async def execute(
        self,
        sql: str,
        parameters: tuple[Any, ...] | dict[str, Any] = (),
    ) -> aiosqlite.Cursor:
        """Execute a SQL statement.

        :param sql: SQL statement (use ? or :name for parameters).
        :param parameters: Query parameters (tuple for ?, dict for :name).
        :return: Cursor with lastrowid and rowcount.
        :raises StorageError: If execution fails.
        """
        async with self._connection() as conn:
            self._logger.debug("Execute: %s", sql)
            try:
                return await conn.execute(sql, parameters)
            except aiosqlite.Error as e:
                self._logger.exception("SQL error.")
                raise StorageError(f"SQL execution failed: {e}") from e

    async def execute_many(
        self,
        sql: str,
        parameters: list[tuple[Any, ...]] | list[dict[str, Any]],
    ) -> aiosqlite.Cursor:
        """Execute a SQL statement with multiple parameter sets.

        Useful for batch inserts/updates.

        :param sql: SQL statement.
        :param parameters: List of parameter tuples/dicts.
        :return: Cursor with rowcount.
        :raises StorageError: If execution fails.
        """
        async with self._connection() as conn:
            self._logger.debug(
                "Execute many (%d rows): %s",
                len(parameters),
                sql,
            )
            try:
                return await conn.executemany(sql, parameters)
            except aiosqlite.Error as e:
                self._logger.exception("SQL error in executemany.")
                raise StorageError(f"SQL execution failed: {e}") from e

    async def fetch_one(
        self,
        sql: str,
        parameters: tuple[Any, ...] | dict[str, Any] = (),
    ) -> aiosqlite.Row | None:
        """Execute a query and fetch one row.

        :param sql: SELECT statement.
        :param parameters: Query parameters.
        :return: Row as a sqlite3.Row (supports both index and key access),
            or None if no results.
        :raises StorageError: If execution fails.
        """
        async with self._connection() as conn:
            self._logger.debug("Fetch one: %s", sql)
            try:
                cursor = await conn.execute(sql, parameters)
                return await cursor.fetchone()
            except aiosqlite.Error as e:
                self._logger.exception("SQL error.")
                raise StorageError(f"SQL fetch failed: {e}") from e

    async def fetch_all(
        self,
        sql: str,
        parameters: tuple[Any, ...] | dict[str, Any] = (),
    ) -> list[aiosqlite.Row]:
        """Execute a query and fetch all rows.

        :param sql: SELECT statement.
        :param parameters: Query parameters.
        :return: List of rows as sqlite3.Row objects
            (support both index and key access).
        :raises StorageError: If execution fails.
        """
        async with self._connection() as conn:
            self._logger.debug("Fetch all: %s", sql)
            try:
                cursor = await conn.execute(sql, parameters)
                return list(await cursor.fetchall())
            except aiosqlite.Error as e:
                self._logger.exception("SQL error.")
                raise StorageError(f"SQL fetch failed: {e}") from e

    async def fetch_value(
        self,
        sql: str,
        parameters: tuple[Any, ...] | dict[str, Any] = (),
    ) -> Any | None:
        """Execute a query and fetch a single value.

        :param sql: SELECT statement returning one column.
        :param parameters: Query parameters.
        :return: The value, or None if no results.
        :raises StorageError: If execution fails.
        """
        row = await self.fetch_one(sql, parameters)
        if row is None:
            return None
        return row[0]

    async def _ensure_connection(self) -> aiosqlite.Connection:
        """Return the existing connection or create one on first use."""
        if self._closed:
            raise StorageError("Storage is closed")
        if self._conn is not None:
            return self._conn
        if isinstance(self._db_path, Path):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA foreign_keys = ON")
        except BaseException:
            # Setup interrupted (timeout, cancellation, etc.). Close the
            # connection to release any write lock held by a partially-
            # executed PRAGMA. shield() keeps the close running on the
            # background thread even if this task is cancelled.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(conn.close())
            raise
        self._conn = conn
        self._logger.info("Database opened at: %s", self._db_path)
        return conn

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Async context manager that provides the connection.

        If already inside a ``transaction()``, yields the shared connection
        without committing (the transaction block handles that). Otherwise
        auto-commits on success and rolls back on failure.
        """
        existing = self._txn_conn.get()
        if existing is not None:
            yield existing
            return

        async with self._lock:
            conn = await self._ensure_connection()
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def _close(self) -> None:
        """Close the connection (internal use by bot)."""
        self._closed = True
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._logger.info("Connection closed.")
