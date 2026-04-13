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

"""Business logic coordinator for the custom_commands module.

Manages the lifecycle of custom commands: CRUD operations, alias management,
command execution with placeholder processing, and counter management. All
persistence is delegated to CommandRepository. Command registry interactions
go through the ModuleCommands wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .placeholders import DEFAULT_MAX_DEPTH, process_placeholders
from .types import (
    NAME_RE,
    AliasIsCanonicalNameError,
    CannotDeleteByAliasError,
    CommandAlreadyExistsError,
    CommandNotFoundError,
    InvalidNameError,
    NotCustomCommandError,
)

if TYPE_CHECKING:
    from owlbot.api import CommandContext, ModuleContext

    from .repository import CommandRepository
    from .types import Command


class CommandManager:
    """Coordinates between repository, command registry, and placeholders."""

    def __init__(
        self,
        ctx: ModuleContext,
        repo: CommandRepository,
    ) -> None:
        """Initialize the manager.

        :param ctx: The module context.
        :param repo: The command repository for persistence.
        """
        self._ctx = ctx
        self._repo = repo
        self._commands = ctx.commands

    async def _resolve(self, name: str) -> Command:
        """Resolve a name/alias to a Command, or raise a domain error.

        If the name is not in the repository but exists in the global command
        registry, raises NotCustomCommandError. Otherwise re-raises
        CommandNotFoundError.
        """
        try:
            return await self._repo.get(name)
        except CommandNotFoundError as err:
            if self._commands.exists(name):
                raise NotCustomCommandError(name) from err
            raise

    def _reregister(self, command: Command) -> None:
        """Unregister and re-register a command with current snapshot settings.

        Both operations are synchronous, preventing interleaved state.
        """
        self._commands.unregister(command.name)
        self._commands.register(
            name=command.name,
            handler=custom_command_handler,
            aliases=list(command.aliases),
            requires_moderator=command.requires_moderator,
            cooldown=command.cooldown,
        )

    async def load_all(self) -> None:
        """Load all commands from the database into the command registry."""
        commands = await self._repo.list_all()
        loaded = 0
        skipped = 0
        for cmd in commands:
            try:
                self._commands.register(
                    name=cmd.name,
                    handler=custom_command_handler,
                    aliases=list(cmd.aliases),
                    requires_moderator=cmd.requires_moderator,
                    cooldown=cmd.cooldown,
                )
                loaded += 1
            except ValueError:
                self._ctx.logger.warning(
                    "Skipping custom command '%s': conflicts with an existing command.",
                    cmd.name,
                )
                skipped += 1
        self._ctx.logger.info("Loaded %d custom command(s) from database.", loaded)
        if skipped:
            self._ctx.logger.info("Skipped %d conflicting custom command(s).", skipped)

    async def create_command(self, name: str, response: str) -> Command:
        """Create a new custom command.

        :param name: Command name (must match NAME_RE).
        :param response: Response template.
        :return: Snapshot of the created command.
        :raises InvalidNameError: If name is invalid.
        :raises CommandAlreadyExistsError: If name conflicts with any command.
        """
        if not NAME_RE.match(name):
            raise InvalidNameError(name)
        if self._commands.exists(name):
            raise CommandAlreadyExistsError(name)

        default_cooldown: int = self._ctx.config.get("default_cooldown", 5)
        command = await self._repo.create(name, response, default_cooldown)
        self._commands.register(
            name=name,
            handler=custom_command_handler,
            cooldown=default_cooldown,
        )

        self._ctx.logger.info("Custom command '%s' created.", name)
        return command

    async def edit_command(self, name: str, response: str) -> Command:
        """Edit an existing custom command's response.

        :param name: Command name or alias.
        :param response: New response template.
        :return: Updated command snapshot.
        """
        command = await self._resolve(name)
        updated = await self._repo.update_response(command.name, response)
        self._ctx.logger.info("Custom command '%s' updated.", command.name)
        return updated

    async def delete_command(self, input_name: str) -> Command:
        """Delete a custom command.

        :param input_name: Command name (must be canonical, not alias).
        :return: Snapshot of the deleted command.
        :raises CannotDeleteByAliasError: If input_name is an alias.
        """
        command = await self._resolve(input_name)
        if input_name != command.name:
            raise CannotDeleteByAliasError(input_name, command.name)
        deleted = await self._repo.delete(command.name)
        self._commands.unregister(command.name)
        self._ctx.logger.info("Custom command '%s' deleted.", command.name)
        return deleted

    async def set_mod_only(self, name: str, *, enabled: bool) -> Command:
        """Toggle moderator-only access for a command.

        :param name: Command name or alias.
        :param enabled: True for moderator-only, False for public.
        :return: Updated command snapshot.
        """
        command = await self._resolve(name)
        updated = await self._repo.update_moderator_flag(command.name, enabled=enabled)
        self._reregister(updated)
        self._ctx.logger.info(
            "Custom command '%s' set to %s.",
            command.name,
            "moderator-only" if enabled else "public",
        )
        return updated

    async def set_cooldown(self, name: str, seconds: int) -> Command:
        """Set a command's cooldown.

        :param name: Command name or alias.
        :param seconds: Cooldown in seconds (0 to disable).
        :return: Updated command snapshot.
        """
        command = await self._resolve(name)
        updated = await self._repo.update_cooldown(command.name, seconds=seconds)
        self._reregister(updated)
        self._ctx.logger.info(
            "Custom command '%s' cooldown set to %ds.",
            command.name,
            seconds,
        )
        return updated

    async def reset_use_count(self, name: str) -> Command:
        """Reset a command's use counter to zero.

        :param name: Command name or alias.
        :return: Updated command snapshot.
        """
        command = await self._resolve(name)
        reset = await self._repo.reset_use_count(command.name)
        self._ctx.logger.info("Custom command '%s' counter reset.", command.name)
        return reset

    async def add_alias(self, command_name: str, alias: str) -> Command:
        """Add an alias to a command.

        :param command_name: Command name or alias (resolved to canonical).
        :param alias: New alias to add.
        :return: Updated command snapshot.
        """
        if not NAME_RE.match(alias):
            raise InvalidNameError(alias)
        command = await self._resolve(command_name)
        if alias == command.name:
            raise AliasIsCanonicalNameError(alias)
        if self._commands.exists(alias):
            raise CommandAlreadyExistsError(alias)
        updated = await self._repo.add_alias(command.name, alias)
        self._reregister(updated)
        self._ctx.logger.info(
            "Alias '%s' added to custom command '%s'.",
            alias,
            command.name,
        )
        return updated

    async def remove_alias(self, alias: str) -> Command:
        """Remove an alias from a command.

        :param alias: Alias to remove.
        :return: Updated command snapshot (owner with alias removed).
        """
        _, updated = await self._repo.remove_alias(alias)
        self._reregister(updated)
        self._ctx.logger.info(
            "Alias '%s' removed from custom command '%s'.",
            alias,
            updated.name,
        )
        return updated

    async def execute_command(
        self,
        name: str,
        args_list: list[str],
        user_display_name: str,
    ) -> str:
        """Execute a custom command: increment count and process placeholders.

        :param name: Canonical command name.
        :param args_list: Arguments passed to the command.
        :param user_display_name: Display name of the invoking user.
        :return: The processed response string.
        """
        command = await self._repo.increment_use_count(name)
        max_depth: int = self._ctx.config.get("max_nesting_depth", DEFAULT_MAX_DEPTH)
        return await process_placeholders(
            command.response,
            args_list,
            user_display_name,
            command,
            self,
            max_depth=max_depth,
        )

    async def get_counter(self, name: str) -> int:
        """Get a counter's value (CounterAccessor protocol).

        :param name: Counter name.
        :return: Current value (0 if not found).
        """
        return await self._repo.get_counter(name)

    async def set_counter(self, name: str, value: int) -> int:
        """Set a counter to an absolute value (CounterAccessor protocol).

        :param name: Counter name.
        :param value: Absolute value to set.
        :return: The new value.
        """
        return await self._repo.set_counter(name, value)

    async def adjust_counter(self, name: str, delta: int) -> int:
        """Adjust a counter by a relative delta (CounterAccessor protocol).

        :param name: Counter name.
        :param delta: Amount to add (can be negative).
        :return: The new value.
        """
        return await self._repo.update_counter(name, delta)

    async def edit_counter(self, name: str, *, value: int, relative: bool) -> int:
        """Set or adjust a named counter.

        :param name: Counter name (must match NAME_RE).
        :param value: The integer value (absolute or delta).
        :param relative: True for relative adjustment, False for absolute set.
        :return: The new counter value.
        :raises InvalidNameError: If name is invalid.
        """
        if not NAME_RE.match(name):
            raise InvalidNameError(name)
        if relative:
            new_value = await self._repo.update_counter(name, value)
        else:
            new_value = await self._repo.set_counter(name, value)
        self._ctx.logger.info("Counter '%s' set to %d.", name, new_value)
        return new_value

    async def list_commands(self) -> list[Command]:
        """Return all custom commands ordered by name.

        :return: List of Command snapshots.
        """
        return await self._repo.list_all()


def get_manager(ctx: ModuleContext) -> CommandManager:
    """Return the CommandManager stored in the module context's state.

    :param ctx: The module context.
    :return: The active CommandManager.
    :raises RuntimeError: If the manager has not been initialized.
    """
    manager = ctx.state.get("manager")
    if not isinstance(manager, CommandManager):
        raise RuntimeError("CommandManager is not initialized.")  # noqa: TRY004  # state error, not a type error
    return manager


async def custom_command_handler(ctx: CommandContext) -> None:
    """Shared handler for all custom commands.

    Delegates execution to the manager, which increments the use count,
    processes placeholders, and returns the response string.

    :param ctx: The command context.
    """
    manager = get_manager(ctx.module)
    response = await manager.execute_command(
        ctx.command, ctx.args_list, ctx.user.display_name
    )
    await ctx.owncast_client.send_message(response)
