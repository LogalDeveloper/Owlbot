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

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from owlbot.api import CommandContext, on_command

if TYPE_CHECKING:
    import aiosqlite

from .scheduler import get_scheduler, parse_interval

# Timer names: must start with a letter or underscore to avoid ambiguity with
# numeric timer IDs. Letters, numbers, underscores, max 32 chars.
_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,31}$")


async def _resolve_timer(ctx: CommandContext, identifier: str) -> aiosqlite.Row | None:
    """
    Resolve a timer by numeric ID or name.

    Tries parsing as an integer first, then falls back to a name lookup.

    :param ctx: The command context.
    :param identifier: A timer ID or name string.
    :return: The timer row, or None if not found.
    """
    try:
        timer_id = int(identifier)
        ctx.logger.debug("Resolving timer by ID: %d", timer_id)
        row = await ctx.storage.fetch_one(
            "SELECT * FROM timers WHERE id = ?", (timer_id,)
        )
        if row:
            ctx.logger.debug("Found timer by ID: %s", _timer_display(row))
            return row
    except ValueError:
        pass

    ctx.logger.debug("Resolving timer by name: %s", identifier)
    row = await ctx.storage.fetch_one(
        "SELECT * FROM timers WHERE name = ?", (identifier.lower(),)
    )
    if row:
        ctx.logger.debug("Found timer by name: %s", _timer_display(row))
    else:
        ctx.logger.debug("Timer '%s' not found.", identifier)
    return row


def _timer_display(row: aiosqlite.Row) -> str:
    """
    Format a timer's display identifier.

    :param row: Timer database row.
    :return: Display string like "timer_name (#3)" or "timer #3".
    """
    if row["name"]:
        return f"{row['name']} (#{row['id']})"
    return f"timer #{row['id']}"


@on_command("addtimer", requires_moderator=True)
async def addtimer(ctx: CommandContext) -> None:
    """
    Create a new empty timer with an optional name.

    Usage: !addtimer [name]

    :param ctx: The command context.
    """
    args = ctx.args_list
    name = None

    if args:
        name = args[0].lower()
        if not _NAME_PATTERN.match(name):
            await ctx.owncast_client.send_message(
                "Invalid timer name. Must start with a letter or underscore, "
                "followed by letters, numbers, or underscores (max 32 characters)."
            )
            return

        existing = await ctx.storage.fetch_one(
            "SELECT id FROM timers WHERE name = ?", (name,)
        )
        if existing:
            await ctx.owncast_client.send_message(
                f"A timer named '{name}' already exists (#{existing['id']})."
            )
            return

    now = datetime.now(UTC).isoformat()
    cursor = await ctx.storage.execute(
        "INSERT INTO timers (name, message, interval_value, interval_type, "
        "min_chat_lines, enabled, created_at, updated_at) "
        "VALUES (?, NULL, '15m', 'simple', 0, 0, ?, ?)",
        (name, now, now),
    )

    timer_id = cursor.lastrowid
    display = f"{name} (#{timer_id})" if name else f"#{timer_id}"
    ctx.logger.info(f"Timer {display} created by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Timer {display} created.")


@on_command("settimermessage", requires_moderator=True)
async def settimermessage(ctx: CommandContext) -> None:
    """
    Set the message text for a timer.

    Usage: !settimermessage <id|name> <message>

    :param ctx: The command context.
    """
    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.owncast_client.send_message(
            "Usage: !settimermessage <id|name> <message>"
        )
        return

    identifier, message = parts
    if not message.strip():
        await ctx.owncast_client.send_message(
            "Usage: !settimermessage <id|name> <message>"
        )
        return

    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE timers SET message = ?, updated_at = ? WHERE id = ?",
        (message, now, row["id"]),
    )

    display = _timer_display(row)
    ctx.logger.info(f"Timer {display} message updated by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Message set for {display}.")
    if row["enabled"]:
        get_scheduler().reschedule()


@on_command("settimerinterval", requires_moderator=True)
async def settimerinterval(ctx: CommandContext) -> None:
    """
    Set the interval for a timer.

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
    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    try:
        interval_type, normalized = parse_interval(interval_str)
    except ValueError as e:
        await ctx.owncast_client.send_message(str(e))
        return

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE timers SET interval_type = ?, interval_value = ?, updated_at = ? "
        "WHERE id = ?",
        (interval_type, normalized, now, row["id"]),
    )

    display = _timer_display(row)
    ctx.logger.info(
        f"Timer {display} interval set to {normalized} by {ctx.user.display_name}."
    )
    await ctx.owncast_client.send_message(
        f"Interval for {display} set to {normalized} ({interval_type})."
    )
    if row["enabled"]:
        get_scheduler().reschedule()


@on_command("settimerlines", requires_moderator=True)
async def settimerlines(ctx: CommandContext) -> None:
    """
    Set the minimum chat lines between timer firings.

    Usage: !settimerlines <id|name> <count>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if len(args) < 2:
        await ctx.owncast_client.send_message("Usage: !settimerlines <id|name> <count>")
        return

    identifier = args[0]
    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    try:
        count = int(args[1])
    except ValueError:
        await ctx.owncast_client.send_message("Line count must be a number.")
        return

    if count < 0:
        await ctx.owncast_client.send_message("Line count cannot be negative.")
        return

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE timers SET min_chat_lines = ?, updated_at = ? WHERE id = ?",
        (count, now, row["id"]),
    )

    display = _timer_display(row)
    label = f"{count} line(s)" if count > 0 else "disabled"
    ctx.logger.info(
        f"Timer {display} min chat lines set to {count} by {ctx.user.display_name}."
    )
    await ctx.owncast_client.send_message(
        f"Minimum chat lines for {display} set to {label}."
    )
    if row["enabled"]:
        get_scheduler().reschedule()


@on_command("enabletimer", requires_moderator=True)
async def enabletimer(ctx: CommandContext) -> None:
    """
    Enable a timer.

    Won't enable a timer that has no message set.

    Usage: !enabletimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !enabletimer <id|name>")
        return

    identifier = args[0]
    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    display = _timer_display(row)

    if row["enabled"]:
        await ctx.owncast_client.send_message(f"Timer {display} is already enabled.")
        return

    if not row["message"]:
        await ctx.owncast_client.send_message(
            f"Cannot enable {display}: no message set. "
            f"Use !settimermessage to set one first."
        )
        return

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE timers SET enabled = 1, updated_at = ? WHERE id = ?",
        (now, row["id"]),
    )

    # Start tracking chat lines for this timer.
    get_scheduler().counted_ids[row["id"]] = set()
    ctx.logger.debug("Started chat line tracking for timer %s.", display)

    ctx.logger.info(f"Timer {display} enabled by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Timer {display} enabled.")
    get_scheduler().reschedule()


@on_command("disabletimer", requires_moderator=True)
async def disabletimer(ctx: CommandContext) -> None:
    """
    Disable a timer.

    Usage: !disabletimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !disabletimer <id|name>")
        return

    identifier = args[0]
    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    display = _timer_display(row)

    if not row["enabled"]:
        await ctx.owncast_client.send_message(f"Timer {display} is already disabled.")
        return

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE timers SET enabled = 0, updated_at = ? WHERE id = ?",
        (now, row["id"]),
    )

    # Stop tracking chat lines for this timer.
    get_scheduler().counted_ids.pop(row["id"], None)
    ctx.logger.debug("Stopped chat line tracking for timer %s.", display)

    ctx.logger.info(f"Timer {display} disabled by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Timer {display} disabled.")
    get_scheduler().reschedule()


@on_command("deletetimer", requires_moderator=True)
async def deletetimer(ctx: CommandContext) -> None:
    """
    Permanently delete a timer.

    Usage: !deletetimer <id|name>

    :param ctx: The command context.
    """
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message("Usage: !deletetimer <id|name>")
        return

    identifier = args[0]
    row = await _resolve_timer(ctx, identifier)
    if not row:
        await ctx.owncast_client.send_message(f"Timer '{identifier}' not found.")
        return

    display = _timer_display(row)
    await ctx.storage.execute("DELETE FROM timers WHERE id = ?", (row["id"],))

    # Stop tracking chat lines.
    get_scheduler().counted_ids.pop(row["id"], None)

    ctx.logger.info(f"Timer {display} deleted by {ctx.user.display_name}.")
    await ctx.owncast_client.send_message(f"Timer {display} deleted.")
    get_scheduler().reschedule()


@on_command("listtimers", requires_moderator=True, cooldown=15)
async def listtimers(ctx: CommandContext) -> None:
    """
    Send the URL to the timer list web page.

    Usage: !listtimers

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Timers: {url}")
