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

"""Shared custom command handler and database/registry helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from owlbot.api import CommandContext, ModuleCommands, ModuleStorage

from .placeholders import DEFAULT_MAX_DEPTH, process_placeholders


async def get_aliases_for_command(
    storage: ModuleStorage, command_name: str
) -> list[str]:
    """Fetch all aliases for a command from the database.

    :param storage: The module storage instance.
    :param command_name: The canonical command name.
    :return: List of alias names (may be empty).
    """
    rows = await storage.fetch_all(
        "SELECT alias FROM command_aliases WHERE command_name = ? ORDER BY alias",
        (command_name,),
    )
    return [row["alias"] for row in rows]


async def resolve_command_name(
    storage: ModuleStorage, name: str
) -> aiosqlite.Row | None:
    """Resolve a command name or alias to the full command row.

    Checks the commands table first, then falls back to the aliases table.
    Returns the command's name, requires_moderator, and cooldown columns.

    :param storage: The module storage instance.
    :param name: A command name or alias (already lowercased).
    :return: The command row, or None if not found.
    """
    return await storage.fetch_one(
        "SELECT c.name, c.requires_moderator, c.cooldown FROM commands c "
        "WHERE c.name = ? "
        "UNION ALL "
        "SELECT c.name, c.requires_moderator, c.cooldown "
        "FROM command_aliases a JOIN commands c ON c.name = a.command_name "
        "WHERE a.alias = ? "
        "LIMIT 1",
        (name, name),
    )


async def custom_command_handler(ctx: CommandContext) -> None:
    """Shared handler for all custom commands.

    Looks up the command in the database, increments use count,
    processes placeholders, and sends the response.

    :param ctx: The command context.
    """
    cmd_name = ctx.command

    row = await ctx.storage.fetch_one(
        "UPDATE commands SET use_count = use_count + 1 WHERE name = ? "
        "RETURNING response, use_count",
        (cmd_name,),
    )

    # Shouldn't happen unless the command was deleted but not unregistered.
    if not row:
        ctx.logger.warning(f"Custom command '{cmd_name}' not found in database.")
        return

    max_depth = ctx.config.get("max_nesting_depth", DEFAULT_MAX_DEPTH)
    response = await process_placeholders(
        row["response"],
        ctx.args_list,
        ctx.user.display_name,
        row["use_count"],
        ctx.storage,
        max_depth=max_depth,
    )

    await ctx.owncast_client.send_message(response)


def reregister_command(
    commands: ModuleCommands,
    name: str,
    *,
    aliases: list[str],
    requires_moderator: bool,
    cooldown: int,
) -> None:
    """Unregister and re-register a custom command with updated settings.

    Both registry operations are synchronous, so no other coroutine can observe
    the intermediate unregistered state.

    :param commands: The module-scoped command API (ModuleCommands).
    :param name: The canonical command name.
    :param aliases: List of alias names.
    :param requires_moderator: Whether the command requires moderator.
    :param cooldown: Cooldown in seconds.
    """
    commands.unregister(name)
    commands.register(
        name=name,
        handler=custom_command_handler,
        aliases=aliases,
        requires_moderator=requires_moderator,
        cooldown=cooldown,
    )


async def update_and_reregister(
    ctx: CommandContext,
    command_row: aiosqlite.Row,
    *,
    requires_moderator: int | None = None,
    cooldown: int | None = None,
) -> None:
    """Update a command's settings in the database and re-register it.

    Uses the provided *command_row* for current settings, applies any
    overrides, writes them back, and re-registers the command so the
    in-memory registry matches the database.

    :param ctx: The command context.
    :param command_row: The command row (from ``resolve_command_name``).
    :param requires_moderator: New value, or None to keep the current one.
    :param cooldown: New value, or None to keep the current one.
    """
    name: str = command_row["name"]
    final_mod = (
        requires_moderator
        if requires_moderator is not None
        else command_row["requires_moderator"]
    )
    final_cooldown = cooldown if cooldown is not None else command_row["cooldown"]

    now = datetime.now(UTC).isoformat()
    await ctx.storage.execute(
        "UPDATE commands SET requires_moderator = ?, cooldown = ?, updated_at = ? "
        "WHERE name = ?",
        (final_mod, final_cooldown, now, name),
    )

    aliases = await get_aliases_for_command(ctx.storage, name)
    reregister_command(
        ctx.commands,
        name,
        aliases=aliases,
        requires_moderator=bool(final_mod),
        cooldown=final_cooldown,
    )
