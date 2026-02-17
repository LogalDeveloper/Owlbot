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

"""Tests for ModuleStorage — the async SQLite storage layer."""

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from owlbot.api.storage import ModuleStorage, StorageError

CREATE_TABLE = "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, value INTEGER)"


@pytest.fixture
async def storage_with_table(storage: ModuleStorage) -> ModuleStorage:
    """Storage instance with a pre-created items table."""
    await storage.execute(CREATE_TABLE)
    return storage


class TestLazyConnections:
    """No connections exist until the first operation."""

    async def test_no_connections_before_use(self, storage: ModuleStorage) -> None:
        """Pool is empty immediately after construction."""
        assert len(storage._all_connections) == 0

    async def test_first_operation_creates_one_connection(
        self, storage: ModuleStorage
    ) -> None:
        """The first execute creates exactly one pooled connection."""
        await storage.execute("SELECT 1")
        assert len(storage._all_connections) == 1


class TestPragmas:
    """WAL mode and foreign keys are enabled on new connections."""

    async def test_wal_mode(self, storage: ModuleStorage) -> None:
        """journal_mode is set to WAL."""
        value = await storage.fetch_value("PRAGMA journal_mode")
        assert value == "wal"

    async def test_foreign_keys_enabled(self, storage: ModuleStorage) -> None:
        """foreign_keys pragma is enabled."""
        value = await storage.fetch_value("PRAGMA foreign_keys")
        assert value == 1


class TestExecute:
    """execute() runs SQL statements and returns a cursor."""

    async def test_ddl_creates_table(self, storage: ModuleStorage) -> None:
        """CREATE TABLE produces a table that can be queried."""
        await storage.execute(CREATE_TABLE)
        rows = await storage.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
        )
        assert len(rows) == 1

    async def test_insert_returns_lastrowid(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """INSERT returns a cursor whose lastrowid is set."""
        cursor = await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
        )
        assert cursor.lastrowid == 1

    async def test_update_returns_rowcount(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """UPDATE returns a cursor whose rowcount reflects affected rows."""
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
        )
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("b", 2)
        )
        cursor = await storage_with_table.execute(
            "UPDATE items SET value = 0 WHERE value > 0"
        )
        assert cursor.rowcount == 2


class TestExecuteMany:
    """execute_many() batch-inserts multiple rows."""

    async def test_batch_insert(self, storage_with_table: ModuleStorage) -> None:
        """Batch insert adds all rows."""
        rows_data = [("a", 1), ("b", 2), ("c", 3)]
        await storage_with_table.execute_many(
            "INSERT INTO items (name, value) VALUES (?, ?)", rows_data
        )
        rows = await storage_with_table.fetch_all("SELECT * FROM items")
        assert len(rows) == 3


class TestFetchOne:
    """fetch_one() returns a Row or None."""

    @pytest.mark.parametrize(
        "accessor, expected",
        [
            pytest.param(lambda row: row["name"], "a", id="key-access"),
            pytest.param(lambda row: row[1], "a", id="index-access"),
        ],
    )
    async def test_row_access(
        self,
        storage_with_table: ModuleStorage,
        accessor: Callable[[Any], Any],
        expected: str,
    ) -> None:
        """Returned row supports both key and index access."""
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
        )
        row = await storage_with_table.fetch_one("SELECT * FROM items WHERE id = 1")
        assert accessor(row) == expected

    async def test_returns_none_when_empty(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """Returns None when no rows match."""
        row = await storage_with_table.fetch_one("SELECT * FROM items WHERE id = 999")
        assert row is None


class TestFetchAll:
    """fetch_all() returns a list of Rows or an empty list."""

    async def test_returns_all_rows(self, storage_with_table: ModuleStorage) -> None:
        """Returns all matching rows."""
        await storage_with_table.execute_many(
            "INSERT INTO items (name, value) VALUES (?, ?)",
            [("a", 1), ("b", 2)],
        )
        rows = await storage_with_table.fetch_all("SELECT * FROM items ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["name"] == "a"
        assert rows[1]["name"] == "b"

    async def test_returns_empty_list(self, storage_with_table: ModuleStorage) -> None:
        """Returns an empty list when no rows match."""
        rows = await storage_with_table.fetch_all("SELECT * FROM items WHERE id = 999")
        assert rows == []


class TestFetchValue:
    """fetch_value() returns a scalar or None."""

    async def test_returns_scalar(self, storage_with_table: ModuleStorage) -> None:
        """Returns the first column of the first row."""
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 42)
        )
        value = await storage_with_table.fetch_value(
            "SELECT value FROM items WHERE name = ?", ("a",)
        )
        assert value == 42

    async def test_returns_none_when_empty(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """Returns None when no rows match."""
        value = await storage_with_table.fetch_value(
            "SELECT value FROM items WHERE id = 999"
        )
        assert value is None


class TestAutoCommit:
    """Outside _checkout(), each operation auto-commits or auto-rolls-back."""

    async def test_auto_commits_on_success(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """A successful write is visible from a second storage instance."""
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
        )
        # Open a second storage pointing at the same file to verify commit.
        async with ModuleStorage(
            storage_with_table._db_path.parent, "test_module"
        ) as s2:
            row = await s2.fetch_one("SELECT name FROM items WHERE id = 1")
            assert row is not None
            assert row["name"] == "a"

    async def test_auto_rollback_on_error(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """A failed write does not persist partial changes."""
        await storage_with_table.execute(
            "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
        )
        with pytest.raises(StorageError):
            # Duplicate primary key triggers an error.
            await storage_with_table.execute(
                "INSERT INTO items (id, name, value) VALUES (?, ?, ?)",
                (1, "b", 2),
            )
        # Original row should still be intact.
        row = await storage_with_table.fetch_one("SELECT name FROM items WHERE id = 1")
        assert row is not None
        assert row["name"] == "a"


class TestCheckout:
    """_checkout() pins a single connection for all operations."""

    async def test_commit_persists(self, storage_with_table: ModuleStorage) -> None:
        """Writes inside _checkout() become visible after _commit()."""
        async with storage_with_table._checkout():
            await storage_with_table.execute(
                "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
            )
            await storage_with_table._commit()

        row = await storage_with_table.fetch_one("SELECT name FROM items WHERE id = 1")
        assert row is not None
        assert row["name"] == "a"

    async def test_rollback_discards(self, storage_with_table: ModuleStorage) -> None:
        """Writes inside _checkout() are discarded after _rollback()."""
        async with storage_with_table._checkout():
            await storage_with_table.execute(
                "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
            )
            await storage_with_table._rollback()

        row = await storage_with_table.fetch_one("SELECT name FROM items WHERE id = 1")
        assert row is None

    async def test_uncommitted_writes_invisible_from_other_connection(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """Uncommitted writes are not visible from a separate connection."""
        async with (
            ModuleStorage(storage_with_table._db_path.parent, "test_module") as s2,
            storage_with_table._checkout(),
        ):
            await storage_with_table.execute(
                "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
            )
            # Before commit, s2 should not see the row.
            row = await s2.fetch_one("SELECT name FROM items WHERE id = 1")
            assert row is None


class TestTransaction:
    """transaction() context manager commits on clean exit, rolls back on exception."""

    async def test_commits_on_clean_exit(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """Writes inside transaction() persist after clean exit."""
        async with storage_with_table._checkout(), storage_with_table.transaction():
            await storage_with_table.execute(
                "INSERT INTO items (name, value) VALUES (?, ?)", ("a", 1)
            )

        row = await storage_with_table.fetch_one("SELECT name FROM items WHERE id = 1")
        assert row is not None
        assert row["name"] == "a"

    async def test_rolls_back_on_exception(
        self, storage_with_table: ModuleStorage
    ) -> None:
        """Writes inside transaction() are discarded if an exception is raised."""
        with pytest.raises(RuntimeError, match="boom"):
            async with storage_with_table._checkout(), storage_with_table.transaction():
                await storage_with_table.execute(
                    "INSERT INTO items (name, value) VALUES (?, ?)",
                    ("a", 1),
                )
                raise RuntimeError("boom")

        row = await storage_with_table.fetch_one("SELECT name FROM items WHERE id = 1")
        assert row is None


class TestConnectionPool:
    """Connection pool reuses connections and respects pool_size."""

    async def test_connections_are_reused(self, storage: ModuleStorage) -> None:
        """After release, the same connection is reused."""
        await storage.execute("SELECT 1")
        assert len(storage._all_connections) == 1
        await storage.execute("SELECT 2")
        assert len(storage._all_connections) == 1

    async def test_pool_grows_up_to_pool_size(self, tmp_path: Path) -> None:
        """Concurrent checkouts grow the pool up to pool_size."""
        async with (
            ModuleStorage(tmp_path, "pool_test", pool_size=2) as s,
            s._checkout(),
            s._checkout(),
        ):
            assert len(s._all_connections) == 2

    async def test_pool_exhaustion_blocks(self, tmp_path: Path) -> None:
        """When the pool is exhausted, acquire blocks until a connection is released."""
        async with (
            ModuleStorage(tmp_path, "pool_block", pool_size=1) as s,
            s._checkout(),
        ):
            # Pool is exhausted (pool_size=1, one checked out).
            # A second acquire should block, so wait_for should time out.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(s._acquire(), timeout=0.1)


class TestStorageError:
    """Invalid SQL raises StorageError wrapping the underlying error."""

    @pytest.mark.parametrize(
        "method, args",
        [
            pytest.param(
                "execute",
                ("INVALID SQL",),
                id="execute",
            ),
            pytest.param(
                "fetch_one",
                ("INVALID SQL",),
                id="fetch-one",
            ),
            pytest.param(
                "fetch_all",
                ("INVALID SQL",),
                id="fetch-all",
            ),
            pytest.param(
                "fetch_value",
                ("INVALID SQL",),
                id="fetch-value",
            ),
            pytest.param(
                "execute_many",
                ("INVALID SQL", [()]),
                id="execute-many",
            ),
        ],
    )
    async def test_invalid_sql_raises_storage_error(
        self, storage: ModuleStorage, method: str, args: tuple[Any, ...]
    ) -> None:
        """Invalid SQL raises StorageError for all query methods."""
        with pytest.raises(StorageError):
            await getattr(storage, method)(*args)

    @pytest.mark.parametrize(
        "method, args",
        [
            pytest.param("execute", ("SELECT 1",), id="execute"),
            pytest.param("fetch_one", ("SELECT 1",), id="fetch-one"),
            pytest.param("fetch_all", ("SELECT 1",), id="fetch-all"),
            pytest.param("fetch_value", ("SELECT 1",), id="fetch-value"),
            pytest.param("execute_many", ("SELECT 1", [()]), id="execute-many"),
        ],
    )
    async def test_closed_storage_raises_storage_error(
        self, storage: ModuleStorage, method: str, args: tuple[Any, ...]
    ) -> None:
        """Operations on closed storage raise StorageError."""
        await storage._close()
        with pytest.raises(StorageError, match="Storage is closed"):
            await getattr(storage, method)(*args)

    async def test_storage_error_wraps_cause(self, storage: ModuleStorage) -> None:
        """StorageError.__cause__ is the underlying aiosqlite error."""
        with pytest.raises(StorageError) as exc_info:
            await storage.execute("INVALID SQL")
        assert exc_info.value.__cause__ is not None


class TestClose:
    """_close() drains the pool and marks storage closed."""

    async def test_close_drains_pool(self, storage: ModuleStorage) -> None:
        """After close, all connections are removed."""
        await storage.execute("SELECT 1")
        assert len(storage._all_connections) == 1
        await storage._close()
        assert len(storage._all_connections) == 0
        assert storage._closed is True

    async def test_double_close_is_safe(self, storage: ModuleStorage) -> None:
        """Calling _close() twice does not raise."""
        await storage.execute("SELECT 1")
        await storage._close()
        await storage._close()

    async def test_context_manager_closes_on_exit(self, tmp_path: Path) -> None:
        """Exiting the async with block closes all connections."""
        async with ModuleStorage(tmp_path, "ctx_test") as s:
            await s.execute("SELECT 1")
            assert len(s._all_connections) == 1
        assert len(s._all_connections) == 0
        assert s._closed is True
