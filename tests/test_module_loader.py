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

"""Focused regression tests for Task 5 module-loader auth wiring."""

from pathlib import Path

from ruamel.yaml import YAML

from owlbot.api.config import Config
from owlbot.api.http_client import HttpClient
from owlbot.module_loader import RESERVED_MODULE_NAMES, ModuleLoader
from owlbot.sessions import SessionManager


def _write_config(tmp_path: Path, *, command_prefix: str = "!") -> Path:
    path = tmp_path / "config.yaml"
    yaml = YAML(typ="safe")
    with path.open("w") as f:
        yaml.dump(
            {
                "owncast": {"url": "http://localhost:8080"},
                "owlbot": {
                    "public_base_url": "http://localhost:8081",
                    "command_prefix": command_prefix,
                },
            },
            f,
        )
    return path


def test_connect_is_reserved_module_name() -> None:
    """The built-in connect route namespace cannot be claimed by modules."""
    assert "connect" in RESERVED_MODULE_NAMES


def test_module_loader_threads_one_shared_session_manager(tmp_path: Path) -> None:
    """ModuleLoader reuses the same session manager across its dispatch stack."""
    config = Config(_write_config(tmp_path))
    session_manager = SessionManager()
    loader = ModuleLoader(
        tmp_path / "modules",
        config,
        HttpClient(),
        session_manager,
    )

    assert loader.session_manager is session_manager
    assert loader.command_dispatcher._session_manager is session_manager
    assert loader.event_dispatcher._session_manager is session_manager
    assert loader.route_dispatcher._session_manager is session_manager


def test_module_loader_threads_configured_command_prefix_to_route_dispatcher(
    tmp_path: Path,
) -> None:
    """ModuleLoader passes the configured command prefix to RouteDispatcher."""
    config = Config(_write_config(tmp_path, command_prefix="/"))
    session_manager = SessionManager()
    loader = ModuleLoader(
        tmp_path / "modules",
        config,
        HttpClient(),
        session_manager,
    )

    assert loader.route_dispatcher._command_prefix == "/"
