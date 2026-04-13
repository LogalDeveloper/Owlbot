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

"""Typed state containers, constants, and helper functions for the polls module."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict

import orjson

if TYPE_CHECKING:
    from datetime import datetime

    from aiohttp import web


MAX_QUESTION_LENGTH = 200
MAX_OPTION_LENGTH = 50
MIN_OPTIONS = 2
MAX_OPTIONS = 10
DEFAULT_DURATION = 240
MIN_DURATION = 30
MAX_DURATION = 600
REMINDER_THRESHOLD = 60
CREATION_TOKEN_TIMEOUT = 15 * 60
STREAM_GRACE_PERIOD = 5 * 60
RESULT_EXPIRY = 3600


class SSEEvent(StrEnum):
    """Server-Sent Event types for the polls module."""

    KEEPALIVE = "keepalive"
    POLL_CANCELLED = "poll_cancelled"
    POLL_ENDED = "poll_ended"
    TALLY = "tally"


def sse_payload(event_type: SSEEvent, data: dict[str, Any]) -> bytes:
    """Serialize an SSE event into wire format.

    :param event_type: The event type name.
    :param data: The JSON-serializable event data.
    :return: Encoded SSE frame ready for ``StreamResponse.write()``.
    """
    return b"event: " + event_type.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"


@dataclass(slots=True)
class Token:
    """A token for poll access.

    :param user_id: The Owncast user ID this token belongs to.
    :param is_moderator: Whether the user has moderator privileges.
    :param is_authenticated: Whether the user has an authenticated Owncast account.
    :param value: The token string used in URLs (auto-generated if not provided).
    """

    user_id: str
    is_moderator: bool
    is_authenticated: bool
    value: str = field(default_factory=lambda: secrets.token_urlsafe(32))


@dataclass(slots=True)
class ActivePoll:
    """An active poll instance.

    :param question: The poll question.
    :param options: List of poll options.
    :param hidden: Whether vote results are hidden until the poll ends.
    :param requires_auth: Whether voters must be authenticated.
    :param allow_multiple: Whether voters can select multiple options.
    :param min_selections: Minimum number of selections required (multi-select).
    :param max_selections: Maximum number of selections allowed (multi-select).
    :param duration: Poll duration in seconds.
    :param created_at: Timestamp when the poll was created.
    :param votes: Mapping of user ID to set of selected option indices.
    :param sse_clients: Mapping of client token to SSE stream response.
    :param timer_task: Asyncio task for the poll timer.
    :param tokens: Mapping of token string to Token object.
    """

    question: str
    options: list[str]
    hidden: bool
    requires_auth: bool
    allow_multiple: bool
    min_selections: int | None
    max_selections: int | None
    duration: int
    created_at: datetime
    votes: dict[str, set[int]] = field(default_factory=dict)
    sse_clients: dict[str, web.StreamResponse] = field(default_factory=dict)
    timer_task: asyncio.Task[None] | None = None
    tokens: dict[str, Token] = field(default_factory=dict)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    _started_at_mono: float = field(default_factory=time.monotonic)

    @property
    def time_remaining(self) -> int:
        """Seconds remaining until the poll ends, minimum 0."""
        elapsed = time.monotonic() - self._started_at_mono
        return max(0, int(self.duration - elapsed))


class PollResults(TypedDict):
    """Computed results of a poll."""

    counts: list[int]
    winners: list[str]
    is_tie: bool
    total_votes: int


def compute_results(options: list[str], votes: dict[str, set[int]]) -> PollResults:
    """Compute poll results from votes.

    Counts votes per option, finds winners, and detects ties.

    :param options: List of poll option strings.
    :param votes: Mapping of user ID to set of selected option indices.
    :return: Dict with counts, winners, is_tie, and total_votes.
    """
    num_options = len(options)
    counts = [0] * num_options

    for selections in votes.values():
        for idx in selections:
            counts[idx] += 1

    total_votes = len(votes)

    max_count = max(counts) if counts else 0
    if total_votes == 0 or max_count == 0:
        winners = []
        is_tie = False
    else:
        winners = [options[i] for i, c in enumerate(counts) if c == max_count]
        is_tie = len(winners) > 1

    return {
        "counts": counts,
        "winners": winners,
        "is_tie": is_tie,
        "total_votes": total_votes,
    }


@dataclass(slots=True)
class CompletedPoll:
    """Snapshot of a completed poll's results.

    Stored in memory on PollManager as the most recent result.
    Ephemeral — not persisted to disk.

    :param question: The poll question.
    :param options: Poll options sorted by vote count descending.
    :param results: Computed results (counts, winners, tie status).
    :param created_at: When the poll was created.
    :param ended_at: When the poll ended.
    """

    question: str
    options: list[str]
    results: PollResults
    created_at: datetime
    ended_at: datetime
