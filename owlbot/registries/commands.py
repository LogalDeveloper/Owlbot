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

"""Command registry, module-scoped wrapper, and dispatcher.

Internal infrastructure for managing command registration and dispatch.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from ..api.commands import CommandEvent, CommandHandler, CommandInfo, CommandMark
from ..api.event_types import ChatEvent

if TYPE_CHECKING:
    from types import ModuleType

    from ..api.context import EventContext, ModuleContext
    from ..api.owncast_client import OwncastClient

type BuiltinCommandHandler = Callable[[ChatEvent, "OwncastClient"], Awaitable[None]]

logger = logging.getLogger("owlbot.commands")


class CommandRegistry:
    """Holds all registered commands for a bot instance.

    Instance-scoped to enable proper dependency injection and allow multiple
    bot instances to coexist without sharing state.
    """

    def __init__(self, prefix: str = "!") -> None:
        """Initialize the command registry.

        :param prefix: Command prefix character (e.g., "!" for "!ping").
        """
        # Maps primary command names to their CommandInfo objects.
        self._commands: dict[str, CommandInfo] = {}

        # Maps all triggers (primary names and aliases) to their primary command name.
        # This allows O(1) lookup for any trigger without scanning all commands.
        self._aliases: dict[str, str] = {}

        self.prefix = prefix

        logger.debug(f"CommandRegistry initialized with prefix '{prefix}'.")

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        aliases: list[str] | tuple[str, ...] | None = None,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
        cooldown: int | float = 0,
        module_name: str,
    ) -> None:
        """Register a command handler.

        :param name: Primary command name (case-insensitive).
        :param handler: Async function to handle the command.
        :param aliases: Optional list of alternative names.
        :param requires_authenticated: If True, user must be logged in.
        :param requires_moderator: If True, user must have moderator privileges.
        :param cooldown: Minimum seconds between invocations (0 to disable).
        :param module_name: Name of the module registering this command.
        :raises ValueError: If command name or alias conflicts with existing command.
        """
        name_lower = name.lower()
        alias_set = frozenset(a.lower() for a in (aliases or []))

        all_triggers = {name_lower} | alias_set
        for trigger in all_triggers:
            if trigger in self._aliases:
                existing = self._aliases[trigger]
                raise ValueError(
                    f"Command trigger '{trigger}' conflicts with existing "
                    f"command '{existing}'"
                )

        info = CommandInfo(
            name=name_lower,
            handler=handler,
            module_name=module_name,
            aliases=alias_set,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
            cooldown=cooldown,
        )

        self._commands[name_lower] = info

        for trigger in all_triggers:
            self._aliases[trigger] = name_lower

        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.commands")
        module_logger.debug(
            f"Registered command '{name_lower}' with aliases {sorted(alias_set)}, "
            f"authenticated={requires_authenticated}, moderator={requires_moderator}, "
            f"cooldown={cooldown}."
        )

    def unregister(self, name: str) -> bool:
        """Unregister a command and all its aliases.

        :param name: The primary command name or any alias.
        :return: True if command was found and removed, False otherwise.
        """
        name_lower = name.lower()

        primary = self._aliases.get(name_lower)
        if primary is None:
            return False

        info = self._commands.get(primary)
        if info is None:
            return False

        for trigger in info.all_triggers:
            self._aliases.pop(trigger, None)

        del self._commands[primary]

        module_logger = logging.getLogger(f"owlbot.modules.{info.module_name}.commands")
        module_logger.debug(f"Unregistered command '{primary}'.")
        return True

    def get(self, trigger: str) -> CommandInfo | None:
        """Look up a command by name or alias.

        :param trigger: Command name or alias (case-insensitive).
        :return: CommandInfo if found, None otherwise.
        """
        trigger_lower = trigger.lower()
        primary = self._aliases.get(trigger_lower)
        if primary is None:
            return None
        return self._commands.get(primary)

    def exists(self, trigger: str) -> bool:
        """Check if a command is registered.

        :param trigger: Command name or alias (case-insensitive).
        :return: True if the command exists, False otherwise.
        """
        return trigger.lower() in self._aliases

    def get_all(self) -> dict[str, CommandInfo]:
        """Get all registered commands.

        :return: Dict mapping primary command names to CommandInfo.
        """
        return self._commands.copy()

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all commands registered by a specific module.

        :param module_name: The module whose commands should be removed.
        :return: Number of commands removed.
        """
        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.commands")
        to_remove = [
            name
            for name, info in self._commands.items()
            if info.module_name == module_name
        ]
        module_logger.debug(f"Unregistering all commands ({len(to_remove)} total).")
        for name in to_remove:
            self.unregister(name)
        return len(to_remove)

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for @on_command-decorated functions and register them.

        Looks for functions with the ``_owlbot_command`` attribute set by
        the ``@on_command`` decorator and registers each one.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        for obj in vars(module).values():
            if callable(obj):
                cmd_info = getattr(obj, "_owlbot_command", None)
                if cmd_info is not None:
                    mark = cast("CommandMark", cmd_info)
                    self.register(
                        name=mark["name"],
                        handler=obj,
                        aliases=mark["aliases"],
                        requires_authenticated=mark["requires_authenticated"],
                        requires_moderator=mark["requires_moderator"],
                        cooldown=mark["cooldown"],
                        module_name=module_name,
                    )

    def parse(self, message: str) -> tuple[str, str] | None:
        """Parse a message to extract command and arguments.

        :param message: The chat message body.
        :return: Tuple of (command_name, args_string), or None if not a command.
        """
        if not message.startswith(self.prefix):
            return None

        content = message[len(self.prefix) :].strip()

        if not content:
            return None

        parts = content.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        logger.debug(f"Parsed command: {command!r} with args: {args!r}")
        return command, args


class CommandDispatcher:
    """Dispatches chat events to registered command handlers.

    Parses messages, checks authentication/moderator requirements,
    and calls the appropriate command handler.
    """

    def __init__(
        self,
        get_module_context: Callable[[str], ModuleContext],
        owncast_client: OwncastClient,
        handler_timeout: float,
        loaded_modules: set[str],
        command_prefix: str = "!",
    ) -> None:
        """Initialize the command dispatcher.

        Creates and owns a :class:`CommandRegistry` internally.

        :param get_module_context: Callable that looks up a
            ModuleContext by module name.
        :param owncast_client: Owncast API client for sending error messages.
        :param handler_timeout: Timeout for command handlers in seconds.
        :param loaded_modules: Reference to the set of currently loaded module names.
        :param command_prefix: Prefix character for commands (e.g., "!").
        """
        self._command_registry = CommandRegistry(command_prefix)
        self._get_module_context = get_module_context
        self._owncast_client = owncast_client
        self._handler_timeout = handler_timeout
        self._loaded_modules = loaded_modules
        # Maps canonical command name -> monotonic timestamp of last invocation.
        self._cooldown_tracker: dict[str, float] = {}
        # Maps canonical command name -> built-in handler callable.
        self._builtin_handlers: dict[str, BuiltinCommandHandler] = {}

        self._register_builtin_commands()

    @property
    def prefix(self) -> str:
        """The command prefix character (e.g., '!')."""
        return self._command_registry.prefix

    def register_builtin(
        self,
        name: str,
        handler: BuiltinCommandHandler,
        *,
        aliases: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Register a built-in command handler.

        Built-in commands use a simpler handler signature (event, owncast_client)
        and don't require module infrastructure.

        :param name: Primary command name (case-insensitive).
        :param handler: Async function with signature (ChatEvent, OwncastClient).
        :param aliases: Optional list of alternative names.
        """
        name_lower = name.lower()
        self._builtin_handlers[name_lower] = handler
        self._command_registry.register(
            name=name,
            handler=handler,  # type: ignore[arg-type]
            aliases=aliases,
            module_name="__builtin__",
            cooldown=60,
        )
        logger.debug(f"Registered built-in command '{name_lower}'.")

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        aliases: list[str] | tuple[str, ...] | None = None,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
        cooldown: int | float = 0,
        module_name: str,
    ) -> None:
        """Register a command handler.

        Delegates to the internal CommandRegistry.

        :param name: Primary command name (case-insensitive).
        :param handler: Async function to handle the command.
        :param aliases: Optional list of alternative names.
        :param requires_authenticated: If True, user must be logged in.
        :param requires_moderator: If True, user must have moderator privileges.
        :param cooldown: Minimum seconds between invocations (0 to disable).
        :param module_name: Name of the module registering this command.
        :raises ValueError: If command name or alias conflicts with existing command.
        """
        self._command_registry.register(
            name=name,
            handler=handler,
            aliases=aliases,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
            cooldown=cooldown,
            module_name=module_name,
        )
        # Clear any stale cooldown tracker entry on re-registration.
        self._cooldown_tracker.pop(name.lower(), None)

    def unregister(self, name: str) -> bool:
        """Unregister a command and all its aliases.

        Delegates to the internal CommandRegistry.

        :param name: The primary command name or any alias.
        :return: True if command was found and removed, False otherwise.
        """
        # Resolve canonical name before unregistering so we can clean up the tracker.
        info = self._command_registry.get(name)
        result = self._command_registry.unregister(name)
        if result and info is not None:
            self._cooldown_tracker.pop(info.name, None)
        return result

    def get(self, trigger: str) -> CommandInfo | None:
        """Look up a command by name or alias.

        Delegates to the internal CommandRegistry.

        :param trigger: Command name or alias (case-insensitive).
        :return: CommandInfo if found, None otherwise.
        """
        return self._command_registry.get(trigger)

    def exists(self, trigger: str) -> bool:
        """Check if a command is registered.

        Delegates to the internal CommandRegistry.

        :param trigger: Command name or alias (case-insensitive).
        :return: True if the command exists, False otherwise.
        """
        return self._command_registry.exists(trigger)

    def get_by_module(self, module_name: str) -> dict[str, CommandInfo]:
        """Get all commands registered by a specific module.

        :param module_name: The module whose commands to return.
        :return: Dict mapping primary command names to CommandInfo for that module.
        """
        return {
            name: info
            for name, info in self._command_registry.get_all().items()
            if info.module_name == module_name
        }

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for @on_command-decorated functions and register them.

        Delegates to the internal CommandRegistry.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        self._command_registry.register_from_module(module, module_name)

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all commands registered by a specific module.

        Delegates to the internal CommandRegistry.

        :param module_name: The module whose commands should be removed.
        :return: Number of commands removed.
        """
        # Collect command names before unregistering so we can clean up the tracker.
        module_commands = self.get_by_module(module_name)
        for cmd_name in module_commands:
            self._cooldown_tracker.pop(cmd_name, None)
        return self._command_registry.unregister_by_module(module_name)

    async def dispatch(self, event: ChatEvent) -> None:
        """Dispatch a chat event to the appropriate command handler if it's a command.

        :param event: The chat event to check for commands.
        """
        from ..api.context import CommandContext, EventContext

        parsed = self._command_registry.parse(event.body)

        if parsed is None:
            return

        command_name, args = parsed

        command_info = self._command_registry.get(command_name)

        if command_info is None:
            # Log for debugging but don't spam the chat with "unknown command" errors.
            logger.debug(f"Unknown command: {command_name}")
            return

        user = event.user

        logger.info(
            f"Command '{command_info.name}' invoked by {user.display_name}"
            f" with args: {args!r}"
        )

        if command_info.requires_authenticated and not user.is_authenticated:
            logger.info(
                f"Command '{command_name}' denied for {user.display_name}: "
                "authentication required"
            )
            await self._owncast_client.send_system_message_to_client(
                event.client_id,
                f"You must be authenticated to use !{command_info.name}.",
            )
            return

        if command_info.requires_moderator and not user.is_moderator:
            logger.info(
                f"Command '{command_name}' denied for {user.display_name}: "
                "moderator required"
            )
            await self._owncast_client.send_system_message_to_client(
                event.client_id,
                f"Only moderators can use !{command_info.name}.",
            )
            return

        if command_info.cooldown > 0:
            now = time.monotonic()
            last = self._cooldown_tracker.get(command_info.name)
            if last is not None and now - last < command_info.cooldown:
                remaining = math.ceil(command_info.cooldown - (now - last))
                logger.info(
                    f"Command '{command_name}' denied for {user.display_name}: "
                    f"on cooldown ({remaining}s remaining)"
                )
                await self._owncast_client.send_system_message_to_client(
                    event.client_id,
                    f"!{command_info.name} can be used every "
                    f"{command_info.cooldown:.0f}s. Try again in {remaining}s.",
                )
                return
            # Record immediately to prevent concurrent tasks from bypassing the
            # cooldown between this check and handler completion.
            self._cooldown_tracker[command_info.name] = now

        # Built-in commands use a simpler dispatch path
        # without ModuleContext or storage.
        builtin_handler = self._builtin_handlers.get(command_info.name)
        if builtin_handler is not None:
            try:
                start = time.perf_counter()
                await asyncio.wait_for(
                    builtin_handler(event, self._owncast_client),
                    timeout=self._handler_timeout,
                )
                elapsed = (time.perf_counter() - start) * 1000
                logger.debug(
                    f"Built-in command '{command_info.name}' "
                    f"completed in {elapsed:.1f}ms."
                )
            except TimeoutError:
                logger.warning(
                    f"Built-in command '{command_info.name}' "
                    f"cancelled after {self._handler_timeout}s timeout."
                )
            except Exception as e:
                logger.exception(
                    f"Built-in command '{command_info.name}' raised exception: {e}"
                )
            return

        cmd_event = CommandEvent(
            # Use the canonical name, not the alias the user typed.
            command=command_info.name,
            args=args,
            args_list=args.split() if args else [],
            prefix=self._command_registry.prefix,
            chat_event=event,
        )

        module_ctx = self._get_module_context(command_info.module_name)

        event_ctx: EventContext[ChatEvent] = EventContext(
            event=event,
            module=module_ctx,
        )

        cmd_ctx = CommandContext(
            command_event=cmd_event,
            event_context=event_ctx,
            module=module_ctx,
        )

        # The checkout acquires a pooled connection for the
        # duration of this command invocation.
        async with module_ctx.storage._checkout():
            try:
                start = time.perf_counter()
                await asyncio.wait_for(
                    command_info.handler(cmd_ctx), timeout=self._handler_timeout
                )
                elapsed = (time.perf_counter() - start) * 1000

                # Command succeeded, commit any database changes.
                await module_ctx.storage._commit()

                logger.debug(
                    f"Command '{command_info.name}' completed in {elapsed:.1f}ms."
                )
            except TimeoutError:
                # Command timed out. Rollback any partial changes.
                await module_ctx.storage._rollback()
                logger.warning(
                    f"Command handler '{command_info.name}' "
                    f"from module '{command_info.module_name}' "
                    f"cancelled after "
                    f"{self._handler_timeout}s timeout."
                )
            except Exception as e:
                # Command raised an exception. Rollback any partial changes.
                await module_ctx.storage._rollback()
                logger.exception(
                    f"Command handler '{command_info.name}' "
                    f"from module '{command_info.module_name}' "
                    f"raised exception: {e}"
                )

    def _register_builtin_commands(self) -> None:
        """Register all built-in commands."""
        from .._version import __version__

        loaded_modules = self._loaded_modules

        async def about_with_modules(
            event: ChatEvent, owncast_client: OwncastClient
        ) -> None:
            module_count = len(loaded_modules)
            module_list = (
                ", ".join(sorted(loaded_modules)) if loaded_modules else "none"
            )
            await owncast_client.send_message(
                f"Owlbot v{__version__} - A logal.dev project | "
                f"Modules ({module_count}): {module_list}"
            )

        self.register_builtin("about", about_with_modules)


class ModuleCommands:
    """Module-scoped wrapper around CommandDispatcher.

    This wrapper auto-supplies the module name for registration operations,
    so modules don't need to pass their own name back into the API.
    Follows the same pattern as ModuleEvents and ModuleRoutes.
    """

    def __init__(self, dispatcher: CommandDispatcher, module_name: str) -> None:
        """Initialize the module-scoped command wrapper.

        :param dispatcher: The CommandDispatcher that owns the command registry.
        :param module_name: The name of the module using this wrapper.
        """
        self._dispatcher = dispatcher
        self._module_name = module_name

    @property
    def prefix(self) -> str:
        """The command prefix character (e.g., '!')."""
        return self._dispatcher.prefix

    @property
    def module_commands(self) -> dict[str, CommandInfo]:
        """Commands registered by this module only."""
        return self._dispatcher.get_by_module(self._module_name)

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        aliases: list[str] | tuple[str, ...] | None = None,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
        cooldown: int | float = 0,
    ) -> None:
        """Register a command handler for this module.

        The module name is automatically supplied.

        :param name: Primary command name (case-insensitive).
        :param handler: Async function to handle the command.
        :param aliases: Optional list of alternative names.
        :param requires_authenticated: If True, user must be logged in.
        :param requires_moderator: If True, user must have moderator privileges.
        :param cooldown: Minimum seconds between invocations (0 to disable).
        :raises ValueError: If command name or alias conflicts with existing command.
        """
        self._dispatcher.register(
            name=name,
            handler=handler,
            aliases=aliases,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
            cooldown=cooldown,
            module_name=self._module_name,
        )

    def unregister(self, name: str) -> bool:
        """Unregister a command and all its aliases.

        Only commands registered by this module can be unregistered.

        :param name: The primary command name or any alias.
        :return: True if command was found and removed, False if not found or not owned.
        """
        # Only allow unregistering commands owned by this module.
        info = self._dispatcher.get(name)
        if info is None or info.module_name != self._module_name:
            return False
        return self._dispatcher.unregister(name)

    def get(self, trigger: str) -> CommandInfo | None:
        """Look up a command by name or alias within this module's registrations.

        :param trigger: Command name or alias (case-insensitive).
        :return: CommandInfo if found and owned by this module, None otherwise.
        """
        info = self._dispatcher.get(trigger)
        if info is None or info.module_name != self._module_name:
            return None
        return info

    def exists(self, trigger: str) -> bool:
        """Check if a command is registered across all modules.

        :param trigger: Command name or alias (case-insensitive).
        :return: True if the command exists, False otherwise.
        """
        return self._dispatcher.exists(trigger)
