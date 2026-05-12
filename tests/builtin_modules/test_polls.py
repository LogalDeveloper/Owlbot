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

"""Tests for the polls module."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import orjson
import pytest

from owlbot.api.event_types import EventType
from owlbot.builtin_modules import polls as polls_pkg
from owlbot.builtin_modules.polls import setup, teardown
from owlbot.builtin_modules.polls.manager import PollManager, get_manager
from owlbot.builtin_modules.polls.repository import PollRepository
from owlbot.builtin_modules.polls.sse import (
    PollSSEConnection,
    PollSSERegistry,
    SSEEvent,
    sse_payload,
)
from owlbot.builtin_modules.polls.types import (
    MAX_DURATION,
    MAX_OPTION_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTION_LENGTH,
    MIN_DURATION,
    REMINDER_THRESHOLD,
    ActivePoll,
    CompletedPoll,
    DuplicateVoteError,
    NoActivePollError,
    PollAlreadyActiveError,
    PollAuthenticationRequiredError,
    PollCreateValidationError,
    PollError,
    PollResults,
    PollStateError,
    PollVoteError,
    PollVoteValidationError,
    complete_poll,
    compute_results,
)
from owlbot.sessions import SESSION_COOKIE_NAME
from owlbot.testing import (
    make_chat_event,
    make_stream_started_event,
    make_stream_stopped_event,
    make_user,
    make_user_joined_event,
)

if TYPE_CHECKING:
    from types import ModuleType
    from typing import Any, NoReturn

    from aiohttp import web
    from aiohttp.test_utils import TestClient

    from owlbot.api.context import ModuleContext
    from owlbot.api.event_types import User
    from owlbot.registries.events import EventDispatcher
    from owlbot.sessions import SessionManager
    from owlbot.testing import RecordingOwncastClient


_REAL_ASYNCIO_SLEEP = asyncio.sleep


@pytest.fixture
def module_pkg() -> ModuleType:
    """Auto-wire module_name, templates, and handler registration for polls."""
    return polls_pkg


@pytest.fixture
def repo() -> PollRepository:
    """PollRepository is memory-only and needs no async setup."""
    return PollRepository()


@pytest.fixture
def manager(module_context: ModuleContext, repo: PollRepository) -> PollManager:
    """PollManager built directly for unit tests; does not run @on_setup."""
    return PollManager(module_context, repo)


@pytest.fixture
def _suppress_poll_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid long-lived auto-end timer tasks in tests that create polls."""
    monkeypatch.setattr(PollManager, "_start_poll_timer", lambda _self, _poll: None)


class _FakeStreamResponse:
    """Minimal StreamResponse stand-in that records SSE writes."""

    def __init__(self, *, fail: bool = False, fail_after: int | None = None) -> None:
        self.written: list[bytes] = []
        self.eof: bool = False
        self._fail = fail
        self._fail_after = fail_after

    async def write(self, data: bytes) -> None:
        """Record bytes or raise a transport-like error."""
        if self._fail or (
            self._fail_after is not None and len(self.written) >= self._fail_after
        ):
            raise ConnectionResetError("client disconnected")
        self.written.append(data)

    async def write_eof(self) -> None:
        """Record that the response was closed."""
        self.eof = True


async def _connect_sse_client(
    manager: PollManager,
    user: User,
    response: _FakeStreamResponse,
) -> PollSSEConnection:
    """Register an SSE client and ensure the connection succeeded."""
    poll = manager.active_poll
    assert poll is not None

    connection = manager._sse.register(
        user,
        response=response,  # type: ignore[arg-type]
    )
    if user.is_moderator or not poll.hidden:
        sent = await manager._sse.send(
            connection,
            SSEEvent.TALLY,
            manager._live_tally_sse_payload(poll),
        )
        assert sent is True
    return connection


def _active_poll(
    *,
    question: str = "Best color?",
    options: list[str] | None = None,
    hidden: bool = False,
    requires_auth: bool = False,
    allow_multiple: bool = False,
    min_selections: int | None = None,
    max_selections: int | None = None,
    duration: int = MIN_DURATION,
) -> ActivePoll:
    """Build an ActivePoll with sensible defaults."""
    return ActivePoll(
        question=question,
        options=options if options is not None else ["Red", "Blue"],
        hidden=hidden,
        requires_auth=requires_auth,
        allow_multiple=allow_multiple,
        min_selections=min_selections,
        max_selections=max_selections,
        duration=duration,
        created_at=datetime(2026, 5, 5, tzinfo=UTC),
    )


def _completed_poll() -> CompletedPoll:
    """Build a minimal completed poll snapshot."""
    return CompletedPoll(
        question="Done?",
        options=("Yes", "No"),
        results=PollResults(
            counts=(1, 0),
            winners=("Yes",),
            is_tie=False,
            total_votes=1,
        ),
        created_at=datetime(2026, 5, 5, 12, tzinfo=UTC),
        ended_at=datetime(2026, 5, 5, 12, 5, tzinfo=UTC),
    )


async def _create_poll(
    manager: PollManager,
    *,
    question: str = "Best color?",
    options: list[str] | None = None,
    hidden: bool = False,
    requires_auth: bool = False,
    allow_multiple: bool = False,
    min_selections: int | None = None,
    max_selections: int | None = None,
    duration: int = MIN_DURATION,
) -> ActivePoll:
    """Create a poll through PollManager with test defaults."""
    return await manager.create_poll(
        question=question,
        options=options if options is not None else ["Red", "Blue"],
        hidden=hidden,
        requires_auth=requires_auth,
        allow_multiple=allow_multiple,
        min_selections=min_selections,
        max_selections=max_selections,
        duration=duration,
    )


def _session_headers(
    session_manager: SessionManager,
    *,
    user: User,
) -> dict[str, str]:
    """Return a Cookie header for a newly created browser session."""
    session = session_manager.create_session(user)
    return {"Cookie": f"{SESSION_COOKIE_NAME}={session.session_id}"}


def _private_bodies(client: RecordingOwncastClient) -> list[str]:
    """Return private system-message bodies in call order."""
    return [
        call.kwargs["body"]
        for call in client.calls
        if call.name == "send_system_message_to_client"
    ]


def _href_from_body(body: str) -> str:
    """Extract the first href value from a rendered test message."""
    return body.split('href="', 1)[1].split('"', 1)[0]


class TestTypes:
    """Poll domain data, SSE serialization, and errors."""

    @pytest.mark.parametrize(
        (
            "votes",
            "expected_counts",
            "expected_winners",
            "expected_is_tie",
            "total",
        ),
        [
            pytest.param({}, (0, 0), (), False, 0, id="no-votes"),
            pytest.param(
                {"u1": {0}, "u2": {1}, "u3": {1}},
                (1, 2),
                ("Blue",),
                False,
                3,
                id="winner",
            ),
            pytest.param(
                {"u1": {0}, "u2": {1}},
                (1, 1),
                ("Red", "Blue"),
                True,
                2,
                id="tie",
            ),
            pytest.param(
                {"u1": {0, 2}, "u2": {2}},
                (1, 0, 2),
                ("Green",),
                False,
                2,
                id="multi-select",
            ),
        ],
    )
    def test_compute_results(
        self,
        votes: dict[str, set[int]],
        expected_counts: tuple[int, ...],
        expected_winners: tuple[str, ...],
        expected_is_tie: object,
        total: int,
    ) -> None:
        """compute_results returns counts, winners, tie state, and voter total."""
        result = compute_results(
            ["Red", "Blue", "Green"][: len(expected_counts)],
            votes,
        )
        assert result.counts == expected_counts
        assert result.winners == expected_winners
        assert result.is_tie is expected_is_tie
        assert result.total_votes == total

    def test_poll_results_are_immutable(self) -> None:
        """PollResults instances cannot be mutated after construction."""
        result = compute_results(["Red", "Blue"], {"u1": {0}})

        with pytest.raises(FrozenInstanceError):
            result.total_votes = 99  # type: ignore[misc]
        assert not hasattr(result.counts, "append")

    def test_complete_poll_builds_display_ready_snapshot(self) -> None:
        """complete_poll returns a completed snapshot with sorted options."""
        ended_at = datetime(2026, 1, 1, tzinfo=UTC)
        poll = _active_poll(options=["Zed", "Alpha", "Beta"])
        poll.votes = {"u1": {0}, "u2": {1}, "u3": {1}}

        result = complete_poll(poll, ended_at)

        assert result.question == poll.question
        assert result.options == ("Alpha", "Zed", "Beta")
        assert result.results.counts == (2, 1, 0)
        assert result.results.winners == ("Alpha",)
        assert result.created_at == poll.created_at
        assert result.ended_at == ended_at

    def test_sse_payload_serializes_event_frame(self) -> None:
        """sse_payload returns an SSE frame with compact JSON data."""
        payload = sse_payload(SSEEvent.TALLY, {"counts": [1, 0], "total_votes": 1})
        assert payload.startswith(b"event: tally\n")
        data_line = payload.split(b"\n")[1]
        assert orjson.loads(data_line.removeprefix(b"data: ")) == {
            "counts": [1, 0],
            "total_votes": 1,
        }

    def test_sse_registry_register_does_not_write(self) -> None:
        """SSE registration tracks the response without sending data."""
        registry = PollSSERegistry(logging.getLogger("owlbot.tests.polls"))
        response = _FakeStreamResponse()

        connection = registry.register(
            make_user(id="viewer"),
            response=response,  # type: ignore[arg-type]
        )

        assert isinstance(connection, PollSSEConnection)
        assert response.written == []
        assert len(registry) == 1

    async def test_sse_send_returns_false_for_unregistered_connection(self) -> None:
        """Send returns False when the connection handle is no longer tracked."""
        registry = PollSSERegistry(logging.getLogger("owlbot.tests.polls"))
        response = _FakeStreamResponse()
        connection = registry.register(
            make_user(id="viewer"),
            response=response,  # type: ignore[arg-type]
        )
        registry.unregister(connection)

        sent = await registry.send(connection, SSEEvent.KEEPALIVE, {})

        assert sent is False
        assert response.written == []

    @pytest.mark.parametrize(
        "error_cls",
        [
            pytest.param(PollAlreadyActiveError, id="already-active"),
            pytest.param(NoActivePollError, id="no-active"),
            pytest.param(PollCreateValidationError, id="create-validation"),
            pytest.param(PollVoteValidationError, id="vote-validation"),
            pytest.param(PollAuthenticationRequiredError, id="auth"),
            pytest.param(DuplicateVoteError, id="duplicate"),
        ],
    )
    def test_error_subclasses_poll_error(self, error_cls: type[Exception]) -> None:
        """Every public polls domain error subclasses PollError."""
        assert issubclass(error_cls, PollError)

    def test_state_errors_share_base(self) -> None:
        """Poll lifecycle errors are part of the state-error branch."""
        assert issubclass(PollAlreadyActiveError, PollStateError)
        assert issubclass(NoActivePollError, PollStateError)

    def test_validation_errors_are_separated_by_flow(self) -> None:
        """Create and vote validation errors belong to their own flows."""
        assert issubclass(PollCreateValidationError, PollError)
        assert not issubclass(PollCreateValidationError, PollVoteError)
        assert issubclass(PollVoteValidationError, PollVoteError)

    def test_vote_errors_store_user_context(self) -> None:
        """Vote errors retain the affected Owncast user ID."""
        auth = PollAuthenticationRequiredError("u1")
        duplicate = DuplicateVoteError("u2")
        assert auth.user_id == "u1"
        assert duplicate.user_id == "u2"
        assert isinstance(auth, PollVoteError)
        assert isinstance(duplicate, PollVoteError)


class TestRepository:
    """PollRepository memory lifecycle."""

    def test_active_poll_lifecycle(self, repo: PollRepository) -> None:
        """An active poll can be created, read, and cleared."""
        poll = _active_poll()
        assert repo.get_active_poll() is None
        assert repo.create_active_poll(poll) is poll
        assert repo.get_active_poll() is poll
        assert repo.pop_active_poll() is poll
        assert repo.get_active_poll() is None

    def test_duplicate_active_guard(self, repo: PollRepository) -> None:
        """Creating a second active poll raises the state error."""
        repo.create_active_poll(_active_poll())
        with pytest.raises(PollAlreadyActiveError, match="already active"):
            repo.create_active_poll(_active_poll(question="Other?"))

    def test_pop_active_poll_without_active_poll(self, repo: PollRepository) -> None:
        """Clearing an empty repository is a no-op."""
        assert repo.pop_active_poll() is None

    def test_vote_storage_and_lookup(self, repo: PollRepository) -> None:
        """Votes are stored and returned as defensive copies."""
        repo.create_active_poll(_active_poll())
        repo.set_vote("u1", {0})
        vote = repo.get_vote("u1")
        assert vote == {0}
        assert repo.list_votes() == {"u1": {0}}
        assert vote is not None
        vote.add(1)
        assert repo.get_vote("u1") == {0}

    def test_duplicate_vote_guard(self, repo: PollRepository) -> None:
        """set_vote rejects duplicate voters."""
        repo.create_active_poll(_active_poll())
        repo.set_vote("u1", {0})
        with pytest.raises(DuplicateVoteError) as exc_info:
            repo.set_vote("u1", {1})
        assert exc_info.value.user_id == "u1"

    def test_vote_operations_require_active_poll(self, repo: PollRepository) -> None:
        """Vote APIs raise when no poll is active."""
        with pytest.raises(NoActivePollError):
            repo.set_vote("u1", {0})
        with pytest.raises(NoActivePollError):
            repo.get_vote("u1")
        with pytest.raises(NoActivePollError):
            repo.list_votes()

    def test_last_result_lifecycle(self, repo: PollRepository) -> None:
        """The last completed result can be stored and cleared."""
        result = _completed_poll()
        assert repo.get_last_result() is None
        repo.set_last_result(result)
        assert repo.get_last_result() is result
        repo.clear_last_result()
        assert repo.get_last_result() is None


@pytest.mark.usefixtures("_suppress_poll_timers")
class TestManager:
    """PollManager business rules and runtime lifecycle."""

    async def test_create_poll_announces_and_stores(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """create_poll validates, stores active state, and announces in chat."""
        poll = await _create_poll(manager, options=["  Red  ", "Blue"])
        assert manager.active_poll is poll
        assert poll.options == ["Red", "Blue"]
        assert owncast_client.calls[0].name == "send_system_message"
        assert "Poll: Best color?" in owncast_client.calls[0].kwargs["body"]
        assert owncast_client.calls[0].kwargs["unsanitized"] is True

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            pytest.param({"question": ""}, "Question is required", id="empty-question"),
            pytest.param(
                {"question": "x" * (MAX_QUESTION_LENGTH + 1)},
                "Question must be",
                id="question-too-long",
            ),
            pytest.param(
                {"options": ["Only"]},
                "At least 2 options",
                id="too-few-options",
            ),
            pytest.param(
                {"options": [f"Option {i}" for i in range(MAX_OPTIONS + 1)]},
                "Maximum",
                id="too-many-options",
            ),
            pytest.param(
                {"options": ["Red", ""]},
                "Option 2 is empty",
                id="empty-option",
            ),
            pytest.param(
                {"options": ["Red", "x" * (MAX_OPTION_LENGTH + 1)]},
                "character limit",
                id="option-too-long",
            ),
            pytest.param(
                {"options": ["Red", "Red"]},
                "Duplicate options",
                id="duplicate-option",
            ),
            pytest.param(
                {"duration": MIN_DURATION - 1},
                "Duration must be",
                id="duration-low",
            ),
            pytest.param(
                {"duration": MAX_DURATION + 1},
                "Duration must be",
                id="duration-high",
            ),
            pytest.param(
                {"allow_multiple": True, "min_selections": 0},
                "Minimum selections must be",
                id="min-below-one",
            ),
            pytest.param(
                {"allow_multiple": True, "max_selections": 0},
                "Maximum selections must be",
                id="max-below-one",
            ),
            pytest.param(
                {"allow_multiple": True, "min_selections": 3},
                "Minimum selections",
                id="min-exceeds-options",
            ),
            pytest.param(
                {"allow_multiple": True, "max_selections": 3},
                "Maximum selections",
                id="max-exceeds-options",
            ),
            pytest.param(
                {"allow_multiple": True, "min_selections": 2, "max_selections": 1},
                "Minimum selections cannot exceed",
                id="min-greater-than-max",
            ),
        ],
    )
    async def test_create_validation(
        self,
        manager: PollManager,
        kwargs: dict[str, object],
        match: str,
    ) -> None:
        """Invalid create requests raise a validation error with useful text."""
        with pytest.raises(PollCreateValidationError, match=match):
            await _create_poll(manager, **kwargs)  # type: ignore[arg-type]

    async def test_create_poll_announces_long_duration_in_minutes(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Long polls announce their duration in minutes."""
        await _create_poll(manager, duration=REMINDER_THRESHOLD * 2)
        body = owncast_client.calls[0].kwargs["body"]
        assert "Voting ends in 2 minutes." in body

    async def test_get_selections(self, manager: PollManager) -> None:
        """get_selections reports stored vote state for page rendering."""
        await _create_poll(manager)
        guest = make_user(id="guest", is_authenticated=False)
        auth_user = make_user(id="auth", is_authenticated=True)

        assert manager.get_selections(guest) is None

        await manager.record_vote(auth_user, {0})
        assert manager.get_selections(auth_user) == frozenset({0})

    async def test_record_vote_auth_required(self, manager: PollManager) -> None:
        """Auth-only polls reject unauthenticated voters."""
        await _create_poll(manager, requires_auth=True)
        with pytest.raises(PollAuthenticationRequiredError) as exc_info:
            await manager.record_vote(make_user(id="guest"), {0})
        assert exc_info.value.user_id == "guest"

    @pytest.mark.parametrize(
        ("poll_kwargs", "selections", "match"),
        [
            pytest.param({}, set(), "No options selected", id="empty"),
            pytest.param({}, {2}, "out of range", id="out-of-range"),
            pytest.param({}, {0, 1}, "Only one selection", id="single-select-multiple"),
            pytest.param(
                {"allow_multiple": True, "min_selections": 2},
                {0},
                "At least 2 selections",
                id="below-min",
            ),
            pytest.param(
                {"allow_multiple": True, "max_selections": 1},
                {0, 1},
                "At most 1 selections",
                id="above-max",
            ),
        ],
    )
    async def test_record_vote_selection_rules(
        self,
        manager: PollManager,
        poll_kwargs: dict[str, object],
        selections: set[int],
        match: str,
    ) -> None:
        """record_vote enforces selection bounds and limits."""
        await _create_poll(manager, **poll_kwargs)  # type: ignore[arg-type]
        with pytest.raises(PollVoteValidationError, match=match):
            await manager.record_vote(make_user(id="u1"), selections)

    async def test_record_vote_duplicate(self, manager: PollManager) -> None:
        """A user can vote only once."""
        user = make_user(id="u1")
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        with pytest.raises(DuplicateVoteError):
            await manager.record_vote(user, {1})

    async def test_hidden_tally_only_sent_to_moderators(
        self,
        manager: PollManager,
    ) -> None:
        """Hidden polls broadcast tally updates only to moderator SSE clients."""
        await _create_poll(manager, hidden=True)
        viewer = _FakeStreamResponse()
        moderator = _FakeStreamResponse()
        await _connect_sse_client(
            manager,
            make_user(id="viewer"),
            viewer,
        )
        await _connect_sse_client(
            manager,
            make_user(id="mod", is_moderator=True),
            moderator,
        )

        await manager.record_vote(make_user(id="voter"), {0})

        assert viewer.written == []
        assert len(moderator.written) == 2
        assert b"event: tally" in moderator.written[-1]

    async def test_public_tally_sent_to_all_clients(self, manager: PollManager) -> None:
        """Non-hidden polls send initial and update tallies to every client."""
        await _create_poll(manager)
        c1 = _FakeStreamResponse()
        c2 = _FakeStreamResponse()
        await _connect_sse_client(manager, make_user(id="u1"), c1)
        await _connect_sse_client(manager, make_user(id="u2"), c2)

        await manager.record_vote(make_user(id="voter"), {1})

        assert len(c1.written) == 2
        assert len(c2.written) == 2
        assert b'"counts":[0,1]' in c1.written[-1]

    async def test_public_tally_keeps_multiple_clients_for_same_user(
        self,
        manager: PollManager,
    ) -> None:
        """Multiple SSE connections for one user are maintained independently."""
        await _create_poll(manager)
        c1 = _FakeStreamResponse()
        c2 = _FakeStreamResponse()
        user = make_user(id="viewer")
        await _connect_sse_client(manager, user, c1)
        await _connect_sse_client(manager, user, c2)

        await manager.record_vote(make_user(id="voter"), {1})

        assert len(c1.written) == 2
        assert len(c2.written) == 2
        assert b'"counts":[0,1]' in c1.written[-1]
        assert b'"counts":[0,1]' in c2.written[-1]

    async def test_unregister_sse_connection_only_removes_matching_connection(
        self,
        manager: PollManager,
    ) -> None:
        """Unregistering one handle keeps other same-user streams alive."""
        await _create_poll(manager)
        c1 = _FakeStreamResponse()
        c2 = _FakeStreamResponse()
        user = make_user(id="viewer")
        connection = await _connect_sse_client(manager, user, c1)
        await _connect_sse_client(manager, user, c2)

        manager._sse.unregister(connection)
        await manager.record_vote(make_user(id="voter"), {1})

        assert len(c1.written) == 1
        assert len(c2.written) == 2
        assert b'"counts":[0,1]' in c2.written[-1]

    async def test_initial_sse_tally_write_failure_is_not_retained(
        self,
        manager: PollManager,
    ) -> None:
        """Failed initial SSE writes are not retained as active clients."""
        await _create_poll(manager)
        client = _FakeStreamResponse(fail=True)
        user = make_user(id="viewer")

        await manager.serve_sse_stream(user, client)  # type: ignore[arg-type]

        assert len(manager._sse) == 0

    async def test_failed_sse_broadcast_removes_client(
        self,
        manager: PollManager,
    ) -> None:
        """Failed SSE writes remove disconnected clients from future broadcasts."""
        await _create_poll(manager)
        client = _FakeStreamResponse(fail_after=1)
        other_client = _FakeStreamResponse()
        user = make_user(id="viewer")
        await _connect_sse_client(manager, user, client)
        await _connect_sse_client(manager, user, other_client)

        await manager.record_vote(make_user(id="u1"), {0})
        await manager.record_vote(make_user(id="u2"), {1})

        assert len(client.written) == 1
        assert len(other_client.written) == 3
        assert b'"counts":[1,1]' in other_client.written[-1]

    async def test_serve_sse_stream_keepalive_failure_unregisters_client(
        self,
        manager: PollManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSE stream keepalive transport failures unregister the client."""
        await _create_poll(manager)
        client = _FakeStreamResponse(fail_after=1)

        async def wait_for(awaitable: Any, **_kwargs: object) -> None:
            awaitable.close()

        monkeypatch.setattr(asyncio, "wait_for", wait_for)

        await manager.serve_sse_stream(make_user(id="viewer"), client)  # type: ignore[arg-type]
        await manager.record_vote(make_user(id="u1"), {0})

        assert len(client.written) == 1

    async def test_serve_sse_stream_exits_when_poll_ends_during_wait(
        self,
        manager: PollManager,
        repo: PollRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSE streams exit without keepalive when the active poll changes."""
        await _create_poll(manager)
        client = _FakeStreamResponse()

        async def wait_for(awaitable: Any, **_kwargs: object) -> None:
            awaitable.close()
            repo.pop_active_poll()

        monkeypatch.setattr(asyncio, "wait_for", wait_for)

        await manager.serve_sse_stream(make_user(id="viewer"), client)  # type: ignore[arg-type]

        assert len(client.written) == 1

    async def test_end_sorts_results_and_closes_clients(
        self,
        manager: PollManager,
    ) -> None:
        """End stores display-ready sorted results and closes SSE responses."""
        await _create_poll(manager, options=["Zed", "Alpha", "Beta"])
        client = _FakeStreamResponse()
        await _connect_sse_client(manager, make_user(id="viewer"), client)
        await manager.record_vote(make_user(id="u1"), {0})
        await manager.record_vote(make_user(id="u2"), {1})
        await manager.record_vote(make_user(id="u3"), {1})

        await manager.end()

        result = manager.last_result
        assert manager.active_poll is None
        assert result is not None
        assert result.options == ("Alpha", "Zed", "Beta")
        assert result.results.counts == (2, 1, 0)
        assert result.results.winners == ("Alpha",)
        assert client.eof is True

    async def test_end_announces_tie(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Ending a tied poll announces the tied options."""
        await _create_poll(manager)
        await manager.record_vote(make_user(id="u1"), {0})
        await manager.record_vote(make_user(id="u2"), {1})

        await manager.end()

        assert (
            "It's a tie between: Red, Blue" in owncast_client.calls[-1].kwargs["body"]
        )

    async def test_create_waits_for_previous_end_cleanup(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A new poll cannot replace SSE state while the old poll is ending."""
        await _create_poll(manager)
        old_client = _FakeStreamResponse()
        await _connect_sse_client(manager, make_user(id="viewer"), old_client)

        end_announcement_started = asyncio.Event()
        release_end_announcement = asyncio.Event()

        async def send_system_message(
            body: str,
            *,
            unsanitized: bool = False,
        ) -> str:
            if "Poll ended:" in body:
                end_announcement_started.set()
                await release_end_announcement.wait()
            return ""

        monkeypatch.setattr(
            owncast_client,
            "send_system_message",
            send_system_message,
        )

        end_task = asyncio.create_task(manager.end())
        create_task: asyncio.Task[ActivePoll] | None = None

        try:
            await asyncio.wait_for(end_announcement_started.wait(), timeout=1)
            assert manager.active_poll is None

            create_task = asyncio.create_task(
                _create_poll(manager, question="Next poll?")
            )
            await _REAL_ASYNCIO_SLEEP(0)

            assert create_task.done() is False
            assert manager.active_poll is None
            assert old_client.eof is True
            assert b"event: poll_ended" in old_client.written[-1]

            release_end_announcement.set()
            new_poll = await asyncio.wait_for(create_task, timeout=1)
            await asyncio.wait_for(end_task, timeout=1)

            assert old_client.eof is True
            assert new_poll.question == "Next poll?"
            assert manager.active_poll is new_poll
        finally:
            release_end_announcement.set()
            tasks: list[asyncio.Task[Any]] = [end_task]
            if create_task is not None:
                tasks.append(create_task)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancel_clears_active_without_result(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Cancel clears active state, avoids result storage, and announces."""
        await _create_poll(manager)
        await manager.cancel()
        assert manager.active_poll is None
        assert manager.last_result is None
        assert "Poll cancelled" in owncast_client.calls[-1].kwargs["body"]

    @pytest.mark.usefixtures("fast_sleep")
    async def test_stream_grace_cancels_active_poll(self, manager: PollManager) -> None:
        """The stream-stop grace task cancels the active poll after its delay."""
        await _create_poll(manager)
        manager.begin_stream_grace()
        await _REAL_ASYNCIO_SLEEP(0.02)
        assert manager.active_poll is None

    @pytest.mark.usefixtures("fast_sleep")
    async def test_result_expiry_clears_last_result(self, manager: PollManager) -> None:
        """The result expiry task clears the last completed result."""
        await _create_poll(manager)
        await manager.record_vote(make_user(id="u1"), {0})
        await manager.end()
        await _REAL_ASYNCIO_SLEEP(0.02)
        assert manager.last_result is None

    async def test_teardown_cancels_active_and_closes_clients(
        self,
        manager: PollManager,
    ) -> None:
        """Teardown cancels active state and closes connected SSE clients."""
        await _create_poll(manager)
        client = _FakeStreamResponse()
        await _connect_sse_client(manager, make_user(id="viewer"), client)
        await manager.teardown()
        assert manager.active_poll is None
        assert client.eof is True

    def test_get_manager_returns_stored_manager(
        self,
        module_context: ModuleContext,
        repo: PollRepository,
    ) -> None:
        """get_manager returns the PollManager stored in ctx.state."""
        mgr = PollManager(module_context, repo)
        module_context.state["manager"] = mgr
        assert get_manager(module_context) is mgr

    def test_get_manager_raises_if_not_initialized(
        self,
        module_context: ModuleContext,
    ) -> None:
        """get_manager raises RuntimeError when manager is missing."""
        with pytest.raises(RuntimeError, match="PollManager is not initialized"):
            get_manager(module_context)


class TestManagerTimers:
    """PollManager timer task behavior without timer suppression."""

    async def test_manual_end_cancels_running_timer(
        self,
        manager: PollManager,
    ) -> None:
        """Manual end cancels the background poll timer task."""
        await _create_poll(manager)
        try:
            await manager.end()
            assert manager.active_poll is None
        finally:
            await manager.teardown()

    @pytest.mark.usefixtures("fast_sleep")
    async def test_poll_timer_sends_reminder_and_auto_ends(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Long poll timers send a reminder before auto-ending the poll."""
        await _create_poll(manager, duration=REMINDER_THRESHOLD + MIN_DURATION)
        await _REAL_ASYNCIO_SLEEP(0.02)

        bodies = [
            call.kwargs["body"]
            for call in owncast_client.calls
            if call.name == "send_system_message"
        ]
        assert any("seconds remaining" in body for body in bodies)
        assert any("Poll ended:" in body for body in bodies)
        assert manager.active_poll is None

    @pytest.mark.usefixtures("fast_sleep")
    async def test_short_poll_timer_auto_ends_without_reminder(
        self,
        manager: PollManager,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Short poll timers skip the reminder and still auto-end."""
        await _create_poll(manager, duration=MIN_DURATION)
        await _REAL_ASYNCIO_SLEEP(0.02)

        bodies = [
            call.kwargs["body"]
            for call in owncast_client.calls
            if call.name == "send_system_message"
        ]
        assert not any("seconds remaining" in body for body in bodies)
        assert any("Poll ended:" in body for body in bodies)
        assert manager.active_poll is None


@pytest.mark.usefixtures("_suppress_poll_timers")
class TestModuleLifecycle:
    """@on_setup and @on_teardown hooks."""

    async def test_setup_stashes_manager(self, module_context: ModuleContext) -> None:
        """Setup stores a PollManager in module state."""
        await setup(module_context)
        assert isinstance(module_context.state["manager"], PollManager)
        await teardown(module_context)

    async def test_teardown_clears_manager(self, module_context: ModuleContext) -> None:
        """Teardown runs manager cleanup and clears state."""
        await setup(module_context)
        await teardown(module_context)
        assert module_context.state["manager"] is None


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestPollCommand:
    """!poll command behavior."""

    async def test_poll_command_sends_create_link(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!poll sends moderators a protected create link."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!poll", user=user, client_id=42)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == f'<a href="{url}"><u>Click here to set up a poll</u></a>.'

    async def test_poll_command_reports_active_conflict(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!poll reports an active-poll conflict with command-owned text."""
        await _create_poll(get_manager(module_context))
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!poll", user=user, client_id=42)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _private_bodies(owncast_client)[0] == "A poll is already active."


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestLifecycleCommands:
    """!endpoll and !cancelpoll command behavior."""

    async def test_endpoll_and_cancelpoll(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """Moderator lifecycle commands delegate to manager end/cancel."""
        manager = get_manager(module_context)
        mod = make_user(is_moderator=True)

        await _create_poll(manager)
        await event_dispatcher.dispatch(
            EventType.CHAT,
            make_chat_event(raw_body="!endpoll", user=mod),
        )
        assert manager.active_poll is None
        assert manager.last_result is not None

        await _create_poll(manager)
        await event_dispatcher.dispatch(
            EventType.CHAT,
            make_chat_event(raw_body="!cancelpoll", user=mod),
        )
        assert manager.active_poll is None

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("!endpoll", id="end"),
            pytest.param("!cancelpoll", id="cancel"),
        ],
    )
    async def test_lifecycle_commands_report_no_active_poll(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        command: str,
    ) -> None:
        """Lifecycle commands map missing state to command-owned text."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body=command, user=user, client_id=42)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _private_bodies(owncast_client)[0] == "There is no active poll."


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestVoteCommand:
    """!vote command behavior."""

    async def test_vote_command_no_active_poll(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!vote hides the command and reports when no poll is active."""
        event = make_chat_event(raw_body="!vote", message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].name == "set_message_visibility"
        assert owncast_client.calls[0].kwargs["message_ids"] == ["m1"]
        assert _private_bodies(owncast_client)[0] == "There is no active poll."

    async def test_vote_command_without_args_sends_voting_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!vote during an active poll hides the command and sends the vote page."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="!vote", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        assert owncast_client.calls[0].kwargs["message_ids"] == ["m1"]
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == f'<a href="{url}"><u>Click here to cast your vote</u></a>.'
        assert manager.get_selections(user) is None

    async def test_vote_command_without_args_auth_required_sends_voting_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!vote sends the voting link without prechecking auth."""
        manager = get_manager(module_context)
        user = make_user(id="guest", is_authenticated=False)
        await _create_poll(manager, requires_auth=True)
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="!vote", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == f'<a href="{url}"><u>Click here to cast your vote</u></a>.'
        assert manager.get_selections(user) is None

    async def test_vote_command_without_args_already_voted_sends_voting_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!vote sends the voting link without prechecking duplicate state."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="!vote", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == f'<a href="{url}"><u>Click here to cast your vote</u></a>.'
        assert manager.get_selections(user) == frozenset({0})

    async def test_vote_command_auth_required_sends_results_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """!vote <number> reports auth failures from record_vote."""
        manager = get_manager(module_context)
        user = make_user(id="guest", is_authenticated=False)
        await _create_poll(manager, requires_auth=True)
        owncast_client.calls.clear()

        def get_selections(_user: User) -> NoReturn:
            pytest.fail("valid quick votes should defer to record_vote")

        monkeypatch.setattr(manager, "get_selections", get_selections)

        event = make_chat_event(raw_body="!vote 1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "This poll requires an authenticated account to vote. "
            f'<a href="{url}"><u>Click here to view live results</u></a>.'
        )
        assert manager.active_poll is not None
        assert user.id not in manager.active_poll.votes

    async def test_vote_command_already_voted_sends_results_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """!vote <number> reports duplicate failures from record_vote."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        owncast_client.calls.clear()

        def get_selections(_user: User) -> NoReturn:
            pytest.fail("valid quick votes should defer to record_vote")

        monkeypatch.setattr(manager, "get_selections", get_selections)

        event = make_chat_event(raw_body="!vote 2", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "You have already voted in this poll. "
            f'<a href="{url}"><u>Click here to view live results</u></a>.'
        )
        assert manager.active_poll is not None
        assert manager.active_poll.votes[user.id] == {0}

    @pytest.mark.parametrize(
        "raw_body",
        [
            pytest.param("!vote abc", id="non-numeric"),
            pytest.param("!vote 99", id="out-of-range"),
        ],
    )
    async def test_vote_command_invalid_option_reports_range(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
        raw_body: str,
    ) -> None:
        """!vote <bad-option> reports syntax errors without status prechecks."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        owncast_client.calls.clear()

        def get_selections(_user: User) -> NoReturn:
            pytest.fail("invalid quick votes should not precheck vote status")

        monkeypatch.setattr(manager, "get_selections", get_selections)

        event = make_chat_event(raw_body=raw_body, user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "Invalid option number. Use !vote 1-2 to vote, or "
            f'<a href="{url}"><u>click here to cast your vote</u></a>.'
        )
        assert manager.active_poll is not None
        assert user.id not in manager.active_poll.votes

    @pytest.mark.parametrize(
        ("error", "expected_fragments"),
        [
            pytest.param(
                NoActivePollError(),
                ("There is no active poll.",),
                id="no-active",
            ),
            pytest.param(
                PollAuthenticationRequiredError("alice"),
                (
                    "This poll requires an authenticated account to vote.",
                    "Click here to view live results",
                ),
                id="auth-required",
            ),
            pytest.param(
                DuplicateVoteError("alice"),
                (
                    "You have already voted in this poll.",
                    "Click here to view live results",
                ),
                id="duplicate",
            ),
            pytest.param(
                PollVoteValidationError("Injected validation error."),
                ("Injected validation error.",),
                id="validation",
            ),
        ],
    )
    async def test_vote_command_record_vote_errors_report_message(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        expected_fragments: tuple[str, ...],
    ) -> None:
        """!vote <number> maps late record_vote failures to command text."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        owncast_client.calls.clear()

        async def record_vote(_user: User, _selections: set[int]) -> None:
            raise error

        monkeypatch.setattr(manager, "record_vote", record_vote)

        event = make_chat_event(raw_body="!vote 1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        body = _private_bodies(owncast_client)[0]
        for fragment in expected_fragments:
            assert fragment in body

    async def test_vote_command_records_single_select_vote(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!vote <number> records a single-select vote."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        event = make_chat_event(raw_body="!vote 2", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert manager.get_selections(user) == frozenset({1})
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "Your vote for #2 (Blue) has been recorded. "
            f'<a href="{url}"><u>Click here to view live results</u></a>.'
        )

    async def test_vote_command_multi_select_sends_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """!vote <number> on multi-select polls sends the voting page link."""
        manager = get_manager(module_context)
        await _create_poll(manager, allow_multiple=True)

        def get_selections(_user: User) -> NoReturn:
            pytest.fail("multi-select command links should not precheck vote status")

        monkeypatch.setattr(manager, "get_selections", get_selections)

        event = make_chat_event(raw_body="!vote 1")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "This poll allows multiple selections,"
            " so votes must be cast through the voting page. "
            f'<a href="{url}"><u>Click here to cast your vote</u></a>.'
        )


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestBareVoteEvent:
    """Bare-number chat vote behavior."""

    @pytest.mark.parametrize(
        "raw_body",
        [
            pytest.param("not a vote", id="non-numeric"),
            pytest.param("3", id="out-of-range"),
        ],
    )
    async def test_bare_number_invalid_messages_are_ignored(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        raw_body: str,
    ) -> None:
        """Bare chat that cannot be a vote leaves chat visibility untouched."""
        await _create_poll(get_manager(module_context))
        owncast_client.calls.clear()

        event = make_chat_event(raw_body=raw_body, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls == []

    async def test_bare_number_vote_records_and_hides(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """A bare valid number records a vote during single-select polls."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        owncast_client.calls.clear()
        event = make_chat_event(raw_body="1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert owncast_client.calls[0].name == "set_message_visibility"
        assert manager.get_selections(user) == frozenset({0})

    @pytest.mark.parametrize(
        ("error", "expected_fragments"),
        [
            pytest.param(
                NoActivePollError(),
                ("There is no active poll.",),
                id="no-active",
            ),
            pytest.param(
                PollAuthenticationRequiredError("alice"),
                (
                    "This poll requires an authenticated account to vote.",
                    "Click here to view live results",
                ),
                id="auth-required",
            ),
            pytest.param(
                DuplicateVoteError("alice"),
                (
                    "You have already voted in this poll.",
                    "Click here to view live results",
                ),
                id="duplicate",
            ),
            pytest.param(
                PollVoteValidationError("Injected validation error."),
                ("Injected validation error.",),
                id="validation",
            ),
        ],
    )
    async def test_bare_number_record_vote_errors_report_message(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        expected_fragments: tuple[str, ...],
    ) -> None:
        """Bare-number voting maps record_vote failures to command text."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        owncast_client.calls.clear()

        async def record_vote(_user: User, _selections: set[int]) -> None:
            raise error

        monkeypatch.setattr(manager, "record_vote", record_vote)

        event = make_chat_event(raw_body="1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        for fragment in expected_fragments:
            assert fragment in body

    async def test_bare_number_auth_required_reports_results_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Bare-number voting reports auth-only polls and does not store a vote."""
        manager = get_manager(module_context)
        user = make_user(id="guest", is_authenticated=False)
        await _create_poll(manager, requires_auth=True)
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "This poll requires an authenticated account to vote. "
            f'<a href="{url}"><u>Click here to view live results</u></a>.'
        )
        assert manager.get_selections(user) is None

    async def test_bare_number_already_voted_reports_results_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Bare-number voting reports duplicate voters without changing their vote."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="2", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "You have already voted in this poll. "
            f'<a href="{url}"><u>Click here to view live results</u></a>.'
        )
        assert manager.get_selections(user) == frozenset({0})

    async def test_bare_number_multi_select_sends_voting_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Bare-number voting on multi-select polls sends the voting page."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager, allow_multiple=True)
        owncast_client.calls.clear()

        event = make_chat_event(raw_body="1", user=user, message_id="m1")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert owncast_client.calls[0].name == "set_message_visibility"
        body = _private_bodies(owncast_client)[0]
        url = _href_from_body(body)
        assert body == (
            "This poll allows multiple selections,"
            " so votes must be cast through the voting page. "
            f'<a href="{url}"><u>Click here to cast your vote</u></a>.'
        )
        assert manager.get_selections(user) is None


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestRoutes:
    """Poll HTML routes and protected-session behavior."""

    async def test_create_page_renders_default_form(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /create renders a blank poll creation form."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )

        resp = await route_client.get("/owlbot/polls/create", headers=headers)
        body = await resp.text()

        assert resp.status == 200
        assert "Create Poll" in body
        assert "What do you want to ask chat?" in body
        min_select_input = body.split('id="min_selections"', 1)[1].split(">", 1)[0]
        max_select_input = body.split('id="max_selections"', 1)[1].split(">", 1)[0]
        assert "disabled" in min_select_input
        assert "disabled" in max_select_input

    async def test_create_page_active_conflict(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /create reports when a poll is already active."""
        await _create_poll(get_manager(module_context))
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )

        resp = await route_client.get("/owlbot/polls/create", headers=headers)

        assert resp.status == 409
        assert "A poll is already active." in await resp.text()

    async def test_create_submit_redirects_and_stores_poll(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /create creates a poll and redirects to /current."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(
            "/owlbot/polls/create",
            headers=headers,
            data=[
                ("question", "Best color?"),
                ("options", "Red"),
                ("options", "Blue"),
                ("duration", str(MIN_DURATION)),
            ],
            allow_redirects=False,
        )

        assert resp.status == 302
        assert resp.headers["Location"] == "http://localhost:8081/owlbot/polls/current"
        poll = get_manager(module_context).active_poll
        assert poll is not None
        assert poll.options == ["Red", "Blue"]

    async def test_create_submit_active_conflict_renders_form_error(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /create maps active-poll conflicts before validation errors."""
        await _create_poll(get_manager(module_context))
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(
            "/owlbot/polls/create",
            headers=headers,
            data=[
                ("question", ""),
                ("options", "Only"),
                ("duration", str(MIN_DURATION)),
            ],
        )

        assert resp.status == 409
        assert "A poll is already active." in await resp.text()

    async def test_create_submit_validation_renders_form_error(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /create renders validation errors without broad exception handling."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(
            "/owlbot/polls/create",
            headers=headers,
            data=[
                ("question", ""),
                ("options", "Red"),
                ("options", "Blue"),
                ("duration", str(MIN_DURATION)),
            ],
        )

        assert resp.status == 400
        assert "Question is required." in await resp.text()

    async def test_create_submit_invalid_duration_renders_form_error(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /create handles invalid duration values with a form error."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(
            "/owlbot/polls/create",
            headers=headers,
            data=[
                ("question", "Best color?"),
                ("options", "Red"),
                ("options", "Blue"),
                ("duration", "not-a-number"),
            ],
        )

        assert resp.status == 400
        assert "Duration must be" in await resp.text()

    async def test_current_page_requires_session(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """Protected vote page returns the shared connect guidance without a session."""
        resp = await route_client.get("/owlbot/polls/current")
        assert resp.status == 401
        assert "!connect" in await resp.text()

    async def test_current_page_no_active_poll_returns_not_found(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /current reports when no poll is active."""
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        resp = await route_client.get("/owlbot/polls/current", headers=headers)

        assert resp.status == 404
        assert "No active poll." in await resp.text()

    async def test_current_page_shows_auth_warning(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """Unauthenticated viewers can open auth-only polls but cannot vote."""
        await _create_poll(get_manager(module_context), requires_auth=True)
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=False),
        )
        resp = await route_client.get("/owlbot/polls/current", headers=headers)
        body = await resp.text()
        assert resp.status == 200
        assert "requires an authenticated account" in body

    @pytest.mark.parametrize(
        ("min_selections", "max_selections", "expected"),
        [
            pytest.param(None, None, "Select one or more options.", id="open-ended"),
            pytest.param(None, 3, "Select 1 to 3 options.", id="max-only"),
            pytest.param(2, None, "Select at least 2 options.", id="min-only"),
            pytest.param(2, 2, "Select exactly 2 options.", id="exact"),
            pytest.param(2, 3, "Select 2 to 3 options.", id="range"),
        ],
    )
    async def test_current_page_shows_multi_select_guidance(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        min_selections: int | None,
        max_selections: int | None,
        expected: str,
    ) -> None:
        """Multi-select vote pages tell viewers how many options to choose."""
        await _create_poll(
            get_manager(module_context),
            options=["Red", "Blue", "Green"],
            allow_multiple=True,
            min_selections=min_selections,
            max_selections=max_selections,
        )
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True),
        )

        resp = await route_client.get("/owlbot/polls/current", headers=headers)

        assert resp.status == 200
        assert expected in await resp.text()

    async def test_current_page_confirms_recorded_vote(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /current confirms an already-recorded vote."""
        manager = get_manager(module_context)
        user = make_user(id="alice", is_authenticated=True)
        await _create_poll(manager)
        await manager.record_vote(user, {1})
        headers = _session_headers(session_manager, user=user)

        resp = await route_client.get("/owlbot/polls/current", headers=headers)

        assert resp.status == 200
        body = await resp.text()
        assert "Your vote has been recorded." in body

    async def test_vote_submit_records_and_redirects(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/vote stores the submitted selection and redirects."""
        manager = get_manager(module_context)
        user = make_user(id="alice", is_authenticated=True)
        await _create_poll(manager)
        headers = _session_headers(session_manager, user=user)
        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "1"},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert manager.get_selections(user) == frozenset({1})

    async def test_vote_submit_no_active_poll_returns_not_found(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/vote reports when no poll is active."""
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "0"},
        )

        assert resp.status == 404
        assert "No active poll." in await resp.text()

    async def test_vote_submit_duplicate_returns_bad_request(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/vote maps duplicate votes to route-owned text."""
        manager = get_manager(module_context)
        user = make_user(id="alice", is_authenticated=True)
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        headers = _session_headers(session_manager, user=user)
        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "1"},
        )

        assert resp.status == 400
        body = await resp.text()
        assert "You have already voted." in body
        assert 'id="vote-form"' in body
        assert '<h1 class="mb-3">Error</h1>' not in body

    async def test_vote_submit_auth_edge_returns_forbidden(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /current/vote reports auth failures from record_vote."""
        manager = get_manager(module_context)
        await _create_poll(manager, requires_auth=True)

        def get_selections(_user: User) -> NoReturn:
            pytest.fail("vote submissions should defer auth checks to record_vote")

        monkeypatch.setattr(manager, "get_selections", get_selections)

        headers = _session_headers(session_manager, user=make_user(id="guest"))
        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "0"},
        )
        assert resp.status == 403
        body = await resp.text()
        assert "requires an authenticated account" in body
        assert 'id="vote-form"' in body
        assert '<h1 class="mb-3">Error</h1>' not in body

    async def test_vote_submit_invalid_selection_returns_bad_request(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/vote rejects non-integer selections."""
        await _create_poll(get_manager(module_context))
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "not-a-number"},
        )

        assert resp.status == 400
        body = await resp.text()
        assert "Invalid selection." in body
        assert 'id="vote-form"' in body
        assert '<h1 class="mb-3">Error</h1>' not in body

    async def test_vote_submit_invalid_selection_handles_late_inactive_poll(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /current/vote maps stale invalid submissions to the 404 page."""
        manager = get_manager(module_context)
        await _create_poll(manager)

        def get_selections(_user: User) -> NoReturn:
            raise NoActivePollError

        monkeypatch.setattr(manager, "get_selections", get_selections)
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True),
        )

        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "not-a-number"},
        )

        assert resp.status == 404
        body = await resp.text()
        assert "No active poll." in body
        assert '<h1 class="mb-3">Error</h1>' in body

    @pytest.mark.parametrize(
        ("error", "status", "expected"),
        [
            pytest.param(NoActivePollError(), 404, "No active poll.", id="no-active"),
            pytest.param(
                PollAuthenticationRequiredError("alice"),
                403,
                "requires an authenticated account",
                id="auth-required",
            ),
            pytest.param(
                PollVoteValidationError("Injected validation error."),
                400,
                "Injected validation error.",
                id="validation",
            ),
        ],
    )
    async def test_vote_submit_late_record_vote_errors(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        status: int,
        expected: str,
    ) -> None:
        """POST /current/vote maps late manager errors to route responses."""
        manager = get_manager(module_context)
        await _create_poll(manager)
        headers = _session_headers(
            session_manager,
            user=make_user(id="alice", is_authenticated=True),
        )

        async def record_vote(_user: User, _selections: set[int]) -> None:
            raise error

        monkeypatch.setattr(manager, "record_vote", record_vote)

        resp = await route_client.post(
            "/owlbot/polls/current/vote",
            headers=headers,
            data={"option": "0"},
        )

        assert resp.status == status
        body = await resp.text()
        if isinstance(error, NoActivePollError):
            assert expected in body
            assert '<h1 class="mb-3">Error</h1>' in body
        else:
            assert expected in body
            assert 'id="vote-form"' in body
            assert '<h1 class="mb-3">Error</h1>' not in body

    async def test_events_stream_no_active_poll_returns_not_found(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /current/events reports when no poll is active."""
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        resp = await route_client.get("/owlbot/polls/current/events", headers=headers)

        assert resp.status == 404
        assert await resp.text() == "No active poll."

    async def test_events_stream_prepares_sse_response(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /current/events prepares an SSE response and delegates streaming."""
        manager = get_manager(module_context)
        await _create_poll(manager)
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        async def serve_sse_stream(_user: User, response: web.StreamResponse) -> None:
            await response.write(sse_payload(SSEEvent.KEEPALIVE, {"time_remaining": 1}))

        monkeypatch.setattr(manager, "serve_sse_stream", serve_sse_stream)

        resp = await route_client.get("/owlbot/polls/current/events", headers=headers)

        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        assert b"event: keepalive" in await resp.read()

    async def test_events_stream_handles_registration_race(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /current/events tolerates no-active errors after response prepare."""
        manager = get_manager(module_context)
        await _create_poll(manager)
        headers = _session_headers(
            session_manager, user=make_user(is_authenticated=True)
        )

        async def serve_sse_stream(
            _user: User,
            _response: web.StreamResponse,
        ) -> None:
            raise NoActivePollError

        monkeypatch.setattr(manager, "serve_sse_stream", serve_sse_stream)

        resp = await route_client.get("/owlbot/polls/current/events", headers=headers)

        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")

    async def test_mod_end_redirects_to_results(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/end ends the active poll and redirects to results."""
        await _create_poll(get_manager(module_context))
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(
            "/owlbot/polls/current/end",
            headers=headers,
            allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == "http://localhost:8081/owlbot/polls/results"

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("/owlbot/polls/current/end", id="end"),
            pytest.param("/owlbot/polls/current/cancel", id="cancel"),
        ],
    )
    async def test_mod_lifecycle_no_active_returns_not_found(
        self,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
        path: str,
    ) -> None:
        """Moderator lifecycle routes map missing state to route-owned text."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )
        resp = await route_client.post(path, headers=headers)

        assert resp.status == 404
        assert "No active poll." in await resp.text()

    async def test_mod_cancel_returns_cancelled_page(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """POST /current/cancel cancels the active poll."""
        await _create_poll(get_manager(module_context))
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=True),
        )

        resp = await route_client.post("/owlbot/polls/current/cancel", headers=headers)

        assert resp.status == 200
        assert "This poll has been cancelled." in await resp.text()

    async def test_results_page_renders_last_result(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """GET /results renders the last completed poll."""
        manager = get_manager(module_context)
        await _create_poll(manager)
        await manager.record_vote(make_user(id="u1"), {0})
        await manager.end()
        headers = _session_headers(session_manager, user=make_user())
        resp = await route_client.get("/owlbot/polls/results", headers=headers)
        body = await resp.text()
        assert resp.status == 200
        assert "Best color?" in body
        assert "Red" in body

    async def test_results_page_reports_active_poll(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /results reports when a poll is still active."""
        await _create_poll(get_manager(module_context))

        resp = await route_client.get("/owlbot/polls/results")

        assert resp.status == 200
        assert "A poll is currently in progress." in await resp.text()

    async def test_results_page_reports_no_recent_result(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /results reports when no completed poll is available."""
        resp = await route_client.get("/owlbot/polls/results")

        assert resp.status == 200
        assert "No poll has been done recently." in await resp.text()

    @pytest.mark.parametrize(
        ("path", "cache_control"),
        [
            pytest.param(
                "/owlbot/polls/static/polls.css",
                "max-age=86400",
                id="css",
            ),
            pytest.param(
                "/owlbot/polls/static/polls.js",
                "max-age=86400",
                id="polls-js",
            ),
            pytest.param(
                "/owlbot/polls/static/create.js",
                "max-age=86400",
                id="create-js",
            ),
            pytest.param(
                "/owlbot/polls/static/shared/config.js",
                "no-cache",
                id="shared-config-js",
            ),
            pytest.param(
                "/owlbot/polls/static/shared/dom.js",
                "no-cache",
                id="shared-dom-js",
            ),
            pytest.param(
                "/owlbot/polls/static/shared/poll-events.js",
                "no-cache",
                id="shared-poll-events-js",
            ),
        ],
    )
    async def test_static_routes(
        self,
        route_client: TestClient[Any, Any],
        path: str,
        cache_control: str,
    ) -> None:
        """Static asset routes serve bundled poll assets."""
        resp = await route_client.get(path)

        assert resp.status == 200
        assert resp.headers["Cache-Control"] == cache_control

    async def test_static_unknown_asset_returns_not_found(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """Static asset route rejects filenames outside the allow-list."""
        resp = await route_client.get("/owlbot/polls/static/missing.js")

        assert resp.status == 404


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestEvents:
    """Poll event handlers."""

    @pytest.mark.usefixtures("fast_sleep")
    async def test_stream_stop_and_start_manage_grace(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
    ) -> None:
        """STREAM_STOPPED starts grace and STREAM_STARTED cancels it."""
        manager = get_manager(module_context)
        await _create_poll(manager)
        await event_dispatcher.dispatch(
            EventType.STREAM_STOPPED,
            make_stream_stopped_event(),
        )
        await event_dispatcher.dispatch(
            EventType.STREAM_STARTED,
            make_stream_started_event(),
        )
        await _REAL_ASYNCIO_SLEEP(0.02)
        assert manager.active_poll is not None

    async def test_user_joined_gets_active_poll_notice(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """USER_JOINED sends a clear private vote link."""
        await _create_poll(get_manager(module_context))
        event = make_user_joined_event(client_id=99)
        await event_dispatcher.dispatch(EventType.USER_JOINED, event)
        body = _private_bodies(owncast_client)[0]
        assert "Poll in progress" in body
        assert "Click here to vote" in body
        assert "!vote" not in body
        assert "Type a number in chat" not in body
        assert _href_from_body(body).startswith("http://localhost:8081/owlbot/connect/")

    async def test_user_joined_after_voting_gets_results_link(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """USER_JOINED sends a results link to viewers who already voted."""
        manager = get_manager(module_context)
        user = make_user(id="alice")
        await _create_poll(manager)
        await manager.record_vote(user, {0})
        owncast_client.calls.clear()

        event = make_user_joined_event(user=user, client_id=99)
        await event_dispatcher.dispatch(EventType.USER_JOINED, event)

        body = _private_bodies(owncast_client)[0]
        assert "Click here to view live results" in body
        assert "Click here to vote" not in body
        assert _href_from_body(body).startswith("http://localhost:8081/owlbot/connect/")

    async def test_user_joined_without_active_poll_noops(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """USER_JOINED does nothing when no poll is active."""
        event = make_user_joined_event(client_id=99)
        await event_dispatcher.dispatch(EventType.USER_JOINED, event)
        assert owncast_client.calls == []

    async def test_user_joined_multi_select_gets_link_instruction(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """USER_JOINED keeps multi-select instructions focused on the vote link."""
        await _create_poll(get_manager(module_context), allow_multiple=True)
        event = make_user_joined_event(client_id=99)
        await event_dispatcher.dispatch(EventType.USER_JOINED, event)
        body = _private_bodies(owncast_client)[0]
        assert "Click here to vote" in body
        assert "!vote" not in body
        assert _href_from_body(body).startswith("http://localhost:8081/owlbot/connect/")


@pytest.mark.usefixtures("_suppress_poll_timers", "module_lifecycle")
class TestModeratorPermissions:
    """Moderator-required poll commands and routes deny non-moderators."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("poll", id="poll"),
            pytest.param("endpoll", id="endpoll"),
            pytest.param("cancelpoll", id="cancelpoll"),
        ],
    )
    async def test_non_moderator_command_denied(
        self,
        command: str,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Non-moderator invocation triggers the dispatcher denial."""
        user = make_user(is_moderator=False)
        event = make_chat_event(raw_body=f"!{command}", user=user)

        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _private_bodies(owncast_client) == [
            f"Only moderators can use !{command}."
        ]

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            pytest.param("GET", "/owlbot/polls/create", id="create-page"),
            pytest.param("POST", "/owlbot/polls/create", id="create-submit"),
            pytest.param("POST", "/owlbot/polls/current/end", id="end"),
            pytest.param("POST", "/owlbot/polls/current/cancel", id="cancel"),
        ],
    )
    async def test_non_moderator_route_denied(
        self,
        method: str,
        path: str,
        route_client: TestClient[Any, Any],
        session_manager: SessionManager,
    ) -> None:
        """Protected moderator routes return the shared moderator-denial page."""
        headers = _session_headers(
            session_manager,
            user=make_user(is_authenticated=True, is_moderator=False),
        )

        if method == "GET":
            resp = await route_client.get(path, headers=headers)
        else:
            resp = await route_client.post(path, headers=headers)

        assert resp.status == 403
        assert "Only moderators can access this page." in await resp.text()
