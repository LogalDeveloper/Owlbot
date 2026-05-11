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

"""Poll lifecycle manager for the polls module."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from markupsafe import escape

from .sse import PollSSERegistry, SSEEvent
from .types import (
    MAX_DURATION,
    MAX_OPTION_LENGTH,
    MAX_OPTIONS,
    MAX_QUESTION_LENGTH,
    MIN_DURATION,
    MIN_OPTIONS,
    REMINDER_THRESHOLD,
    RESULT_EXPIRY,
    STREAM_GRACE_PERIOD,
    ActivePoll,
    CompletedPoll,
    DuplicateVoteError,
    NoActivePollError,
    PollAlreadyActiveError,
    PollAuthenticationRequiredError,
    PollCreateValidationError,
    PollVoteValidationError,
    complete_poll,
    compute_results,
)

if TYPE_CHECKING:
    from aiohttp import web

    from owlbot.api import ModuleContext, User
    from owlbot.builtin_modules.polls.repository import PollRepository


class PollManager:
    """Own poll business rules, runtime tasks, chat output, and SSE streams."""

    def __init__(self, ctx: ModuleContext, repo: PollRepository) -> None:
        """Initialize the manager with module services and a data repository.

        :param ctx: Module context that provides routes, chat clients, and logging.
        :param repo: Repository used for active poll and result state.
        """
        self._ctx = ctx
        self._repo = repo
        self._lifecycle_lock = asyncio.Lock()
        self._poll_timer_task: asyncio.Task[None] | None = None
        self._poll_done_event = asyncio.Event()
        self._sse = PollSSERegistry(ctx.logger)
        self._grace_task: asyncio.Task[None] | None = None
        self._result_expiry_task: asyncio.Task[None] | None = None

    @property
    def active_poll(self) -> ActivePoll | None:
        """The currently active poll, if any.

        :return: The active poll, or None if no poll is active.
        """
        return self._repo.get_active_poll()

    @property
    def last_result(self) -> CompletedPoll | None:
        """The most recently completed poll result, if any.

        :return: The most recent completed poll, or None if unavailable.
        """
        return self._repo.get_last_result()

    def get_selections(self, user: User) -> frozenset[int] | None:
        """Return a viewer's stored selections for the active poll, if any.

        :param user: Viewer whose selections should be read.
        :return: Selected option indices, or None if the viewer has not voted.
        :raises NoActivePollError: If no poll is active.
        """
        self._require_active_poll()
        current_vote = self._repo.get_vote(user.id)
        if current_vote is None:
            return None
        return frozenset(current_vote)

    async def create_poll(
        self,
        *,
        question: str,
        options: list[str],
        hidden: bool,
        requires_auth: bool,
        allow_multiple: bool,
        min_selections: int | None,
        max_selections: int | None,
        duration: int,
    ) -> ActivePoll:
        """Create and activate a new poll.

        :param question: Poll question text.
        :param options: Raw option strings from the create flow.
        :param hidden: Whether live results are hidden from non-moderators.
        :param requires_auth: Whether voters must have authenticated accounts.
        :param allow_multiple: Whether voters may select more than one option.
        :param min_selections: Optional minimum selections for multi-select polls.
        :param max_selections: Optional maximum selections for multi-select polls.
        :param duration: Poll duration in seconds.
        :return: The newly active poll.
        :raises PollAlreadyActiveError: If another poll is already active.
        :raises PollCreateValidationError: If the submitted poll fields are invalid.
        """
        async with self._lifecycle_lock:
            return await self._create_poll_locked(
                question=question,
                options=options,
                hidden=hidden,
                requires_auth=requires_auth,
                allow_multiple=allow_multiple,
                min_selections=min_selections,
                max_selections=max_selections,
                duration=duration,
            )

    async def record_vote(self, user: User, selections: set[int]) -> None:
        """Record a user's vote and broadcast permitted live tally updates.

        :param user: Viewer casting the vote.
        :param selections: Zero-based selected option indices.
        :raises NoActivePollError: If no poll is active.
        :raises PollAuthenticationRequiredError: If the poll requires auth.
        :raises DuplicateVoteError: If the viewer already voted.
        :raises PollVoteValidationError: If selections violate poll rules.
        """
        poll = self._require_active_poll()

        if poll.requires_auth and not user.is_authenticated:
            raise PollAuthenticationRequiredError(user.id)
        if self._repo.get_vote(user.id) is not None:
            raise DuplicateVoteError(user.id)

        self._validate_vote(poll, selections)
        self._repo.set_vote(user.id, selections)
        await self._broadcast_tally(poll)

    async def end(self) -> None:
        """End the active poll, store results in memory, and announce.

        :raises NoActivePollError: If no poll is active.
        """
        async with self._lifecycle_lock:
            await self._end_locked()

    async def cancel(self) -> None:
        """Cancel the active poll without saving results.

        :raises NoActivePollError: If no poll is active.
        """
        async with self._lifecycle_lock:
            await self._cancel_locked()

    async def serve_sse_stream(self, user: User, response: web.StreamResponse) -> None:
        """Serve the SSE lifecycle for a prepared response.

        :param user: Viewer connected to the event stream.
        :param response: Prepared streaming response for SSE writes.
        :raises NoActivePollError: If no poll is active when registration starts.
        """
        poll = self._require_active_poll()
        connection = self._sse.register(user, response=response)

        try:
            if user.is_moderator or not poll.hidden:
                sent = await self._sse.send(
                    connection,
                    SSEEvent.TALLY,
                    self._live_tally_sse_payload(poll),
                )
                if not sent:
                    return
            while self.active_poll is poll:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._poll_done_event.wait(), timeout=15)
                if self.active_poll is not poll:
                    break
                sent = await self._sse.send(
                    connection,
                    SSEEvent.KEEPALIVE,
                    {"time_remaining": poll.time_remaining},
                )
                if not sent:
                    break
        finally:
            self._sse.unregister(connection)

    def begin_stream_grace(self) -> None:
        """Start the stream-stop grace period.

        Replaces any existing grace task. If the stream does not restart before
        the grace period expires, any active poll is cancelled.
        """
        self.cancel_stream_grace()

        async def _grace() -> None:
            self._ctx.logger.info(
                "Stream stopped. Polls grace period: %ds.",
                STREAM_GRACE_PERIOD,
            )
            await asyncio.sleep(STREAM_GRACE_PERIOD)
            if self.active_poll is not None:
                await self.cancel()
                self._ctx.logger.info(
                    "Active poll cancelled after stream grace period."
                )
            self._grace_task = None

        self._grace_task = asyncio.create_task(
            _grace(), name="Polls Module - Stream grace period"
        )

    def cancel_stream_grace(self) -> None:
        """Cancel any running stream-stop grace period."""
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

    async def teardown(self) -> None:
        """Cancel any active poll, close SSE clients, and stop background tasks."""
        self.cancel_stream_grace()
        await self._stop_result_expiry()
        if self.active_poll is not None:
            await self.cancel()
        else:
            await self._sse.close_all()

    def _require_active_poll(self) -> ActivePoll:
        """Return the active poll or raise the domain no-active error.

        :return: The currently active poll.
        :raises NoActivePollError: If no poll is active.
        """
        poll = self.active_poll
        if poll is None:
            raise NoActivePollError
        return poll

    async def _create_poll_locked(
        self,
        *,
        question: str,
        options: list[str],
        hidden: bool,
        requires_auth: bool,
        allow_multiple: bool,
        min_selections: int | None,
        max_selections: int | None,
        duration: int,
    ) -> ActivePoll:
        """Create and activate a poll while the lifecycle lock is held.

        :param question: Poll question text.
        :param options: Raw option strings from the create form.
        :param hidden: Whether live results are hidden from non-moderators.
        :param requires_auth: Whether voters must have authenticated accounts.
        :param allow_multiple: Whether voters may select more than one option.
        :param min_selections: Optional minimum selections for multi-select polls.
        :param max_selections: Optional maximum selections for multi-select polls.
        :param duration: Poll duration in seconds.
        :return: The newly active poll.
        :raises PollAlreadyActiveError: If another poll is already active.
        :raises PollCreateValidationError: If the submitted poll fields are invalid.
        """
        if self.active_poll is not None:
            raise PollAlreadyActiveError

        self._repo.clear_last_result()
        self._cancel_result_expiry()

        (
            normalized_options,
            normalized_min,
            normalized_max,
        ) = self._validate_create_request(
            question=question,
            options=options,
            allow_multiple=allow_multiple,
            min_selections=min_selections,
            max_selections=max_selections,
            duration=duration,
        )

        poll = ActivePoll(
            question=question,
            options=normalized_options,
            hidden=hidden,
            requires_auth=requires_auth,
            allow_multiple=allow_multiple,
            min_selections=normalized_min,
            max_selections=normalized_max,
            duration=duration,
            created_at=datetime.now(UTC),
        )
        self._poll_done_event.set()
        await self._sse.close_all()
        self._repo.create_active_poll(poll)
        self._poll_done_event = asyncio.Event()
        self._start_poll_timer(poll)

        await self._announce_poll(poll)
        self._ctx.logger.info(
            "Poll created: %s (%d options, %ds)",
            poll.question,
            len(poll.options),
            poll.duration,
        )
        return poll

    async def _end_locked(self) -> None:
        """End the active poll while the lifecycle lock is held.

        Stores the completed result, notifies SSE clients, closes event streams,
        and announces the result in chat.

        :raises NoActivePollError: If no poll is active.
        """
        poll = self._repo.pop_active_poll()
        if poll is None:
            raise NoActivePollError

        await self._stop_poll_timer()

        completed = complete_poll(poll, datetime.now(UTC))
        self._repo.set_last_result(completed)
        self._start_result_expiry()

        await self._sse.broadcast(
            SSEEvent.POLL_ENDED,
            {"results_url": self._ctx.routes.url_for("/results")},
        )
        await self._sse.close_all()
        self._poll_done_event.set()
        await self._announce_results(completed)

    async def _cancel_locked(self) -> None:
        """Cancel the active poll while the lifecycle lock is held.

        Clears active state, notifies SSE clients, closes event streams, and
        sends a cancellation message in chat without storing results.

        :raises NoActivePollError: If no poll is active.
        """
        poll = self._repo.pop_active_poll()
        if poll is None:
            raise NoActivePollError

        await self._stop_poll_timer()
        await self._sse.broadcast(SSEEvent.POLL_CANCELLED, {})
        await self._sse.close_all()
        self._poll_done_event.set()
        await self._ctx.owncast_client.send_system_message(
            f"<h3>Poll cancelled: {escape(poll.question)}</h3>",
            unsanitized=True,
        )

    def _validate_create_request(
        self,
        *,
        question: str,
        options: list[str],
        allow_multiple: bool,
        min_selections: int | None,
        max_selections: int | None,
        duration: int,
    ) -> tuple[list[str], int | None, int | None]:
        """Validate and normalize poll creation input.

        :param question: Poll question text.
        :param options: Raw option strings from the create form.
        :param allow_multiple: Whether the poll allows multiple selections.
        :param min_selections: Optional minimum selections for multi-select polls.
        :param max_selections: Optional maximum selections for multi-select polls.
        :param duration: Poll duration in seconds.
        :return: Normalized options, minimum selections, and maximum selections.
        :raises PollCreateValidationError: If any create field is invalid.
        """
        if not question:
            raise PollCreateValidationError("Question is required.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise PollCreateValidationError(
                f"Question must be {MAX_QUESTION_LENGTH} characters or less."
            )

        normalized_options = [option.strip() for option in options]
        if len(normalized_options) < MIN_OPTIONS:
            raise PollCreateValidationError(
                f"At least {MIN_OPTIONS} options are required."
            )
        if len(normalized_options) > MAX_OPTIONS:
            raise PollCreateValidationError(f"Maximum {MAX_OPTIONS} options allowed.")

        for index, option in enumerate(normalized_options):
            if not option:
                raise PollCreateValidationError(f"Option {index + 1} is empty.")
            if len(option) > MAX_OPTION_LENGTH:
                raise PollCreateValidationError(
                    f"Option {index + 1} exceeds {MAX_OPTION_LENGTH} character limit."
                )
        if len(set(normalized_options)) != len(normalized_options):
            raise PollCreateValidationError("Duplicate options are not allowed.")

        if duration < MIN_DURATION or duration > MAX_DURATION:
            raise PollCreateValidationError(
                f"Duration must be between {MIN_DURATION} and {MAX_DURATION}s."
            )

        if not allow_multiple:
            return normalized_options, None, None

        if min_selections is not None and min_selections < 1:
            raise PollCreateValidationError("Minimum selections must be at least 1.")
        if max_selections is not None and max_selections < 1:
            raise PollCreateValidationError("Maximum selections must be at least 1.")

        num_options = len(normalized_options)
        if min_selections is not None and min_selections > num_options:
            raise PollCreateValidationError(
                f"Minimum selections ({min_selections})"
                f" exceeds option count ({num_options})."
            )
        if max_selections is not None and max_selections > num_options:
            raise PollCreateValidationError(
                f"Maximum selections ({max_selections})"
                f" exceeds option count ({num_options})."
            )
        if (
            min_selections is not None
            and max_selections is not None
            and min_selections > max_selections
        ):
            raise PollCreateValidationError(
                "Minimum selections cannot exceed maximum selections."
            )

        return normalized_options, min_selections, max_selections

    def _validate_vote(self, poll: ActivePoll, selections: set[int]) -> None:
        """Validate a submitted vote against the active poll's rules.

        :param poll: Poll receiving the vote.
        :param selections: Zero-based selected option indices.
        :raises PollVoteValidationError: If the vote does not satisfy poll rules.
        """
        if not selections:
            raise PollVoteValidationError("No options selected.")

        num_options = len(poll.options)
        for idx in selections:
            if idx < 0 or idx >= num_options:
                raise PollVoteValidationError(
                    f"Option index {idx} is out of range (0-{num_options - 1})."
                )

        if not poll.allow_multiple and len(selections) > 1:
            raise PollVoteValidationError("Only one selection is allowed.")
        if poll.min_selections is not None and len(selections) < poll.min_selections:
            raise PollVoteValidationError(
                f"At least {poll.min_selections} selections are required."
            )
        if poll.max_selections is not None and len(selections) > poll.max_selections:
            raise PollVoteValidationError(
                f"At most {poll.max_selections} selections are allowed."
            )

    def _start_poll_timer(self, poll: ActivePoll) -> None:
        """Start the background timer that ends a poll after its duration.

        :param poll: Poll whose duration controls the timer.
        """

        async def _poll_timer() -> None:
            if poll.duration > REMINDER_THRESHOLD:
                await asyncio.sleep(poll.duration - REMINDER_THRESHOLD)
                await self._announce_reminder(poll)
                await asyncio.sleep(REMINDER_THRESHOLD)
            else:
                await asyncio.sleep(poll.duration)
            await self.end()

        self._poll_timer_task = asyncio.create_task(
            _poll_timer(), name="Polls Module - Active poll timer"
        )

    async def _stop_poll_timer(self) -> None:
        """Cancel and await the active poll timer, if one exists."""
        task = self._poll_timer_task
        self._poll_timer_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _cancel_result_expiry(self) -> None:
        """Cancel the last-result expiry task without awaiting it."""
        if self._result_expiry_task is not None:
            self._result_expiry_task.cancel()
            self._result_expiry_task = None

    async def _stop_result_expiry(self) -> None:
        """Cancel and await the last-result expiry task, if one exists."""
        task = self._result_expiry_task
        self._result_expiry_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _start_result_expiry(self) -> None:
        """Start the background task that clears the recent completed result."""
        self._cancel_result_expiry()

        async def _expire_result() -> None:
            await asyncio.sleep(RESULT_EXPIRY)
            self._repo.clear_last_result()
            self._result_expiry_task = None
            self._ctx.logger.debug("Last poll result expired after %ds.", RESULT_EXPIRY)

        self._result_expiry_task = asyncio.create_task(
            _expire_result(), name="Polls Module - Result expiry"
        )

    async def _announce_poll(self, poll: ActivePoll) -> None:
        """Announce a newly created poll in chat.

        :param poll: Poll to announce.
        """
        if poll.duration >= REMINDER_THRESHOLD:
            minutes = poll.duration // 60
            time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            time_str = f"{poll.duration} second{'s' if poll.duration != 1 else ''}"

        announcement = (
            f"<h3>Poll: {escape(poll.question)}</h3>"
            f"{self._option_lines(poll.options)}"
            f"<br>{self._voting_instructions(poll)} Voting ends in {time_str}."
        )
        await self._ctx.owncast_client.send_system_message(
            announcement,
            unsanitized=True,
        )

    async def _announce_reminder(self, poll: ActivePoll) -> None:
        """Announce the final poll reminder in chat.

        :param poll: Poll nearing the end of its duration.
        """
        reminder = (
            f"<strong>{REMINDER_THRESHOLD} seconds remaining:</strong>"
            f" {escape(poll.question)}<br>{self._voting_instructions(poll)}"
        )
        await self._ctx.owncast_client.send_system_message(
            reminder,
            unsanitized=True,
        )

    async def _announce_results(
        self,
        completed: CompletedPoll,
    ) -> None:
        """Announce completed poll results in chat.

        :param completed: Completed poll snapshot to announce.
        """
        results_url = self._ctx.routes.url_for("/results")
        results = completed.results
        winners = results.winners
        is_tie = results.is_tie
        total_votes = results.total_votes

        if total_votes == 0:
            outcome = "No votes were cast."
        elif is_tie:
            tied = ", ".join(escape(winner) for winner in winners)
            outcome = f"It's a tie between: {tied}"
        else:
            outcome = f"Winner: <strong>{escape(winners[0])}</strong>"

        announcement = (
            f"<h3>Poll ended: {escape(completed.question)}</h3>"
            f"{outcome}<br>"
            f"Total votes: {total_votes}<br>"
            f'<a href="{escape(results_url)}">'
            "<u>Click here to view the results</u></a>."
        )
        await self._ctx.owncast_client.send_system_message(
            announcement,
            unsanitized=True,
        )

    async def _broadcast_tally(self, poll: ActivePoll) -> None:
        """Broadcast a live tally update to connected SSE clients.

        :param poll: Active poll whose tally should be broadcast.
        """
        await self._sse.broadcast(
            SSEEvent.TALLY,
            self._live_tally_sse_payload(poll),
            moderators_only=poll.hidden,
        )

    def _live_tally_sse_payload(self, poll: ActivePoll) -> dict[str, Any]:
        """Build the SSE payload for a live tally update.

        :param poll: Active poll to summarize.
        :return: JSON-serializable tally payload.
        """
        results = compute_results(poll.options, self._repo.list_votes())
        return {
            "options": poll.options,
            "counts": list(results.counts),
            "total_votes": results.total_votes,
        }

    def _voting_instructions(self, poll: ActivePoll) -> str:
        """Return chat voting instructions for a poll.

        :param poll: Poll whose selection mode determines the instructions.
        :return: HTML-safe instruction text assembled from trusted literals.
        """
        if poll.allow_multiple:
            return "Use <strong>!vote</strong> to get a voting link."
        return "Type a number in chat or use <strong>!vote</strong> to vote."

    def _option_lines(self, options: list[str]) -> str:
        """Render numbered option lines for chat announcements.

        :param options: Poll option labels.
        :return: HTML option list with escaped labels.
        """
        return "".join(
            f"<strong>{index}.</strong> {escape(option)}<br>"
            for index, option in enumerate(options, 1)
        )


def get_manager(ctx: ModuleContext) -> PollManager:
    """Retrieve the polls manager from module state.

    :param ctx: Module context that owns polls runtime state.
    :return: The initialized polls manager.
    :raises RuntimeError: If setup has not stored a manager in module state.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, PollManager):
        raise RuntimeError("PollManager is not initialized.")  # noqa: TRY004  # state error, not a type error
    return manager
