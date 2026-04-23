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

"""Tests for the quotes module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from owlbot.api.event_types import EventType
from owlbot.builtin_modules import quotes as quotes_pkg
from owlbot.builtin_modules.quotes import setup, teardown
from owlbot.builtin_modules.quotes.manager import QuoteManager, get_manager
from owlbot.builtin_modules.quotes.repository import QuoteRepository
from owlbot.builtin_modules.quotes.types import (
    Quote,
    QuoteError,
    QuoteNotFoundError,
)
from owlbot.testing import make_chat_event, make_user

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Any

    from aiohttp.test_utils import TestClient

    from owlbot.api.context import ModuleContext
    from owlbot.api.storage import ModuleStorage
    from owlbot.registries.events import EventDispatcher
    from owlbot.testing import RecordingOwncastClient


@pytest.fixture
def module_pkg() -> ModuleType:
    """Auto-wire module_name, templates, and handler registration for quotes."""
    return quotes_pkg


@pytest.fixture
async def repo(storage: ModuleStorage) -> QuoteRepository:
    """QuoteRepository backed by storage, with schema initialized."""
    repo = QuoteRepository(storage)
    await repo.setup()
    return repo


@pytest.fixture
def manager(module_context: ModuleContext, repo: QuoteRepository) -> QuoteManager:
    """QuoteManager built directly for unit tests; does not run @on_setup."""
    return QuoteManager(module_context, repo)


class TestTypes:
    """Quote dataclass conversion, date formatting, and domain errors."""

    def test_builds_from_row(self) -> None:
        """Quote.from_row constructs a Quote with correct field values."""
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

    @pytest.mark.parametrize(
        ("created_at", "expected"),
        [
            pytest.param("2026-03-15T10:00:00+00:00", "2026-03-15", id="utc"),
            pytest.param(
                "2026-03-15T01:00:00+05:30",
                "2026-03-15",
                id="local-date-preserved-across-utc-boundary",
            ),
        ],
    )
    def test_date(self, created_at: str, expected: str) -> None:
        """Quote.date returns the local calendar date, not the UTC date."""
        quote = Quote(id=1, text="t", added_by="a", created_at=created_at)
        assert quote.date == expected

    def test_not_found_inherits_from_quote_error(self) -> None:
        """QuoteNotFoundError is a QuoteError."""
        assert issubclass(QuoteNotFoundError, QuoteError)

    def test_not_found_stores_quote_id(self) -> None:
        """QuoteNotFoundError stores the quote ID on .quote_id."""
        err = QuoteNotFoundError(42)
        assert err.quote_id == 42

    def test_not_found_str_contains_id(self) -> None:
        """QuoteNotFoundError's str contains the quote ID."""
        err = QuoteNotFoundError(42)
        assert "42" in str(err)


class TestRepository:
    """QuoteRepository CRUD behavior against real storage."""

    async def test_create_returns_quote(self, repo: QuoteRepository) -> None:
        """Create returns a populated Quote snapshot."""
        quote = await repo.create("Hello, world!", "tester")
        assert quote.id == 1
        assert quote.text == "Hello, world!"
        assert quote.added_by == "tester"
        assert quote.created_at is not None

    async def test_create_auto_increments(self, repo: QuoteRepository) -> None:
        """Sequential creates get sequential IDs."""
        q1 = await repo.create("First", "tester")
        q2 = await repo.create("Second", "tester")
        assert q2.id == q1.id + 1

    async def test_get_existing(self, repo: QuoteRepository) -> None:
        """Get returns the matching quote."""
        created = await repo.create("Test", "tester")
        fetched = await repo.get(created.id)
        assert fetched.id == created.id
        assert fetched.text == "Test"

    async def test_get_not_found(self, repo: QuoteRepository) -> None:
        """Get raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError) as exc_info:
            await repo.get(999)
        assert exc_info.value.quote_id == 999

    async def test_get_random_returns_quote(self, repo: QuoteRepository) -> None:
        """get_random returns the lone quote when the table has one row."""
        await repo.create("Only quote", "tester")
        quote = await repo.get_random()
        assert quote is not None
        assert quote.text == "Only quote"

    async def test_get_random_empty(self, repo: QuoteRepository) -> None:
        """get_random returns None when the table is empty."""
        assert await repo.get_random() is None

    async def test_delete_returns_quote(self, repo: QuoteRepository) -> None:
        """Delete returns the deleted snapshot."""
        created = await repo.create("Doomed", "tester")
        deleted = await repo.delete(created.id)
        assert deleted.id == created.id
        assert deleted.text == "Doomed"

    async def test_delete_removes_from_db(self, repo: QuoteRepository) -> None:
        """A deleted quote is no longer retrievable."""
        created = await repo.create("Doomed", "tester")
        await repo.delete(created.id)
        with pytest.raises(QuoteNotFoundError):
            await repo.get(created.id)

    async def test_delete_not_found(self, repo: QuoteRepository) -> None:
        """Delete raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError) as exc_info:
            await repo.delete(999)
        assert exc_info.value.quote_id == 999

    async def test_list_all(self, repo: QuoteRepository) -> None:
        """list_all returns quotes ordered by ID."""
        await repo.create("First", "alice")
        await repo.create("Second", "bob")
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].text == "First"
        assert result[1].text == "Second"

    async def test_list_all_empty(self, repo: QuoteRepository) -> None:
        """list_all returns an empty list when no quotes exist."""
        assert await repo.list_all() == []


class TestManager:
    """QuoteManager forwarding behavior and the get_manager helper."""

    async def test_add_quote(self, manager: QuoteManager) -> None:
        """add_quote returns a populated Quote with correct fields."""
        quote = await manager.add_quote("Test quote", "tester")
        assert quote.id == 1
        assert quote.text == "Test quote"
        assert quote.added_by == "tester"

    async def test_delete_quote(self, manager: QuoteManager) -> None:
        """delete_quote removes the quote and returns its snapshot."""
        created = await manager.add_quote("Doomed", "tester")
        deleted = await manager.delete_quote(created.id)
        assert deleted.id == created.id
        assert deleted.text == "Doomed"
        assert await manager.list_quotes() == []

    async def test_delete_quote_not_found(self, manager: QuoteManager) -> None:
        """delete_quote raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError):
            await manager.delete_quote(999)

    async def test_get_quote(self, manager: QuoteManager) -> None:
        """get_quote returns the matching quote."""
        created = await manager.add_quote("Test", "tester")
        fetched = await manager.get_quote(created.id)
        assert fetched.id == created.id
        assert fetched.text == "Test"

    async def test_get_quote_not_found(self, manager: QuoteManager) -> None:
        """get_quote raises QuoteNotFoundError for an unknown ID."""
        with pytest.raises(QuoteNotFoundError):
            await manager.get_quote(999)

    async def test_get_random_quote(self, manager: QuoteManager) -> None:
        """get_random_quote returns a quote when quotes exist."""
        await manager.add_quote("Only one", "tester")
        quote = await manager.get_random_quote()
        assert quote is not None
        assert quote.text == "Only one"

    async def test_get_random_quote_empty(self, manager: QuoteManager) -> None:
        """get_random_quote returns None when no quotes exist."""
        assert await manager.get_random_quote() is None

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

    def test_get_manager_returns_stored_manager(
        self, module_context: ModuleContext, repo: QuoteRepository
    ) -> None:
        """get_manager returns the QuoteManager stored in ctx.state."""
        mgr = QuoteManager(module_context, repo)
        module_context.state["manager"] = mgr
        assert get_manager(module_context) is mgr

    def test_get_manager_raises_if_not_initialized(
        self, module_context: ModuleContext
    ) -> None:
        """get_manager raises RuntimeError when manager is missing."""
        with pytest.raises(RuntimeError, match="QuoteManager is not initialized"):
            get_manager(module_context)


class TestModuleLifecycle:
    """@on_setup and @on_teardown hooks."""

    async def test_setup_creates_table_and_manager(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """Setup creates the quotes table and stashes a QuoteManager in state."""
        await setup(module_context)

        row = await storage.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='quotes'"
        )
        assert row is not None
        assert row["name"] == "quotes"

        mgr = module_context.state["manager"]
        assert isinstance(mgr, QuoteManager)

    async def test_teardown_clears_manager(self, module_context: ModuleContext) -> None:
        """Teardown clears the manager from state."""
        await setup(module_context)
        await teardown(module_context)
        assert module_context.state["manager"] is None


@pytest.mark.usefixtures("module_lifecycle")
class TestQuoteCommand:
    """!quote command handler."""

    async def test_quote_empty_db(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!quote with no stored quotes reports empty."""
        event = make_chat_event(raw_body="!quote")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert (
            owncast_client.calls[0].kwargs["body"] == "No quotes have been added yet."
        )

    async def test_quote_random(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!quote with no ID returns a random quote."""
        await get_manager(module_context).add_quote("The only one", "tester")
        event = make_chat_event(raw_body="!quote")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == '"The only one" (#1)'

    async def test_quote_by_id(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!quote <id> returns the formatted detail for that quote."""
        seeded = await get_manager(module_context).add_quote("Hello", "ModUser")
        event = make_chat_event(raw_body="!quote 1")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == (
            f'Quote #1: "Hello" - Added by ModUser on {seeded.date}'
        )

    async def test_quote_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!quote <unknown-id> reports not found."""
        event = make_chat_event(raw_body="!quote 999")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Quote #999 not found."

    async def test_quote_invalid_id(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!quote <non-int> sends usage."""
        event = make_chat_event(raw_body="!quote abc")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Usage: !quote [id]"


@pytest.mark.usefixtures("module_lifecycle")
class TestAddQuoteCommand:
    """!addquote command handler."""

    async def test_addquote_attributes_to_user(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addquote creates the quote attributed to the moderator's display name."""
        user = make_user(display_name="ModUser", is_moderator=True)
        event = make_chat_event(raw_body="!addquote Hello there", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].kwargs["body"] == "Quote #1 added."

        stored = await get_manager(module_context).list_quotes()
        assert len(stored) == 1
        assert stored[0].text == "Hello there"
        assert stored[0].added_by == "ModUser"

    async def test_addquote_no_text(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addquote with no text sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addquote", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Usage: !addquote <quote text>"


@pytest.mark.usefixtures("module_lifecycle")
class TestDeleteQuoteCommand:
    """!deletequote command handler."""

    async def test_deletequote_removes(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletequote <id> removes the quote and confirms."""
        manager = get_manager(module_context)
        await manager.add_quote("Doomed", "tester")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletequote 1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].kwargs["body"] == "Quote #1 deleted."
        assert await manager.list_quotes() == []

    async def test_deletequote_no_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletequote with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletequote", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Usage: !deletequote <id>"

    async def test_deletequote_invalid_id(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletequote <non-int> sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletequote abc", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Usage: !deletequote <id>"

    async def test_deletequote_not_found(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletequote <unknown-id> reports not found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletequote 999", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].kwargs["body"] == "Quote #999 not found."


@pytest.mark.usefixtures("module_lifecycle")
class TestListQuotesCommand:
    """!listquotes command handler."""

    async def test_listquotes_sends_url(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!listquotes sends the public URL of the /list route."""
        event = make_chat_event(raw_body="!listquotes")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert (
            owncast_client.calls[0].kwargs["body"]
            == "Quotes: http://localhost:8081/owlbot/quotes/list"
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestQuotesListRoute:
    """/list HTML route."""

    async def test_list_empty(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /list with no quotes serves the empty-state message."""
        resp = await route_client.get("/owlbot/quotes/list")
        assert resp.status == 200
        assert resp.content_type == "text/html"
        body = await resp.text()
        assert "No quotes have been added yet." in body

    async def test_list_populated(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /list with quotes includes each quote's text and attribution."""
        manager = get_manager(module_context)
        await manager.add_quote("First quote", "alice")
        await manager.add_quote("Second quote", "bob")

        resp = await route_client.get("/owlbot/quotes/list")
        assert resp.status == 200
        body = await resp.text()
        assert "First quote" in body
        assert "Second quote" in body
        assert "alice" in body
        assert "bob" in body


@pytest.mark.usefixtures("module_lifecycle")
class TestModeratorPermissions:
    """Moderator-required commands deny non-moderators."""

    @pytest.mark.parametrize("command", ["addquote", "deletequote"])
    async def test_non_moderator_denied(
        self,
        command: str,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Non-moderator invocation triggers the dispatcher denial."""
        user = make_user(is_moderator=False)
        event = make_chat_event(raw_body=f"!{command}", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        denials = [
            c.kwargs["body"]
            for c in owncast_client.calls
            if c.name == "send_system_message_to_client"
        ]
        assert denials == [f"Only moderators can use !{command}."]
