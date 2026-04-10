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

"""Tests for the quotes module: types, repository, and manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from owlbot.api.storage import ModuleStorage
from owlbot.builtin_modules.quotes.manager import QuoteManager, get_manager
from owlbot.builtin_modules.quotes.repository import QuoteRepository
from owlbot.builtin_modules.quotes.types import (
    Quote,
    QuoteError,
    QuoteNotFoundError,
)
from tests.conftest import make_module_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from owlbot.api.context import ModuleContext


@pytest.fixture
async def quote_storage() -> AsyncIterator[ModuleStorage]:
    """Yield an open in-memory ModuleStorage."""
    async with ModuleStorage(None, "quotes") as storage:
        yield storage


@pytest.fixture
async def repo(quote_storage: ModuleStorage) -> QuoteRepository:
    """QuoteRepository backed by quote_storage, with schema initialized."""
    r = QuoteRepository(quote_storage)
    await r.setup()
    return r


@pytest.fixture
def quote_ctx(quote_storage: ModuleStorage) -> ModuleContext:
    """ModuleContext backed by quote_storage."""
    return make_module_context(storage=quote_storage, module_name="quotes")


@pytest.fixture
def manager(quote_ctx: ModuleContext, repo: QuoteRepository) -> QuoteManager:
    """QuoteManager backed by real repo."""
    return QuoteManager(quote_ctx, repo)


class TestQuoteFromRow:
    """Quote.from_row() database row conversion."""

    async def test_builds_from_row(self) -> None:
        """from_row constructs a Quote with correct field values."""
        row = {
            "id": 5,
            "text": "Test quote",
            "added_by": "someone",
            "created_at": "2026-03-15T10:00:00+00:00",
        }
        quote = Quote.from_row(row)  # type: ignore[arg-type]
        assert quote.id == 5
        assert quote.text == "Test quote"
        assert quote.added_by == "someone"
        assert quote.created_at == "2026-03-15T10:00:00+00:00"


class TestQuoteErrors:
    """Quote domain error classes."""

    def test_base_error_hierarchy(self) -> None:
        """All quote errors inherit from QuoteError."""
        assert issubclass(QuoteNotFoundError, QuoteError)

    def test_not_found_stores_quote_id(self) -> None:
        """QuoteNotFoundError stores the quote ID."""
        err = QuoteNotFoundError(42)
        assert err.quote_id == 42
        assert "42" in str(err)


class TestRepositoryCreate:
    """QuoteRepository.create() inserts new quotes."""

    async def test_create_returns_quote(self, repo: QuoteRepository) -> None:
        """Creating a quote returns a Quote snapshot with correct fields."""
        quote = await repo.create("Hello, world!", "tester")
        assert quote.id == 1
        assert quote.text == "Hello, world!"
        assert quote.added_by == "tester"
        assert quote.created_at is not None

    async def test_create_auto_increments(self, repo: QuoteRepository) -> None:
        """Each created quote gets a sequential ID."""
        q1 = await repo.create("First", "tester")
        q2 = await repo.create("Second", "tester")
        assert q2.id == q1.id + 1


class TestRepositoryGet:
    """QuoteRepository.get() fetches by ID."""

    async def test_get_existing(self, repo: QuoteRepository) -> None:
        """Fetches a quote by its ID."""
        created = await repo.create("Test", "tester")
        fetched = await repo.get(created.id)
        assert fetched.id == created.id
        assert fetched.text == "Test"

    async def test_get_not_found(self, repo: QuoteRepository) -> None:
        """Raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError) as exc_info:
            await repo.get(999)
        assert exc_info.value.quote_id == 999


class TestRepositoryGetRandom:
    """QuoteRepository.get_random() fetches a random quote."""

    async def test_get_random_returns_quote(self, repo: QuoteRepository) -> None:
        """Returns a quote when the table is not empty."""
        await repo.create("Only quote", "tester")
        quote = await repo.get_random()
        assert quote is not None
        assert quote.text == "Only quote"

    async def test_get_random_empty_table(self, repo: QuoteRepository) -> None:
        """Returns None when no quotes exist."""
        result = await repo.get_random()
        assert result is None


class TestRepositoryDelete:
    """QuoteRepository.delete() removes records."""

    async def test_delete_returns_quote(self, repo: QuoteRepository) -> None:
        """Deleting a quote returns its snapshot."""
        created = await repo.create("Doomed", "tester")
        deleted = await repo.delete(created.id)
        assert deleted.id == created.id
        assert deleted.text == "Doomed"

    async def test_delete_removes_from_db(self, repo: QuoteRepository) -> None:
        """Deleted quote is no longer retrievable."""
        created = await repo.create("Doomed", "tester")
        await repo.delete(created.id)
        with pytest.raises(QuoteNotFoundError):
            await repo.get(created.id)

    async def test_delete_not_found(self, repo: QuoteRepository) -> None:
        """Raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError) as exc_info:
            await repo.delete(999)
        assert exc_info.value.quote_id == 999


class TestRepositoryListAll:
    """QuoteRepository.list_all() returns all quotes."""

    async def test_list_all(self, repo: QuoteRepository) -> None:
        """Returns all quotes ordered by ID."""
        await repo.create("First", "alice")
        await repo.create("Second", "bob")
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].text == "First"
        assert result[1].text == "Second"

    async def test_list_all_empty(self, repo: QuoteRepository) -> None:
        """Returns an empty list when no quotes exist."""
        assert await repo.list_all() == []


class TestQuoteManagerAddQuote:
    """QuoteManager.add_quote() creates quotes."""

    async def test_add_quote(self, manager: QuoteManager) -> None:
        """add_quote creates a quote and returns its snapshot."""
        quote = await manager.add_quote("Test quote", "tester")
        assert quote.id == 1
        assert quote.text == "Test quote"
        assert quote.added_by == "tester"


class TestQuoteManagerDeleteQuote:
    """QuoteManager.delete_quote() removes quotes."""

    async def test_delete_quote(self, manager: QuoteManager) -> None:
        """delete_quote removes the quote and returns its snapshot."""
        created = await manager.add_quote("Doomed", "tester")
        deleted = await manager.delete_quote(created.id)
        assert deleted.id == created.id
        assert deleted.text == "Doomed"
        assert await manager.list_quotes() == []

    async def test_delete_quote_not_found(self, manager: QuoteManager) -> None:
        """delete_quote raises QuoteNotFoundError for unknown ID."""
        with pytest.raises(QuoteNotFoundError):
            await manager.delete_quote(999)


class TestQuoteManagerGetQuote:
    """QuoteManager.get_quote() fetches by ID."""

    async def test_get_quote(self, manager: QuoteManager) -> None:
        """get_quote returns the matching quote."""
        created = await manager.add_quote("Test", "tester")
        fetched = await manager.get_quote(created.id)
        assert fetched.id == created.id
        assert fetched.text == "Test"

    async def test_get_quote_not_found(self, manager: QuoteManager) -> None:
        """get_quote raises QuoteNotFoundError for unknown ID."""
        with pytest.raises(QuoteNotFoundError):
            await manager.get_quote(999)


class TestQuoteManagerGetRandomQuote:
    """QuoteManager.get_random_quote() fetches random quotes."""

    async def test_get_random_quote(self, manager: QuoteManager) -> None:
        """get_random_quote returns a quote when quotes exist."""
        await manager.add_quote("Only one", "tester")
        quote = await manager.get_random_quote()
        assert quote is not None
        assert quote.text == "Only one"

    async def test_get_random_quote_empty(self, manager: QuoteManager) -> None:
        """get_random_quote returns None when no quotes exist."""
        result = await manager.get_random_quote()
        assert result is None


class TestQuoteManagerListQuotes:
    """QuoteManager.list_quotes() returns all quotes."""

    async def test_list_quotes(self, manager: QuoteManager) -> None:
        """list_quotes returns all quotes ordered by ID."""
        await manager.add_quote("First", "alice")
        await manager.add_quote("Second", "bob")
        result = await manager.list_quotes()
        assert len(result) == 2
        assert result[0].text == "First"
        assert result[1].text == "Second"

    async def test_list_quotes_empty(self, manager: QuoteManager) -> None:
        """list_quotes returns an empty list when no quotes exist."""
        assert await manager.list_quotes() == []


class TestGetManager:
    """get_manager() helper."""

    def test_returns_manager(self) -> None:
        """get_manager returns the QuoteManager from ctx.state."""
        ctx = make_module_context()
        repo_stub = QuoteRepository(ctx.storage)
        mgr = QuoteManager(ctx, repo_stub)
        ctx.state["manager"] = mgr
        assert get_manager(ctx) is mgr

    def test_raises_if_not_initialized(self) -> None:
        """get_manager raises RuntimeError when manager is missing."""
        ctx = make_module_context()
        with pytest.raises(RuntimeError, match="QuoteManager is not initialized"):
            get_manager(ctx)
