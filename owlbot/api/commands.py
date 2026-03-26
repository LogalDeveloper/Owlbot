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

"""Command system types and decorators for Owlbot.

This module provides the module-facing API for commands:
- @on_command decorator for registering command handlers
- CommandEvent dataclass for parsed command data
- CommandInfo dataclass for command metadata
- CommandHandler type alias
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import CommandContext
    from .event_types import ChatEvent


class CommandMark(TypedDict):
    """Type for the command marker attribute set by @on_command."""

    name: str
    aliases: list[str] | tuple[str, ...] | None
    requires_authenticated: bool
    requires_moderator: bool
    cooldown: int | float


@dataclass(frozen=True, slots=True)
class CommandEvent:
    """Parsed command information from a chat message."""

    command: str
    args: str
    args_list: list[str]
    prefix: str
    chat_event: ChatEvent


type CommandHandler = "Callable[[CommandContext], Awaitable[None]]"


@dataclass(frozen=True, slots=True)
class CommandInfo:
    """Metadata about a registered command."""

    name: str
    handler: CommandHandler
    module_name: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    requires_authenticated: bool = False
    requires_moderator: bool = False
    cooldown: int | float = 0
    all_triggers: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Compute derived fields."""
        object.__setattr__(self, "all_triggers", frozenset({self.name}) | self.aliases)


def on_command(
    name: str,
    *,
    aliases: list[str] | tuple[str, ...] | None = None,
    requires_authenticated: bool = False,
    requires_moderator: bool = False,
    cooldown: int | float = 0,
) -> Callable[[CommandHandler], CommandHandler]:
    """Register a command handler.

    :param name: Primary command name (case-insensitive).
    :param aliases: Optional list of alternative names.
    :param requires_authenticated: If True, user must be logged in.
    :param requires_moderator: If True, user must have moderator privileges.
    :param cooldown: Minimum seconds between invocations, global across all
        users (0 to disable).
    :return: Decorator that marks the function for registration.
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        # Mark the function with command info for deferred registration.
        # The module loader will scan for this attribute and register commands.
        func._owlbot_command = CommandMark(  # type: ignore[attr-defined]
            name=name,
            aliases=aliases,
            requires_authenticated=requires_authenticated,
            requires_moderator=requires_moderator,
            cooldown=cooldown,
        )
        return func

    return decorator
