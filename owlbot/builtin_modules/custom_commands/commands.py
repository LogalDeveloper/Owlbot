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

"""Chat command handlers for custom commands (add, edit, delete, etc.)."""

from __future__ import annotations

from owlbot.api import CommandContext, on_command

from .manager import get_manager
from .types import (
    AliasAlreadyExistsError,
    AliasIsCanonicalNameError,
    CannotDeleteByAliasError,
    CommandAlreadyExistsError,
    CommandNotFoundError,
    InactiveCommandError,
    InvalidNameError,
    NotCustomCommandError,
)


def _clean_name(raw: str, prefix: str) -> str:
    """Strip the command prefix (if present) and lowercase a raw name."""
    return raw.removeprefix(prefix).lower()


@on_command("addcommand", aliases=["addcmd"], requires_moderator=True)
async def addcommand(ctx: CommandContext) -> None:
    """Create a new custom command.

    Usage: !addcommand !<name> <response text>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addcommand {prefix}<name> <response text>"
        )
        return

    name = _clean_name(args[0], prefix)
    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addcommand {prefix}<name> <response text>"
        )
        return
    response = parts[1]

    try:
        cmd = await get_manager(ctx.module).create_command(name, response)
    except InvalidNameError:
        await ctx.owncast_client.send_message(
            "Invalid command name. Only letters, numbers, and underscores are allowed."
        )
        return
    except CommandAlreadyExistsError:
        await ctx.owncast_client.send_message(
            f"Command {prefix}{name} already exists. "
            f"Use '{prefix}editcommand' to modify it."
        )
        return

    await ctx.owncast_client.send_message(f"Command {prefix}{cmd.name} created.")


@on_command("editcommand", aliases=["editcmd"], requires_moderator=True)
async def editcommand(ctx: CommandContext) -> None:
    """Edit an existing custom command's response.

    Usage: !editcommand !<name> <new response>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcommand {prefix}<name> <new response>"
        )
        return

    name = _clean_name(args[0], prefix)
    parts = ctx.args.split(maxsplit=1)
    if len(parts) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcommand {prefix}<name> <new response>"
        )
        return
    response = parts[1]

    try:
        cmd = await get_manager(ctx.module).edit_command(name, response)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except InactiveCommandError as e:
        await ctx.owncast_client.send_message(
            f"Custom command {prefix}{e.name} is disabled because another module "
            f"registered the same name. Delete it or resolve the conflict to re-enable."
        )
        return

    await ctx.owncast_client.send_message(f"Command {prefix}{cmd.name} updated.")


@on_command("deletecommand", aliases=["delcmd"], requires_moderator=True)
async def deletecommand(ctx: CommandContext) -> None:
    """Delete a custom command.

    Usage: !deletecommand !<name>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}deletecommand {prefix}<name>"
        )
        return

    name = _clean_name(args[0], prefix)

    try:
        cmd = await get_manager(ctx.module).delete_command(name)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except CannotDeleteByAliasError as e:
        await ctx.owncast_client.send_message(
            f"{prefix}{e.alias} is an alias of {prefix}{e.canonical}. "
            f"Use {prefix}removealias {prefix}{e.alias} to remove the alias, "
            f"or {prefix}deletecommand {prefix}{e.canonical} "
            "to delete the command."
        )
        return

    await ctx.owncast_client.send_message(f"Command {prefix}{cmd.name} deleted.")


@on_command("commandmodonly", aliases=["cmdmodonly"], requires_moderator=True)
async def commandmodonly(ctx: CommandContext) -> None:
    """Toggle moderator-only access for a custom command.

    Usage: !commandmodonly !<name> <on|off>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}commandmodonly {prefix}<name> <on|off>"
        )
        return

    setting = args[1].lower()
    if setting not in ("on", "off"):
        await ctx.owncast_client.send_message(
            "Invalid setting. Expected 'on' or 'off'."
        )
        return

    name = _clean_name(args[0], prefix)
    enabled = setting == "on"

    try:
        cmd = await get_manager(ctx.module).set_mod_only(name, enabled=enabled)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except InactiveCommandError as e:
        await ctx.owncast_client.send_message(
            f"Custom command {prefix}{e.name} is disabled because another module "
            f"registered the same name. Delete it or resolve the conflict to re-enable."
        )
        return

    status = "moderator-only" if cmd.requires_moderator else "public"
    await ctx.owncast_client.send_message(
        f"Command {prefix}{cmd.name} is now {status}."
    )


@on_command("resetcommand", aliases=["resetcmd"], requires_moderator=True)
async def resetcommand(ctx: CommandContext) -> None:
    """Reset a custom command's use counter to 0.

    Usage: !resetcommand !<name>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}resetcommand {prefix}<name>"
        )
        return

    name = _clean_name(args[0], prefix)

    try:
        cmd = await get_manager(ctx.module).reset_use_count(name)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except InactiveCommandError as e:
        await ctx.owncast_client.send_message(
            f"Custom command {prefix}{e.name} is disabled because another module "
            f"registered the same name. Delete it or resolve the conflict to re-enable."
        )
        return

    await ctx.owncast_client.send_message(f"Command {prefix}{cmd.name} counter reset.")


@on_command("editcounter", aliases=["editcount"], requires_moderator=True)
async def editcounter(ctx: CommandContext) -> None:
    """Set, increment, or decrement a named counter.

    Usage: !editcounter <name> <value>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) != 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}editcounter <name> <value>"
        )
        return

    counter_name = args[0].lower()
    value_str = args[1]

    try:
        value = int(value_str)
    except ValueError:
        await ctx.owncast_client.send_message(
            "Invalid value. Must be an integer (e.g. 15, +1, -3)."
        )
        return

    relative = value_str[0] in ("+", "-")

    try:
        new_value = await get_manager(ctx.module).edit_counter(
            counter_name, value=value, relative=relative
        )
    except InvalidNameError:
        await ctx.owncast_client.send_message(
            "Invalid counter name. Only letters, numbers, and underscores are allowed."
        )
        return

    if relative:
        sign = "+" if value >= 0 else ""
        await ctx.owncast_client.send_message(
            f"Adjusted the {counter_name} counter by {sign}{value} to {new_value}."
        )
    else:
        await ctx.owncast_client.send_message(
            f"Set the {counter_name} counter to {new_value}."
        )


@on_command("commandcooldown", aliases=["cmdcooldown"], requires_moderator=True)
async def commandcooldown(ctx: CommandContext) -> None:
    """Set or disable a custom command's cooldown.

    Usage: !commandcooldown !<name> <seconds>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}commandcooldown {prefix}<name> <seconds>"
        )
        return

    try:
        seconds = int(args[1])
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

    name = _clean_name(args[0], prefix)

    try:
        cmd = await get_manager(ctx.module).set_cooldown(name, seconds)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except InactiveCommandError as e:
        await ctx.owncast_client.send_message(
            f"Custom command {prefix}{e.name} is disabled because another module "
            f"registered the same name. Delete it or resolve the conflict to re-enable."
        )
        return

    if cmd.cooldown == 0:
        await ctx.owncast_client.send_message(
            f"Command {prefix}{cmd.name} cooldown disabled."
        )
    else:
        await ctx.owncast_client.send_message(
            f"Command {prefix}{cmd.name} cooldown set to {cmd.cooldown}s."
        )


@on_command("addalias", requires_moderator=True)
async def addalias(ctx: CommandContext) -> None:
    """Add an alias to an existing custom command.

    Usage: !addalias !<command> !<alias>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if len(args) < 2:  # noqa: PLR2004  # just checking argument count
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}addalias {prefix}<command> {prefix}<alias>"
        )
        return

    command_name = _clean_name(args[0], prefix)
    alias = _clean_name(args[1], prefix)

    try:
        cmd = await get_manager(ctx.module).add_alias(command_name, alias)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message("That command does not exist.")
        return
    except NotCustomCommandError:
        await ctx.owncast_client.send_message(
            "That command is not a custom command and cannot be modified."
        )
        return
    except InactiveCommandError as e:
        await ctx.owncast_client.send_message(
            f"Custom command {prefix}{e.name} is disabled because another module "
            f"registered the same name. Delete it or resolve the conflict to re-enable."
        )
        return
    except InvalidNameError:
        await ctx.owncast_client.send_message(
            "Invalid alias name. Only letters, numbers, and underscores are allowed."
        )
        return
    except AliasIsCanonicalNameError:
        await ctx.owncast_client.send_message(
            "An alias cannot be the same as the command name."
        )
        return
    except AliasAlreadyExistsError as e:
        await ctx.owncast_client.send_message(
            f"Alias {prefix}{e.alias} is already assigned to {prefix}{e.owner}."
        )
        return
    except CommandAlreadyExistsError as e:
        await ctx.owncast_client.send_message(
            f"Alias {prefix}{e.name} conflicts with an existing command."
        )
        return

    await ctx.owncast_client.send_message(
        f"Alias {prefix}{alias} added to {prefix}{cmd.name}."
    )


@on_command("removealias", requires_moderator=True)
async def removealias(ctx: CommandContext) -> None:
    """Remove an alias from a custom command.

    Usage: !removealias !<alias>

    :param ctx: The command context.
    """
    prefix = ctx.commands.prefix
    args = ctx.args_list
    if not args:
        await ctx.owncast_client.send_message(
            f"Usage: {prefix}removealias {prefix}<alias>"
        )
        return

    alias = _clean_name(args[0], prefix)

    try:
        cmd = await get_manager(ctx.module).remove_alias(alias)
    except CommandNotFoundError:
        await ctx.owncast_client.send_message(f"Alias {prefix}{alias} does not exist.")
        return

    await ctx.owncast_client.send_message(
        f"Alias {prefix}{alias} removed from {prefix}{cmd.name}."
    )


@on_command("listcommands", aliases=["listcmds"], cooldown=15)
async def listcommands(ctx: CommandContext) -> None:
    """List all custom commands.

    Sends a URL to the command list web page.

    Usage: !listcommands

    :param ctx: The command context.
    """
    url = ctx.routes.url_for("/list")
    await ctx.owncast_client.send_message(f"Custom commands: {url}")
