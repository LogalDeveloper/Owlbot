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

"""Moderator commands for managing timers."""

from owlbot.api import CommandContext, on_command

from .manager import get_manager
from .types import (
    InvalidTimerNameError,
    NegativeLineCountError,
    TimerAlreadyDisabledError,
    TimerAlreadyEnabledError,
    TimerMessageRequiredError,
    TimerNameTakenError,
    TimerNotFoundError,
    parse_interval,
)


@on_command("addtimer", requires_moderator=True)
async def addtimer(ctx: CommandContext) -> None:
    """Create a new empty timer with an optional name.

    Usage: !addtimer [name]

    :param ctx: The command context.
    """
    args = ctx.args_list
    name = args[0].lower() if args else None

    try:
        info = await get_manager(ctx.module).create_timer(name)
    except InvalidTimerNameError:
        await ctx.owncast_client.send_message(
            "Invalid timer name. Must start with a letter or underscore, "
            "followed by letters, numbers, or underscores "
            "(max 32 characters)."
        )
        return
    except TimerNameTakenError as e:
        await ctx.owncast_client.send_message(
            f"A timer named '{e.name}' already exists (#{e.existing_id})."
        )
        return

    await ctx.owncast_client.send_message(f"Timer {info.display} created.")


@on_command("settimermessage", requires_moderator=True)
async def settimermessage(ctx: CommandContext) -> None:
    """Set the message text for a timer.

    Usage: !settimermessage <id|name> <message>

    :param ctx: The command context.
    """
    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await ctx.owncast_client.send_message(
            "Usage: !settimermessage <id|name> <message>"
        )
        return

    identifier, message = parts

    try:
        info = await get_manager(ctx.module).set_message(identifier, message)
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return

    await ctx.owncast_client.send_message(f"Message set for {info.display}.")


@on_command("settimerinterval", requires_moderator=True)
async def settimerinterval(ctx: CommandContext) -> None:
    """Set the interval for a timer.

    Accepts simple durations (15m, 1h30m) or cron expressions (*/15 * * * *).

    Usage: !settimerinterval <id|name> <interval>

    :param ctx: The command context.
    """
    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.owncast_client.send_message(
            "Usage: !settimerinterval <id|name> <interval>"
        )
        return

    identifier, interval_str = parts

    try:
        interval_type, normalized = parse_interval(interval_str)
    except ValueError as e:
        await ctx.owncast_client.send_message(str(e))
        return

    try:
        info = await get_manager(ctx.module).set_interval(
            identifier, interval_type, normalized
        )
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return

    await ctx.owncast_client.send_message(
        f"Interval for {info.display} set to "
        f"{info.interval_value} ({info.interval_type})."
    )


@on_command("settimerlines", requires_moderator=True)
async def settimerlines(ctx: CommandContext) -> None:
    """Set the minimum chat lines between timer firings.

    Usage: !settimerlines <id|name> <count>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if len(args) < 2:
        await ctx.owncast_client.send_message("Usage: !settimerlines <id|name> <count>")
        return

    try:
        count = int(args[1])
    except ValueError:
        await ctx.owncast_client.send_message("Line count must be a number.")
        return

    try:
        info = await get_manager(ctx.module).set_min_chat_lines(args[0], count)
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return
    except NegativeLineCountError:
        await ctx.owncast_client.send_message("Line count cannot be negative.")
        return

    lines = info.min_chat_lines
    label = f"{lines} line(s)" if lines > 0 else "disabled"
    await ctx.owncast_client.send_message(
        f"Minimum chat lines for {info.display} set to {label}."
    )


@on_command("enabletimer", requires_moderator=True)
async def enabletimer(ctx: CommandContext) -> None:
    """Enable a timer.

    Usage: !enabletimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !enabletimer <id|name>")
        return

    try:
        info = await get_manager(ctx.module).enable_timer(args[0])
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return
    except TimerAlreadyEnabledError as e:
        await ctx.owncast_client.send_message(
            f"Timer {e.info.display} is already enabled."
        )
        return
    except TimerMessageRequiredError as e:
        await ctx.owncast_client.send_message(
            f"Cannot enable {e.info.display}: no message set. "
            f"Use !settimermessage to set one first."
        )
        return

    await ctx.owncast_client.send_message(f"Timer {info.display} enabled.")


@on_command("disabletimer", requires_moderator=True)
async def disabletimer(ctx: CommandContext) -> None:
    """Disable a timer.

    Usage: !disabletimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !disabletimer <id|name>")
        return

    try:
        info = await get_manager(ctx.module).disable_timer(args[0])
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return
    except TimerAlreadyDisabledError as e:
        await ctx.owncast_client.send_message(
            f"Timer {e.info.display} is already disabled."
        )
        return

    await ctx.owncast_client.send_message(f"Timer {info.display} disabled.")


@on_command("deletetimer", requires_moderator=True)
async def deletetimer(ctx: CommandContext) -> None:
    """Permanently delete a timer.

    Usage: !deletetimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !deletetimer <id|name>")
        return

    try:
        info = await get_manager(ctx.module).delete_timer(args[0])
    except TimerNotFoundError as e:
        await ctx.owncast_client.send_message(f"Timer '{e.identifier}' not found.")
        return

    await ctx.owncast_client.send_message(f"Timer {info.display} deleted.")


@on_command("listtimers", requires_moderator=True, cooldown=15)
async def listtimers(ctx: CommandContext) -> None:
    """Send the URL to the timer list web page.

    Usage: !listtimers

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Timers: {url}")
