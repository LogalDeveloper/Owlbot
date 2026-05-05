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

"""Poll lifecycle manager for the polls module.

Centralizes all poll state transitions (creation, voting, ending, cancellation)
and SSE broadcasting into a single class, keeping route and command handlers thin.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from markupsafe import escape

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
    PollSSEClient,
    SSEEvent,
    compute_results,
    sse_payload,
)

if TYPE_CHECKING:
    from aiohttp import web

    from owlbot.api import ModuleContext, User


class PollError(Exception):
    """Raised when a poll operation fails."""


class PollManager:
    """Owns all poll lifecycle operations.

    Manages poll creation, voting, SSE broadcasting, ending, and cancellation.
    Initialized during module setup and stored in module state.

    :param ctx: The module context providing storage, owncast client, routes, etc.
    """

    def __init__(self, ctx: ModuleContext) -> None:
        """Initialize the manager with the module context."""
        self._ctx = ctx
        self._active_poll: ActivePoll | None = None
        self._grace_task: asyncio.Task[None] | None = None
        self._last_result: CompletedPoll | None = None
        self._result_expiry_task: asyncio.Task[None] | None = None

    @property
    def active_poll(self) -> ActivePoll | None:
        """The currently active poll, if any."""
        return self._active_poll

    @property
    def last_result(self) -> CompletedPoll | None:
        """The most recently completed poll result, if any."""
        return self._last_result

    def _cancel_result_expiry(self) -> None:
        """Cancel any running result expiry task."""
        if self._result_expiry_task is not None:
            self._result_expiry_task.cancel()
            self._result_expiry_task = None

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

        Constructs an :class:`ActivePoll`, starts the auto-end timer task, and
        announces in chat.

        :param question: The poll question.
        :param options: List of poll option strings.
        :param hidden: Whether vote results are hidden until the poll ends.
        :param requires_auth: Whether voters must be authenticated.
        :param allow_multiple: Whether voters can select multiple options.
        :param min_selections: Minimum selections required (multi-select).
        :param max_selections: Maximum selections allowed (multi-select).
        :param duration: Poll duration in seconds.
        :return: The newly created :class:`ActivePoll`.
        :raises PollError: If validation fails or a poll is already active.
        """
        self._last_result = None
        self._cancel_result_expiry()

        if self._active_poll is not None:
            raise PollError("A poll is already active.")

        # Validate inputs.
        if not question:
            raise PollError("Question is required.")
        if len(question) > MAX_QUESTION_LENGTH:
            raise PollError(
                f"Question must be {MAX_QUESTION_LENGTH} characters or less."
            )

        # Strip and validate options.
        options = [o.strip() for o in options]
        if len(options) < MIN_OPTIONS:
            raise PollError(f"At least {MIN_OPTIONS} options are required.")
        if len(options) > MAX_OPTIONS:
            raise PollError(f"Maximum {MAX_OPTIONS} options allowed.")
        for i, opt in enumerate(options):
            if not opt:
                raise PollError(f"Option {i + 1} is empty.")
            if len(opt) > MAX_OPTION_LENGTH:
                raise PollError(
                    f"Option {i + 1} exceeds {MAX_OPTION_LENGTH} character limit."
                )
        if len(set(options)) != len(options):
            raise PollError("Duplicate options are not allowed.")

        if duration < MIN_DURATION or duration > MAX_DURATION:
            raise PollError(
                f"Duration must be between {MIN_DURATION} and {MAX_DURATION}s."
            )

        # Ignore selection limits for single-select polls.
        if not allow_multiple:
            min_selections = None
            max_selections = None

        if min_selections is not None and min_selections < 1:
            raise PollError("Minimum selections must be at least 1.")
        if max_selections is not None and max_selections < 1:
            raise PollError("Maximum selections must be at least 1.")

        num_options = len(options)
        if min_selections is not None and min_selections > num_options:
            raise PollError(
                f"Minimum selections ({min_selections})"
                f" exceeds option count ({num_options})."
            )
        if max_selections is not None and max_selections > num_options:
            raise PollError(
                f"Maximum selections ({max_selections})"
                f" exceeds option count ({num_options})."
            )
        if (
            min_selections is not None
            and max_selections is not None
            and min_selections > max_selections
        ):
            raise PollError("Minimum selections cannot exceed maximum selections.")

        now = datetime.now(UTC)

        poll = ActivePoll(
            question=question,
            options=options,
            hidden=hidden,
            requires_auth=requires_auth,
            allow_multiple=allow_multiple,
            min_selections=min_selections,
            max_selections=max_selections,
            duration=duration,
            created_at=now,
        )

        self._active_poll = poll

        # Start auto-end timer task with reminder before expiry.
        async def _poll_timer() -> None:
            if duration > REMINDER_THRESHOLD:
                await asyncio.sleep(duration - REMINDER_THRESHOLD)
                if poll.allow_multiple:
                    instructions = "Use <strong>!vote</strong> to get a voting link."
                else:
                    instructions = (
                        "Type a number in chat or use <strong>!vote</strong> to vote."
                    )

                escaped_question = escape(poll.question)
                reminder = (
                    f"<strong>{REMINDER_THRESHOLD} seconds remaining:</strong>"
                    f" {escaped_question}<br>{instructions}"
                )
                await self._ctx.owncast_client.send_system_message(
                    reminder, unsanitized=True
                )
                await asyncio.sleep(REMINDER_THRESHOLD)
            else:
                await asyncio.sleep(duration)
            await self.end()

        poll.timer_task = asyncio.create_task(_poll_timer())

        # Announce in chat.
        if duration >= REMINDER_THRESHOLD:
            minutes = duration // 60
            time_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            time_str = f"{duration} second{'s' if duration != 1 else ''}"

        escaped_question = escape(question)
        option_lines = "".join(
            f"<strong>{i}.</strong> {escape(opt)}<br>"
            for i, opt in enumerate(options, 1)
        )

        if allow_multiple:
            instructions = "Use <strong>!vote</strong> to get a voting link."
        else:
            instructions = (
                "Type a number in chat or use <strong>!vote</strong> to vote."
            )

        announcement = (
            f"<h3>Poll: {escaped_question}</h3>"
            f"{option_lines}"
            f"<br>{instructions} Voting ends in {time_str}."
        )
        await self._ctx.owncast_client.send_system_message(
            announcement, unsanitized=True
        )

        self._ctx.logger.info(
            "Poll created: %s (%d options, %ds)",
            question,
            len(options),
            duration,
        )

        return poll

    async def record_vote(
        self,
        user: User,
        selections: set[int],
    ) -> None:
        """Record a user's vote and broadcast an SSE update.

        :param user: The voter.
        :param selections: Set of selected option indices (0-based).
        :raises PollError: If no poll is active or the selections are invalid.
        """
        poll = self._active_poll
        if poll is None:
            raise PollError("No active poll.")

        # Check authentication requirement.
        if poll.requires_auth and not user.is_authenticated:
            raise PollError("This poll requires an authenticated account to vote.")

        if user.id in poll.votes:
            raise PollError("You have already voted.")

        if not selections:
            raise PollError("No options selected.")

        num_options = len(poll.options)
        for idx in selections:
            if idx < 0 or idx >= num_options:
                raise PollError(
                    f"Option index {idx} is out of range (0-{num_options - 1})."
                )

        if not poll.allow_multiple and len(selections) > 1:
            raise PollError("Only one selection is allowed.")

        if poll.min_selections is not None and len(selections) < poll.min_selections:
            raise PollError(f"At least {poll.min_selections} selections are required.")

        if poll.max_selections is not None and len(selections) > poll.max_selections:
            raise PollError(f"At most {poll.max_selections} selections are allowed.")

        poll.votes[user.id] = selections

        # Broadcast SSE update.
        results = compute_results(poll.options, poll.votes)
        await self._broadcast_sse(
            poll,
            SSEEvent.TALLY,
            {
                "options": poll.options,
                "counts": results["counts"],
                "total_votes": results["total_votes"],
            },
        )

    async def end(self) -> None:
        """End the active poll, store results in memory, and announce.

        :raises PollError: If no poll is currently active.
        """
        poll = self._active_poll
        if poll is None:
            raise PollError("No active poll.")

        # Clear state synchronously before any await to prevent a concurrent
        # end() call (e.g. timer + !endpoll) from passing the guard above.
        self._active_poll = None

        # Cancel the timer task.  When the timer itself calls end(),
        # skip entirely: cancelling the current task would poison
        # every subsequent await with CancelledError.
        if (
            poll.timer_task is not None
            and poll.timer_task is not asyncio.current_task()
        ):
            poll.timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll.timer_task

        # Compute results and sort options by vote count descending,
        # then alphabetically, so the stored data is display-ready.
        results = compute_results(poll.options, poll.votes)
        counts: list[int] = results["counts"]
        sorted_indices = sorted(
            range(len(poll.options)),
            key=lambda i: (-counts[i], poll.options[i].lower()),
        )
        sorted_options = [poll.options[i] for i in sorted_indices]
        results["counts"] = [counts[i] for i in sorted_indices]

        # Store result in memory.
        self._last_result = CompletedPoll(
            question=poll.question,
            options=sorted_options,
            results=results,
            created_at=poll.created_at,
            ended_at=datetime.now(UTC),
        )

        # Start expiry timer.
        self._cancel_result_expiry()

        async def _expire_result() -> None:
            await asyncio.sleep(RESULT_EXPIRY)
            self._last_result = None
            self._result_expiry_task = None
            self._ctx.logger.debug("Last poll result expired after %ds.", RESULT_EXPIRY)

        self._result_expiry_task = asyncio.create_task(_expire_result())

        # Announce results in chat.
        results_url = self._ctx.routes.url_for("/results")
        winners: list[str] = results["winners"]
        is_tie: bool = results["is_tie"]
        total_votes: int = results["total_votes"]

        if total_votes == 0:
            outcome = "No votes were cast."
        elif is_tie:
            tied = ", ".join(escape(w) for w in winners)
            outcome = f"It's a tie between: {tied}"
        else:
            outcome = f"Winner: <strong>{escape(winners[0])}</strong>"

        escaped_question = escape(poll.question)
        announcement = (
            f"<h3>Poll ended: {escaped_question}</h3>"
            f"{outcome}<br>"
            f"Total votes: {total_votes}<br>"
            f'<a href="{escape(results_url)}">'
            "<u>Click here to view the results</u></a>."
        )
        await self._ctx.owncast_client.send_system_message(
            announcement, unsanitized=True
        )

        # Wake up SSE keepalive loops so they exit promptly.
        poll.done_event.set()

        # Send poll_ended SSE event.
        await self._broadcast_sse(
            poll,
            SSEEvent.POLL_ENDED,
            {"results_url": results_url},
        )
        await self._close_sse_clients(poll)

    async def cancel(self) -> None:
        """Cancel the active poll without saving results.

        :raises PollError: If no poll is currently active.
        """
        poll = self._active_poll
        if poll is None:
            raise PollError("No active poll.")

        # Clear state synchronously before any await to prevent a concurrent
        # cancel() call from passing the guard above.
        self._active_poll = None

        # Cancel the timer task.
        if poll.timer_task is not None:
            poll.timer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll.timer_task

        # Wake up SSE keepalive loops so they exit promptly.
        poll.done_event.set()

        # Announce cancellation in chat.
        await self._ctx.owncast_client.send_system_message(
            f"<h3>Poll cancelled: {escape(poll.question)}</h3>",
            unsanitized=True,
        )

        # Send poll_cancelled SSE event.
        await self._broadcast_sse(poll, SSEEvent.POLL_CANCELLED, {})
        await self._close_sse_clients(poll)

    async def register_sse_client(
        self,
        user: User,
        *,
        response: web.StreamResponse,
    ) -> None:
        """Register an SSE client connection for a user.

        Sends an initial ``tally`` event so the client has current
        results without needing them baked into the page.

        :param user: Owncast user associated with the browser session.
        :param response: The SSE stream response object.
        :raises PollError: If no poll is currently active.
        """
        poll = self._active_poll
        if poll is None:
            raise PollError("No active poll.")

        poll.sse_clients[user.id] = PollSSEClient(
            user_id=user.id,
            is_moderator=user.is_moderator,
            response=response,
        )

        # Send initial vote counts if this client should see results.
        if user.is_moderator or not poll.hidden:
            results = compute_results(poll.options, poll.votes)
            payload = sse_payload(
                SSEEvent.TALLY,
                {
                    "options": poll.options,
                    "counts": results["counts"],
                    "total_votes": results["total_votes"],
                },
            )
            await response.write(payload)

    def begin_stream_grace(self) -> None:
        """Start the stream-stop grace period.

        If the stream does not restart within :data:`STREAM_GRACE_PERIOD`
        seconds, any active poll is cancelled.
        """
        self.cancel_stream_grace()

        async def _grace() -> None:
            self._ctx.logger.info(
                "Stream stopped. Polls grace period: %ds.",
                STREAM_GRACE_PERIOD,
            )
            await asyncio.sleep(STREAM_GRACE_PERIOD)
            if self._active_poll is not None:
                await self.cancel()
                self._ctx.logger.info(
                    "Active poll cancelled after stream grace period."
                )
            self._grace_task = None

        self._grace_task = asyncio.create_task(_grace())

    def cancel_stream_grace(self) -> None:
        """Cancel any running stream-stop grace period."""
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

    async def teardown(self) -> None:
        """Clean up on module unload.

        Cancels grace period and any active poll.
        """
        self.cancel_stream_grace()
        self._cancel_result_expiry()
        if self._active_poll is not None:
            await self.cancel()

    async def _broadcast_sse(
        self, poll: ActivePoll, event_type: SSEEvent, data: dict[str, Any]
    ) -> None:
        """Broadcast an SSE event to connected clients.

        For ``tally`` events on hidden polls, only moderator clients receive
        the update. All other event types are sent to every client.

        :param poll: The active poll.
        :param event_type: The SSE event type.
        :param data: The event data to serialize.
        """
        clients = poll.sse_clients
        payload = sse_payload(event_type, data)

        # Determine which clients to send to.
        if poll.hidden and event_type == SSEEvent.TALLY:
            # Only moderators receive vote updates for hidden polls.
            targets = {
                user_id: client
                for user_id, client in clients.items()
                if client.is_moderator
            }
        else:
            targets = dict(clients)

        if not targets:
            return

        # Send to all targets concurrently.
        user_ids = list(targets.keys())
        responses = list(targets.values())
        results = await asyncio.gather(
            *(client.response.write(payload) for client in responses),
            return_exceptions=True,
        )

        # Remove failed clients.
        for user_id, result in zip(user_ids, results, strict=True):
            if isinstance(result, BaseException):
                self._ctx.logger.debug("Removing disconnected SSE client: %s", user_id)
                clients.pop(user_id, None)

    async def _close_sse_clients(self, poll: ActivePoll) -> None:
        """Close all SSE client connections for a poll.

        :param poll: The active poll whose clients should be closed.
        """
        for client in poll.sse_clients.values():
            with contextlib.suppress(Exception):
                await client.response.write_eof()
        poll.sse_clients.clear()


def get_manager(ctx: ModuleContext) -> PollManager:
    """Retrieve the PollManager from the module context's state.

    :param ctx: The module context.
    :return: The PollManager instance.
    :raises RuntimeError: If PollManager has not been initialized.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, PollManager):
        raise RuntimeError("PollManager is not initialized.")  # noqa: TRY004  # state error, not a type error
    return manager
