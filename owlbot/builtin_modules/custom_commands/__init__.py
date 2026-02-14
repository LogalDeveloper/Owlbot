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

"""Custom commands module for Owlbot.

Allows moderators to create, edit, delete, and list custom chat commands at runtime.
Custom commands are stored in SQLite and dynamically registered
with the CommandRegistry.
"""

from owlbot.api import ModuleContext, on_setup

from .handler import custom_command_handler

# Re-export decorated handlers so the module loader discovers them.
from .management_commands import (
    addalias,
    addcommand,
    commandcooldown,
    commandmodonly,
    deletecommand,
    editcommand,
    listcommands,
    removealias,
    resetcommand,
)
from .routes import command_list_page

__all__ = [
    "addalias",
    "addcommand",
    "command_list_page",
    "commandcooldown",
    "commandmodonly",
    "deletecommand",
    "editcommand",
    "listcommands",
    "removealias",
    "resetcommand",
    "setup",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """
    Initialize the custom_commands module.

    Creates the database schema and loads existing commands from the database.

    :param ctx: Module context with config, storage, and other services.
    """
    ctx.config.register_defaults({"max_nesting_depth": 4, "default_cooldown": 5})
    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            name TEXT PRIMARY KEY NOT NULL,
            response TEXT NOT NULL,
            use_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            requires_moderator INTEGER DEFAULT 0,
            cooldown INTEGER DEFAULT 0
        )
    """)

    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS command_aliases (
            alias TEXT PRIMARY KEY NOT NULL,
            command_name TEXT NOT NULL,
            FOREIGN KEY (command_name) REFERENCES commands(name) ON DELETE CASCADE
        )
    """)

    await ctx.storage.execute("""
        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY NOT NULL,
            value INTEGER DEFAULT 0
        )
    """)

    rows = await ctx.storage.fetch_all(
        "SELECT c.name, c.requires_moderator, c.cooldown, "
        "GROUP_CONCAT(ca.alias) AS aliases "
        "FROM commands c "
        "LEFT JOIN command_aliases ca ON c.name = ca.command_name "
        "GROUP BY c.name"
    )
    skipped = 0
    for row in rows:
        aliases = row["aliases"].split(",") if row["aliases"] else []
        try:
            ctx.commands.register(
                name=row["name"],
                handler=custom_command_handler,
                aliases=aliases,
                requires_moderator=bool(row["requires_moderator"]),
                cooldown=row["cooldown"],
            )
        except ValueError:
            ctx.logger.warning(
                f"Skipping custom command '{row['name']}': "
                "conflicts with an existing command."
            )
            skipped += 1

    loaded_count = len(rows) - skipped
    ctx.logger.info(f"Loaded {loaded_count} custom command(s) from database.")
    if skipped:
        ctx.logger.info(f"Skipped {skipped} conflicting custom command(s).")
