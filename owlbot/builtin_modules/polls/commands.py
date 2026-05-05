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

"""Chat commands for the polls module."""

from __future__ import annotations

from markupsafe import escape

from owlbot.api import (
    ChatEvent,
    CommandContext,
    EventContext,
    EventType,
    on_command,
    on_event,
)

from .manager import PollError, get_manager


@on_command("poll", requires_moderator=True, cooldown=3)
async def poll_command(ctx: CommandContext) -> None:
    """Start the poll creation flow.

    Sends the poll creator a private protected link to the creation form.

    :param ctx: The command context.
    """
    manager = get_manager(ctx.module)

    if manager.active_poll is not None:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id, "A poll is already active."
        )
        return

    url = ctx.session_url_for("/create")
    await ctx.owncast_client.send_system_message_to_client(
        ctx.chat_event.client_id,
        f'<a href="{url}"><u>Click here to set up a poll</u></a>.',
        unsanitized=True,
    )


@on_command("endpoll", requires_moderator=True)
async def end_poll_command(ctx: CommandContext) -> None:
    """End the active poll and announce results.

    :param ctx: The command context.
    """
    manager = get_manager(ctx.module)

    try:
        await manager.end()
    except PollError as e:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id, str(e)
        )


@on_command("cancelpoll", requires_moderator=True)
async def cancel_poll_command(ctx: CommandContext) -> None:
    """Cancel the active poll without announcing results.

    :param ctx: The command context.
    """
    manager = get_manager(ctx.module)

    try:
        await manager.cancel()
    except PollError as e:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id, str(e)
        )


@on_command("vote")
async def vote_command(ctx: CommandContext) -> None:
    """Cast a vote or get a voting link.

    Hides the vote message, then either processes a quick numeric vote
    (single-select only) or sends a private voting page link.

    :param ctx: The command context.
    """
    manager = get_manager(ctx.module)

    # Hide the vote message to keep votes private and prevent chat spam.
    await ctx.owncast_client.set_message_visibility(
        [ctx.chat_event.message_id], visible=False
    )

    poll = manager.active_poll

    if poll is None:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id, "There is no active poll."
        )
        return

    user_id = ctx.user.id
    vote_url = ctx.session_url_for("/vote")

    # Check authentication requirement.
    if poll.requires_auth and not ctx.user.is_authenticated:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id,
            "This poll requires an authenticated account to vote. "
            f'<a href="{vote_url}">'
            "<u>Click here to view live results</u></a>.",
            unsanitized=True,
        )
        return

    # Check if the user has already voted.
    if user_id in poll.votes:
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id,
            "You have already voted in this poll. "
            f'<a href="{vote_url}"><u>Click here to view live results</u></a>.',
            unsanitized=True,
        )
        return

    args = ctx.args_list

    if args and not poll.allow_multiple:
        # Quick vote by number (single-select only).
        try:
            choice = int(args[0])
        except ValueError:
            choice = None

        if choice is None or choice < 1 or choice > len(poll.options):
            await ctx.owncast_client.send_system_message_to_client(
                ctx.chat_event.client_id,
                f"Invalid option number."
                f" Use !vote 1-{len(poll.options)} to vote, or "
                f'<a href="{vote_url}">'
                "<u>click here to cast your vote</u></a>.",
                unsanitized=True,
            )
            return

        # Convert 1-based to 0-based index.
        selection = choice - 1

        await manager.record_vote(
            ctx.user,
            {selection},
        )
        await ctx.owncast_client.send_system_message_to_client(
            ctx.chat_event.client_id,
            f"Your vote for #{choice} ({escape(poll.options[selection])})"
            " has been recorded. "
            f'<a href="{vote_url}"><u>Click here to view live results</u></a>.',
            unsanitized=True,
        )
        return

    # Send voting page link.
    if args and poll.allow_multiple:
        message = (
            "This poll allows multiple selections,"
            " so votes must be cast through the voting page. "
            f'<a href="{vote_url}">'
            "<u>Click here to cast your vote</u></a>."
        )
    else:
        message = f'<a href="{vote_url}"><u>Click here to cast your vote</u></a>.'
    await ctx.owncast_client.send_system_message_to_client(
        ctx.chat_event.client_id,
        message,
        unsanitized=True,
    )


@on_event(EventType.CHAT)
async def handle_bare_vote(ctx: EventContext[ChatEvent]) -> None:
    """Allow voting by typing a bare number in chat during active polls.

    For single-select polls, records the vote directly. For multi-select
    polls, sends the user a voting page link instead. The message is hidden
    to keep votes private and prevent chat spam.

    :param ctx: The event context.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll
    if poll is None:
        return

    event = ctx.event

    text = event.raw_body.strip()

    try:
        choice = int(text)
    except ValueError:
        return

    # Validate the number is within the option range.
    if choice < 1 or choice > len(poll.options):
        return

    # Hide the message to keep votes private and prevent chat spam.
    await ctx.owncast_client.set_message_visibility([event.message_id], visible=False)
    ctx.stop_propagation("bare number vote in active poll")

    vote_url = ctx.session_url_for("/vote")

    # Check authentication requirement.
    if poll.requires_auth and not event.user.is_authenticated:
        await ctx.owncast_client.send_system_message_to_client(
            event.client_id,
            "This poll requires an authenticated account to vote. "
            f'<a href="{vote_url}">'
            "<u>Click here to view live results</u></a>.",
            unsanitized=True,
        )
        return

    # Check if the user has already voted.
    if event.user.id in poll.votes:
        await ctx.owncast_client.send_system_message_to_client(
            event.client_id,
            "You have already voted in this poll. "
            f'<a href="{vote_url}"><u>Click here to view live results</u></a>.',
            unsanitized=True,
        )
        return

    # Multi-select polls can't use bare number voting; send a link instead.
    if poll.allow_multiple:
        await ctx.owncast_client.send_system_message_to_client(
            event.client_id,
            "This poll allows multiple selections,"
            " so votes must be cast through the voting page. "
            f'<a href="{vote_url}">'
            "<u>Click here to cast your vote</u></a>.",
            unsanitized=True,
        )
        return

    # Record the vote.
    selection = choice - 1

    await manager.record_vote(
        event.user,
        {selection},
    )
    await ctx.owncast_client.send_system_message_to_client(
        event.client_id,
        f"Your vote for #{choice} ({escape(poll.options[selection])})"
        " has been recorded. "
        f'<a href="{vote_url}"><u>Click here to view live results</u></a>.',
        unsanitized=True,
    )
