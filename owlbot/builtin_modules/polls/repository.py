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

"""Synchronous in-memory repository for active poll data and recent results."""

from __future__ import annotations

from .types import (
    ActivePoll,
    CompletedPoll,
    DuplicateVoteError,
    NoActivePollError,
    PollAlreadyActiveError,
)


class PollRepository:
    """Store active poll state and the most recent completed result in memory."""

    def __init__(self) -> None:
        """Initialize an empty repository."""
        self._active_poll: ActivePoll | None = None
        self._last_result: CompletedPoll | None = None

    def get_active_poll(self) -> ActivePoll | None:
        """Return the active poll, if one exists.

        :return: The active poll, or None if no poll is active.
        """
        return self._active_poll

    def create_active_poll(self, poll: ActivePoll) -> ActivePoll:
        """Store a new active poll.

        :param poll: The poll to activate.
        :return: The stored poll.
        :raises PollAlreadyActiveError: If a poll is already active.
        """
        if self._active_poll is not None:
            raise PollAlreadyActiveError
        self._active_poll = poll
        return poll

    def pop_active_poll(self) -> ActivePoll | None:
        """Clear and return the active poll, if one exists.

        :return: The previously active poll, or None if no poll was active.
        """
        poll = self._active_poll
        if poll is None:
            return None
        self._active_poll = None
        return poll

    def get_vote(self, user_id: str) -> set[int] | None:
        """Return a copy of a user's selected options for the active poll.

        :param user_id: ID of the user whose vote should be read.
        :return: Selected option indices, or None if the user has not voted.
        :raises NoActivePollError: If no poll is active.
        """
        poll = self._require_active_poll()
        selections = poll.votes.get(user_id)
        return None if selections is None else set(selections)

    def set_vote(self, user_id: str, selections: set[int]) -> None:
        """Store a user's selected options for the active poll.

        :param user_id: ID of the user casting the vote.
        :param selections: Selected option indices.
        :raises NoActivePollError: If no poll is active.
        :raises DuplicateVoteError: If the user already voted.
        """
        poll = self._require_active_poll()
        if user_id in poll.votes:
            raise DuplicateVoteError(user_id)
        poll.votes[user_id] = set(selections)

    def list_votes(self) -> dict[str, set[int]]:
        """Return a copy of all user selections for the active poll.

        :return: Mapping of user IDs to selected option indices.
        :raises NoActivePollError: If no poll is active.
        """
        poll = self._require_active_poll()
        return {user_id: set(selections) for user_id, selections in poll.votes.items()}

    def get_last_result(self) -> CompletedPoll | None:
        """Return the most recent completed result, if one exists.

        :return: Most recent completed poll, or None if no result is available.
        """
        return self._last_result

    def set_last_result(self, result: CompletedPoll) -> None:
        """Store the most recent completed result.

        :param result: Completed poll snapshot to expose on the results page.
        """
        self._last_result = result

    def clear_last_result(self) -> None:
        """Clear the most recent completed result."""
        self._last_result = None

    def _require_active_poll(self) -> ActivePoll:
        """Return the active poll or raise the domain no-active error.

        :return: The currently active poll.
        :raises NoActivePollError: If no poll is active.
        """
        poll = self._active_poll
        if poll is None:
            raise NoActivePollError
        return poll
