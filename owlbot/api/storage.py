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
import contextvars
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class StorageError(Exception):
    """Raised when a storage operation fails."""


class ModuleStorage:
    """Module-scoped async SQLite storage backed by a lazy connection pool.

    Each module gets its own isolated database file. Connections are created
    lazily and pooled up to ``pool_size``. WAL mode is enabled so that
    concurrent readers and a single writer can operate without "database is
    locked" errors.

    Transactions are managed at the handler level. The bot commits after each
    handler succeeds, and rolls back if the handler throws an exception. Module
    developers don't need to think about commits for normal usage.

    For finer control within a handler, use the transaction() context manager
    to group multiple operations that should succeed or fail together.

    Concurrency: The _checkout() context manager acquires a dedicated
    connection from the pool for the current handler invocation.
    """

    def __init__(self, storage_dir: Path, module_name: str, pool_size: int = 4):
        """Initialize the storage API.

        :param storage_dir: Directory where module databases are stored.
        :param module_name: Name of the module this storage belongs to.
        :param pool_size: Maximum number of pooled connections.
        """
        self._db_path = storage_dir / f"{module_name}.db"
        self._module_name = module_name
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._pool_size = pool_size
        self._all_connections: list[aiosqlite.Connection] = []
        self._closed = False
        self._txn_conn: contextvars.ContextVar[aiosqlite.Connection | None] = (
            contextvars.ContextVar(f"_txn_conn_{module_name}", default=None)
        )
        self._logger = logging.getLogger(f"owlbot.modules.{module_name}.storage")
        self._logger.info(f"Database initialized at: {self._db_path.absolute()}")

    async def __aenter__(self) -> ModuleStorage:
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
        """Context manager for explicit transaction control within a handler.

        Use this when you need multiple operations to succeed or fail together
        within a single handler. Commits on success, rolls back on exception.

        Example:
            async with ctx.storage.transaction():
                await ctx.storage.execute("UPDATE scores SET score = score - ?", (10,))
                await ctx.storage.execute("UPDATE scores SET score = score + ?", (10,))
                # Both committed together, or both rolled back on error

        :return: This ModuleStorage instance.

        """
        self._logger.debug("Explicit transaction started.")
        try:
            yield self
            await self._commit()
        except BaseException:
            await self._rollback()
            raise

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
            self._logger.debug(f"Execute: {sql[:80]}{'...' if len(sql) > 80 else ''}")
            try:
                return await conn.execute(sql, parameters)
            except aiosqlite.Error as e:
                self._logger.error(f"SQL error: {e}")
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
                f"Execute many ({len(parameters)} rows): "
                f"{sql[:80]}{'...' if len(sql) > 80 else ''}"
            )
            try:
                return await conn.executemany(sql, parameters)
            except aiosqlite.Error as e:
                self._logger.error(f"SQL error in executemany: {e}")
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
            self._logger.debug(f"Fetch one: {sql[:80]}{'...' if len(sql) > 80 else ''}")
            try:
                cursor = await conn.execute(sql, parameters)
                return await cursor.fetchone()
            except aiosqlite.Error as e:
                self._logger.error(f"SQL error: {e}")
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
            self._logger.debug(f"Fetch all: {sql[:80]}{'...' if len(sql) > 80 else ''}")
            try:
                cursor = await conn.execute(sql, parameters)
                return list(await cursor.fetchall())
            except aiosqlite.Error as e:
                self._logger.error(f"SQL error: {e}")
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

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new database connection with WAL mode and foreign keys."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        self._all_connections.append(conn)
        self._logger.debug(
            f"Pool connection created ({len(self._all_connections)}/{self._pool_size})."
        )
        return conn

    async def _acquire(self) -> aiosqlite.Connection:
        """Acquire a connection from the pool, creating one if needed."""
        if self._closed:
            raise StorageError("Storage is closed")

        try:
            return self._pool.get_nowait()
        except asyncio.QueueEmpty:
            pass

        if len(self._all_connections) < self._pool_size:
            return await self._create_connection()

        # Pool exhausted, wait for one to be returned.
        return await self._pool.get()

    def _release(self, conn: aiosqlite.Connection) -> None:
        """Return a connection to the pool."""
        self._pool.put_nowait(conn)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Async context manager that provides a connection.

        If already inside a ``_checkout``, yields the checked-out connection
        without releasing it. Otherwise acquires a standalone connection from
        the pool that auto-commits on success and rolls back on failure
        before being released.
        """
        existing = self._txn_conn.get()
        if existing is not None:
            yield existing
            return

        conn = await self._acquire()
        try:
            yield conn
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise
        finally:
            self._release(conn)

    @asynccontextmanager
    async def _checkout(self) -> AsyncIterator[None]:
        """Check out a connection from the pool for the duration of a handler.

        Sets a ContextVar so that all storage operations within the handler
        reuse the same connection.
        """
        conn = await self._acquire()
        token = self._txn_conn.set(conn)
        try:
            yield
        finally:
            self._txn_conn.reset(token)
            self._release(conn)

    async def _commit(self) -> None:
        """Commit the current transaction (internal use by bot).

        Called automatically after each handler completes successfully.
        """
        conn = self._txn_conn.get()
        if conn is not None:
            await conn.commit()
            self._logger.debug("Transaction committed.")

    async def _rollback(self) -> None:
        """Rollback the current transaction (internal use by bot).

        Called automatically if a handler throws an exception.
        """
        conn = self._txn_conn.get()
        if conn is not None:
            await conn.rollback()
            self._logger.debug("Transaction rolled back.")

    async def _close(self) -> None:
        """Close all pool connections (internal use by bot)."""
        self._closed = True

        for conn in self._all_connections:
            await conn.close()

        while not self._pool.empty():
            self._pool.get_nowait()

        self._all_connections.clear()
        self._logger.info("All pool connections closed.")
