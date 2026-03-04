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

"""Typed state containers for the clips module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

    from owlbot.api import ModuleContext

    from .cache import ChunkCache
    from .processing import ProcessingManager


@dataclass
class EditorSession:
    """A clip editor session."""

    preview_path: Path
    work_dir: Path
    duration: float
    creator: str
    expiry_task: asyncio.Task[None] | None = None


@dataclass
class ModuleState:
    """Runtime state for the clips module."""

    cache: ChunkCache | None = None
    sessions: dict[str, EditorSession] = field(default_factory=dict)
    grace_task: asyncio.Task[None] | None = None
    cache_task: asyncio.Task[None] | None = None
    _manager: ProcessingManager | None = None

    @property
    def manager(self) -> ProcessingManager:
        """Return the processing manager, raising if not yet initialized.

        :return: The active ProcessingManager.
        :raises RuntimeError: If the manager has not been set.
        """
        if self._manager is None:
            msg = "ProcessingManager is not initialized."
            raise RuntimeError(msg)
        return self._manager

    @manager.setter
    def manager(self, value: ProcessingManager | None) -> None:
        self._manager = value


def get_state(ctx: ModuleContext) -> ModuleState:
    """Retrieve the ModuleState from the module context.

    :param ctx: The module context.
    :return: The ModuleState instance.
    :raises RuntimeError: If ModuleState has not been initialized.
    """
    state = ctx.state.get("clips")
    if not isinstance(state, ModuleState):
        raise RuntimeError("ModuleState is not initialized.")
    return state
