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

"""Lifecycle hook decorators for module setup and teardown.

This module provides the module-facing API for lifecycle hooks:
- @on_setup decorator for marking setup functions
- @on_teardown decorator for marking teardown functions
- LifecycleHandler type alias
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import ModuleContext

type LifecycleHandler = "Callable[[ModuleContext], Awaitable[None]]"


def on_setup(func: LifecycleHandler) -> LifecycleHandler:
    """Mark a function as a module setup hook.

    The decorated function will be called during module loading with a
    ``ModuleContext``. Each storage operation within a setup hook
    auto-commits independently.

    Applied without parentheses::

        @on_setup
        async def setup(ctx: ModuleContext) -> None:
            await ctx.storage.execute("CREATE TABLE IF NOT EXISTS ...")

    :param func: The setup function to mark.
    :return: The same function, with ``_owlbot_setup`` attribute set.
    """
    func._owlbot_setup = True  # type: ignore[attr-defined]
    return func


def on_teardown(func: LifecycleHandler) -> LifecycleHandler:
    """Mark a function as a module teardown hook.

    The decorated function will be called during module unload or bot
    shutdown with the same ``ModuleContext`` from setup.

    Applied without parentheses::

        @on_teardown
        async def teardown(ctx: ModuleContext) -> None:
            ctx.logger.info("Cleaning up...")

    :param func: The teardown function to mark.
    :return: The same function, with ``_owlbot_teardown`` attribute set.
    """
    func._owlbot_teardown = True  # type: ignore[attr-defined]
    return func
