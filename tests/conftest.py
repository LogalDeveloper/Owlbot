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

"""Shared test fixtures for the Owlbot test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from owlbot.api.context import ModuleContext
from owlbot.api.storage import ModuleStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def make_module_context(
    storage: ModuleStorage | None = None, module_name: str = "mod_a"
) -> ModuleContext:
    """Build a stub ModuleContext backed by real or None storage."""
    return ModuleContext(
        module_name=module_name,
        config=None,  # type: ignore[arg-type]
        owncast_client=None,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        commands=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        routes=None,  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        templates=None,  # type: ignore[arg-type]
        admin_client=None,
    )


@pytest.fixture
async def storage() -> AsyncIterator[ModuleStorage]:
    """Yield an open in-memory ModuleStorage."""
    async with ModuleStorage(None, "test_module") as s:
        yield s
