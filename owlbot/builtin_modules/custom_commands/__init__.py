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

Allows moderators to create, edit, delete, and list custom chat commands
at runtime. Custom commands are stored in SQLite and dynamically registered
with the CommandRegistry.
"""

from owlbot.api import ModuleContext, on_setup, on_teardown

from .commands import (
    addalias,
    addcommand,
    commandcooldown,
    commandmodonly,
    deletecommand,
    editcommand,
    editcounter,
    listcommands,
    removealias,
    resetcommand,
)
from .manager import CommandManager
from .repository import CommandRepository
from .routes import command_list_page

__all__ = [
    "addalias",
    "addcommand",
    "command_list_page",
    "commandcooldown",
    "commandmodonly",
    "deletecommand",
    "editcommand",
    "editcounter",
    "listcommands",
    "removealias",
    "resetcommand",
    "setup",
    "teardown",
]


@on_setup
async def setup(ctx: ModuleContext) -> None:
    """Initialize the custom_commands module.

    :param ctx: Module context with config, storage, and other services.
    """
    ctx.config.register_defaults({"max_nesting_depth": 4, "default_cooldown": 5})
    repo = CommandRepository(ctx.storage)
    await repo.setup()
    manager = CommandManager(ctx, repo)
    ctx.state["manager"] = manager

    await manager.load_all()


@on_teardown
async def teardown(ctx: ModuleContext) -> None:
    """Clean up the custom_commands module.

    :param ctx: Module context.
    """
    ctx.state["manager"] = None
