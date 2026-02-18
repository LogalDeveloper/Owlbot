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

"""Management commands for custom commands (add, edit, delete, etc.)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from owlbot.api import CommandContext, on_command

from .handler import (
    custom_command_handler,
    get_aliases_for_command,
    reregister_command,
    resolve_command_name,
    update_and_reregister,
)
from .placeholder_handlers import NAME_RE

if TYPE_CHECKING:
    import aiosqlite


def _clean_raw_name(raw_name: str, prefix: str) -> str:
    """Strip the command prefix (if present) and lowercase a raw name."""
    return raw_name.removeprefix(prefix).lower()


async def _resolve_or_error(ctx: CommandContext, raw_name: str) -> aiosqlite.Row | None:
    """Resolve a raw argument to a custom command row, or send an error message.

    Cleans the raw name, resolves it via the database, and -- if not found --
    sends the appropriate error to chat (distinguishing "not a custom command"
    from "does not exist").

    :param ctx: The command context.
    :param raw_name: The raw argument from the user (may include prefix).
    :return: The command row (name, requires_moderator, cooldown), or None
        if resolution failed.
    """
    prefix = ctx.commands.prefix
    input_name = _clean_raw_name(raw_name, prefix)
    row = await resolve_command_name(ctx.storage, input_name)
    if row is not None:
        return row
    if ctx.commands.exists(input_name):
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
    else:
        await ctx.owncast_client.send_message("That command does not exist.")
    return None


@on_command("addcommand", aliases=["addcmd"], requires_moderator=True)
async def addcommand(ctx: CommandContext) -> None:
    """
    Create a new custom command.

    Usage: !addcommand !name response text

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addcommand {prefix}name response text"
        )
        return

    name = _clean_raw_name(args[0], prefix)

    if not NAME_RE.match(name):
        await ctx.owncast_client.send_message(
            "Invalid command name. Only letters, numbers, and underscores are allowed."
        )
        return

    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addcommand {prefix}name response text"
        )
        return
    response = parts[1]

    if ctx.commands.exists(name):
        await ctx.owncast_client.send_message(
            f"Command {prefix}{name} already exists. "
            f"Use '{prefix}editcommand' to modify it."
        )
        return

    # Register immediately after the exists() check -- both are synchronous,
    # so no other coroutine can interleave.
    default_cooldown = ctx.config.get("default_cooldown", 5)
    ctx.commands.register(
        name=name,
        handler=custom_command_handler,
        cooldown=default_cooldown,
    )

    # Persist to database.  If this fails, roll back the in-memory registration.
    now = datetime.now(UTC).isoformat()
    try:
        await ctx.storage.execute(
            "INSERT INTO commands (name, response, use_count,"
            " created_at, updated_at, requires_moderator,"
            " cooldown) VALUES (?, ?, 0, ?, ?, 0, ?)",
            (name, response, now, now, default_cooldown),
        )
    except Exception:
        ctx.commands.unregister(name)
        raise

    ctx.logger.info(f"Custom command '{name}' created.")
    await ctx.owncast_client.send_message(f"Command {prefix}{name} created.")


@on_command("editcommand", aliases=["editcmd"], requires_moderator=True)
async def editcommand(ctx: CommandContext) -> None:
    """
    Edit an existing custom command's response.

    Usage: !editcommand !name new response

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcommand {prefix}name new response"
        )
        return

    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcommand {prefix}name new response"
        )
        return
    response = parts[1]

    row = await _resolve_or_error(ctx, args[0])
    if row is None:
        return
    name: str = row["name"]

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE commands SET response = ?, updated_at = ? WHERE name = ?",
        (response, now, name),
    )

    ctx.logger.info(f"Custom command '{name}' updated.")
    await ctx.owncast_client.send_message(f"Command {prefix}{name} updated.")


@on_command("deletecommand", aliases=["delcmd"], requires_moderator=True)
async def deletecommand(ctx: CommandContext) -> None:
    """
    Delete a custom command.

    Usage: !deletecommand !name

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}deletecommand {prefix}name"
        )
        return

    input_name = _clean_raw_name(args[0], prefix)
    row = await _resolve_or_error(ctx, args[0])
    if row is None:
        return
    name: str = row["name"]

    if name != input_name:
        await ctx.owncast_client.send_message(
            f"{prefix}{input_name} is an alias of {prefix}{name}. "
            f"Use {prefix}removealias {prefix}{input_name} to remove the alias, "
            f"or {prefix}deletecommand {prefix}{name} to delete the command."
        )
        return

    await ctx.storage.execute("DELETE FROM commands WHERE name = ?", (name,))

    ctx.commands.unregister(name)

    ctx.logger.info(f"Custom command '{name}' deleted.")
    await ctx.owncast_client.send_message(f"Command {prefix}{name} deleted.")


@on_command("commandmodonly", aliases=["cmdmodonly"], requires_moderator=True)
async def commandmodonly(ctx: CommandContext) -> None:
    """
    Toggle moderator-only access for a custom command.

    Usage: !commandmodonly !name <on|off>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}commandmodonly {prefix}name <on|off>"
        )
        return

    setting = args[1].lower()

    if setting not in ("on", "off"):
        await ctx.owncast_client.send_message(
            "Invalid setting. Expected 'on' or 'off'."
        )
        return

    requires_moderator = 1 if setting == "on" else 0

    row = await _resolve_or_error(ctx, args[0])
    if row is None:
        return
    name: str = row["name"]

    await update_and_reregister(ctx, row, requires_moderator=requires_moderator)

    status = "moderator-only" if requires_moderator else "public"
    ctx.logger.info(f"Custom command '{name}' set to {status}.")
    await ctx.owncast_client.send_message(f"Command {prefix}{name} is now {status}.")


@on_command("resetcommand", aliases=["resetcmd"], requires_moderator=True)
async def resetcommand(ctx: CommandContext) -> None:
    """
    Reset a custom command's use counter to 0.

    Usage: !resetcommand !name

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}resetcommand {prefix}name"
        )
        return

    row = await _resolve_or_error(ctx, args[0])
    if row is None:
        return
    name: str = row["name"]

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE commands SET use_count = 0, updated_at = ? WHERE name = ?",
        (now, name),
    )

    ctx.logger.info(f"Custom command '{name}' counter reset to 0.")
    await ctx.owncast_client.send_message(f"Command {prefix}{name} counter reset.")


@on_command("editcounter", aliases=["editcount"], requires_moderator=True)
async def editcounter(ctx: CommandContext) -> None:
    """
    Set, increment, or decrement a named counter.

    Usage: !editcounter <name> <value>

    The value can be an absolute number (e.g. ``15``), or a relative
    modifier prefixed with ``+`` or ``-`` (e.g. ``+1``, ``-3``).
    If the counter does not exist it is created on the fly.

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) != 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcounter name <value>"
        )
        return

    counter_name = args[0].lower()

    if not NAME_RE.match(counter_name):
        await ctx.owncast_client.send_message(
            "Invalid counter name. Only letters, numbers, and underscores are allowed."
        )
        return

    value_str = args[1]

    try:
        value = int(value_str)
    except ValueError:
        await ctx.owncast_client.send_message(
            "Invalid value. Must be an integer (e.g. 15, +1, -3)."
        )
        return

    # Fetch old value (default 0 if counter doesn't exist yet).
    row = await ctx.storage.fetch_one(
        "SELECT value FROM counters WHERE name = ?", (counter_name,)
    )
    old_value = row["value"] if row else 0

    # Leading +/- means relative delta; otherwise absolute.
    new_value = old_value + value if value_str[0] in ("+", "-") else value

    await ctx.storage.execute(
        "INSERT INTO counters (name, value) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = ?",
        (counter_name, new_value, new_value),
    )

    ctx.logger.info(
        f"Counter '{counter_name}' changed from {old_value} to {new_value}."
    )
    await ctx.owncast_client.send_message(
        f"Changed the {counter_name} counter from {old_value} to {new_value}."
    )


@on_command("commandcooldown", aliases=["cmdcooldown"], requires_moderator=True)
async def commandcooldown(ctx: CommandContext) -> None:
    """
    Set or disable a custom command's cooldown.

    Usage: !commandcooldown !name <seconds>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}commandcooldown {prefix}name <seconds>"
        )
        return

    raw_seconds = args[1]

    try:
        seconds = int(raw_seconds)
    except ValueError:
        await ctx.owncast_client.send_message(
            "Invalid cooldown value. Must be a non-negative integer."
        )
        return

    if seconds < 0:
        await ctx.owncast_client.send_message(
            "Cooldown must be a non-negative integer (0 to disable)."
        )
        return

    row = await _resolve_or_error(ctx, args[0])
    if row is None:
        return
    name: str = row["name"]

    await update_and_reregister(ctx, row, cooldown=seconds)

    if seconds == 0:
        ctx.logger.info(f"Custom command '{name}' cooldown disabled.")
        await ctx.owncast_client.send_message(
            f"Command {prefix}{name} cooldown disabled."
        )
    else:
        ctx.logger.info(f"Custom command '{name}' cooldown set to {seconds}s.")
        await ctx.owncast_client.send_message(
            f"Command {prefix}{name} cooldown set to {seconds}s."
        )


@on_command("addalias", requires_moderator=True)
async def addalias(ctx: CommandContext) -> None:
    """
    Add an alias to an existing custom command.

    Usage: !addalias !command !alias

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if len(args) < 2:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addalias {prefix}command {prefix}alias"
        )
        return

    alias = _clean_raw_name(args[1], prefix)

    if not NAME_RE.match(alias):
        await ctx.owncast_client.send_message(
            "Invalid alias name. Only letters, numbers, and underscores are allowed."
        )
        return

    command_row = await _resolve_or_error(ctx, args[0])
    if command_row is None:
        return
    name: str = command_row["name"]

    if alias == name:
        await ctx.owncast_client.send_message(
            "An alias cannot be the same as the command name."
        )
        return

    existing_alias = await ctx.storage.fetch_one(
        "SELECT alias, command_name FROM command_aliases WHERE alias = ?",
        (alias,),
    )
    if existing_alias:
        if existing_alias["command_name"] == name:
            await ctx.owncast_client.send_message(
                f"Alias {prefix}{alias} is already assigned to {prefix}{name}."
            )
        else:
            await ctx.owncast_client.send_message(
                f"Alias {prefix}{alias} is already assigned to "
                f"{prefix}{existing_alias['command_name']}."
            )
        return

    if ctx.commands.exists(alias):
        await ctx.owncast_client.send_message(
            f"Alias {prefix}{alias} conflicts with an existing command."
        )
        return

    current_aliases = await get_aliases_for_command(ctx.storage, name)

    await ctx.storage.execute(
        "INSERT INTO command_aliases (alias, command_name) VALUES (?, ?)",
        (alias, name),
    )

    updated_aliases = [*current_aliases, alias]
    reregister_command(
        ctx.commands,
        name,
        aliases=updated_aliases,
        requires_moderator=bool(command_row["requires_moderator"]),
        cooldown=command_row["cooldown"],
    )

    ctx.logger.info(f"Alias '{alias}' added to custom command '{name}'.")
    await ctx.owncast_client.send_message(
        f"Alias {prefix}{alias} added to {prefix}{name}."
    )


@on_command("removealias", requires_moderator=True)
async def removealias(ctx: CommandContext) -> None:
    """
    Remove an alias from a custom command.

    Usage: !removealias !alias

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list

    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}removealias {prefix}alias"
        )
        return

    alias = _clean_raw_name(args[0], prefix)

    row = await ctx.storage.fetch_one(
        "SELECT a.command_name, c.requires_moderator, c.cooldown "
        "FROM command_aliases a "
        "JOIN commands c ON c.name = a.command_name "
        "WHERE a.alias = ?",
        (alias,),
    )
    if not row:
        await ctx.owncast_client.send_message(f"Alias {prefix}{alias} does not exist.")
        return

    command_name: str = row["command_name"]

    await ctx.storage.execute("DELETE FROM command_aliases WHERE alias = ?", (alias,))

    remaining_aliases = await get_aliases_for_command(ctx.storage, command_name)
    reregister_command(
        ctx.commands,
        command_name,
        aliases=remaining_aliases,
        requires_moderator=bool(row["requires_moderator"]),
        cooldown=row["cooldown"],
    )

    ctx.logger.info(f"Alias '{alias}' removed from custom command '{command_name}'.")
    await ctx.owncast_client.send_message(
        f"Alias {prefix}{alias} removed from {prefix}{command_name}."
    )


@on_command("listcommands", aliases=["listcmds"], cooldown=15)
async def listcommands(ctx: CommandContext) -> None:
    """
    List all custom commands.

    Sends a URL to the command list web page.
    Has a 15-second cooldown to prevent spam.

    Usage: !listcommands

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Custom commands: {url}")
