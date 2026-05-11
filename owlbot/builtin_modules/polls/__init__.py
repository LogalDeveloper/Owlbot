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

"""Polls module for Owlbot.

Allows moderators to create interactive polls in chat with live results
via Server-Sent Events. Supports single and multi-select polls, hidden
results, and authentication requirements.
"""

from __future__ import annotations

from markupsafe import escape

from owlbot.api import (
    EventContext,
    EventType,
    ModuleContext,
    StreamStartedEvent,
    StreamStoppedEvent,
    UserJoinedEvent,
    on_event,
    on_setup,
    on_teardown,
)

from .commands import (
    cancel_poll_command,
    end_poll_command,
    handle_bare_vote,
    poll_command,
    vote_command,
)
from .manager import PollManager, get_manager
from .repository import PollRepository
from .routes import (
    create_page,
    create_submit,
    current_page,
    events_stream,
    mod_cancel,
    mod_end,
    results_page,
    static_asset,
    vote_submit,
)

__all__ = [
    "cancel_poll_command",
    "create_page",
    "create_submit",
    "current_page",
    "end_poll_command",
    "events_stream",
    "handle_bare_vote",
    "mod_cancel",
    "mod_end",
    "on_stream_started",
    "on_stream_stopped",
    "on_user_joined",
    "poll_command",
    "results_page",
    "setup",
    "static_asset",
    "teardown",
    "vote_command",
    "vote_submit",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the polls module.

    :param ctx: Module context with config, storage, and other services.
    """
    repo = PollRepository()
    ctx.state["manager"] = PollManager(ctx, repo)


@on_event(EventType.STREAM_STOPPED)
async def on_stream_stopped(ctx: EventContext[StreamStoppedEvent]) -> None:
    """Begin a grace period when the stream goes offline.

    If the stream does not restart before the grace period expires, any active
    poll is cancelled.

    :param ctx: The event context.
    """
    get_manager(ctx.module).begin_stream_grace()


@on_event(EventType.STREAM_STARTED)
async def on_stream_started(ctx: EventContext[StreamStartedEvent]) -> None:
    """Cancel the stream grace period if the stream restarts.

    :param ctx: The event context.
    """
    get_manager(ctx.module).cancel_stream_grace()


@on_event(EventType.USER_JOINED)
async def on_user_joined(ctx: EventContext[UserJoinedEvent]) -> None:
    """Notify a user joining chat that a poll is active.

    Sends a private message with the poll question, options, and a protected
    current-poll link.

    :param ctx: The event context.
    """
    manager = get_manager(ctx.module)
    poll = manager.active_poll
    if poll is None:
        return

    escaped_question = escape(poll.question)
    option_lines = "".join(
        f"<strong>{i}.</strong> {escape(opt)}<br>"
        for i, opt in enumerate(poll.options, 1)
    )
    selections = manager.get_selections(ctx.event.user)
    vote_url = ctx.session_url_for("/current")
    if selections is not None:
        instructions = (
            f'<a href="{vote_url}"><u>Click here to view live results</u></a>.'
        )
    else:
        instructions = f'<a href="{vote_url}"><u>Click here to vote</u></a>.'

    await ctx.owncast_client.send_system_message_to_client(
        ctx.event.client_id,
        f"<h3>Poll in progress: {escaped_question}</h3>"
        f"{option_lines}"
        f"<br>{instructions}",
        unsanitized=True,
    )


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the polls module.

    :param ctx: Module context.
    """
    manager = get_manager(ctx)
    await manager.teardown()
    ctx.state["manager"] = None
