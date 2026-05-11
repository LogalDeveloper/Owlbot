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

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


MAX_QUESTION_LENGTH = 200
MAX_OPTION_LENGTH = 50
MIN_OPTIONS = 2
MAX_OPTIONS = 10
DEFAULT_DURATION = 240
MIN_DURATION = 30
MAX_DURATION = 600
REMINDER_THRESHOLD = 60
STREAM_GRACE_PERIOD = 5 * 60
RESULT_EXPIRY = 3600


class PollError(Exception):
    """Base class for polls module domain errors."""


class PollStateError(PollError):
    """Raised when an operation is invalid for the current poll state."""


class PollAlreadyActiveError(PollStateError):
    """Raised when creating a poll while another one is active."""

    def __init__(self) -> None:
        """Initialize with the user-facing active-poll message."""
        super().__init__("A poll is already active.")


class NoActivePollError(PollStateError):
    """Raised when an operation requires an active poll."""

    def __init__(self) -> None:
        """Initialize with the user-facing missing-poll message."""
        super().__init__("No active poll.")


class PollCreateValidationError(PollError):
    """Raised when poll creation input is invalid."""


class PollVoteError(PollError):
    """Raised when a vote cannot be accepted."""


class PollVoteValidationError(PollVoteError):
    """Raised when a submitted vote is invalid."""


class PollAuthenticationRequiredError(PollVoteError):
    """Raised when an unauthenticated user tries to vote in an auth-only poll."""

    def __init__(self, user_id: str) -> None:
        """Initialize with the denied user ID."""
        self.user_id = user_id
        super().__init__("This poll requires an authenticated account to vote.")


class DuplicateVoteError(PollVoteError):
    """Raised when the same user tries to vote more than once."""

    def __init__(self, user_id: str) -> None:
        """Initialize with the duplicate voter's user ID."""
        self.user_id = user_id
        super().__init__("You have already voted.")


@dataclass(slots=True)
class ActivePoll:
    """An active poll instance.

    :param question: The poll question.
    :param options: List of poll options.
    :param hidden: Whether live result tallies are hidden from non-moderator
        viewers until the poll ends.
    :param requires_auth: Whether voters must be authenticated.
    :param allow_multiple: Whether voters can select multiple options.
    :param min_selections: Minimum number of selections required (multi-select).
    :param max_selections: Maximum number of selections allowed (multi-select).
    :param duration: Poll duration in seconds.
    :param created_at: Timestamp when the poll was created.
    :param votes: Mapping of user ID to set of selected option indices.
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
    _started_at_mono: float = field(default_factory=time.monotonic)

    @property
    def time_remaining(self) -> int:
        """Seconds remaining until the poll ends, minimum 0.

        :return: Remaining poll duration in whole seconds.
        """
        elapsed = time.monotonic() - self._started_at_mono
        return max(0, int(self.duration - elapsed))


@dataclass(frozen=True, slots=True)
class PollResults:
    """Computed results of a poll.

    :param counts: Vote counts aligned with poll option order.
    :param winners: Option labels tied for the highest vote count.
    :param is_tie: Whether multiple options share the highest vote count.
    :param total_votes: Number of voters represented in the result.
    """

    counts: tuple[int, ...]
    winners: tuple[str, ...]
    is_tie: bool
    total_votes: int


def compute_results(options: list[str], votes: dict[str, set[int]]) -> PollResults:
    """Compute poll results from votes.

    Counts votes per option, finds winners, and detects ties.

    :param options: List of poll option strings.
    :param votes: Mapping of user ID to set of selected option indices.
    :return: Immutable results with counts, winners, tie state, and voter total.
    """
    num_options = len(options)
    counts = [0] * num_options

    for selections in votes.values():
        for idx in selections:
            counts[idx] += 1

    total_votes = len(votes)

    max_count = max(counts) if counts else 0
    if total_votes == 0 or max_count == 0:
        winners: list[str] = []
        is_tie = False
    else:
        winners = [options[i] for i, c in enumerate(counts) if c == max_count]
        is_tie = len(winners) > 1

    return PollResults(
        counts=tuple(counts),
        winners=tuple(winners),
        is_tie=is_tie,
        total_votes=total_votes,
    )


@dataclass(frozen=True, slots=True)
class CompletedPoll:
    """Snapshot of a completed poll's results.

    Stored in memory as the most recent poll result.
    Ephemeral — not persisted to disk.

    :param question: The poll question.
    :param options: Poll options sorted by vote count descending, then by label
        for ties.
    :param results: Computed results (counts, winners, tie status).
    :param created_at: When the poll was created.
    :param ended_at: When the poll ended.
    """

    question: str
    options: tuple[str, ...]
    results: PollResults
    created_at: datetime
    ended_at: datetime


def complete_poll(poll: ActivePoll, ended_at: datetime) -> CompletedPoll:
    """Build a completed poll snapshot with display-ready sorted results.

    :param poll: Active poll to complete.
    :param ended_at: Timestamp when the poll ended.
    :return: Completed poll snapshot with options sorted by result count, then
        by label for ties.
    """
    raw_results = compute_results(
        poll.options,
        {user_id: set(selections) for user_id, selections in poll.votes.items()},
    )
    counts = raw_results.counts
    sorted_indices = sorted(
        range(len(poll.options)),
        key=lambda i: (-counts[i], poll.options[i].lower()),
    )
    sorted_options = tuple(poll.options[i] for i in sorted_indices)

    return CompletedPoll(
        question=poll.question,
        options=sorted_options,
        results=PollResults(
            counts=tuple(counts[i] for i in sorted_indices),
            winners=raw_results.winners,
            is_tie=raw_results.is_tie,
            total_votes=raw_results.total_votes,
        ),
        created_at=poll.created_at,
        ended_at=ended_at,
    )
