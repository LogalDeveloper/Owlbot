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

"""Internal infrastructure: registries, module-scoped wrappers, and dispatchers."""

from .commands import CommandDispatcher, CommandRegistry, ModuleCommands
from .events import EventDispatcher, EventRegistry, ModuleEvents
from .routes import ModuleRoutes, RouteDispatcher, RouteRegistry

__all__ = [
    "CommandDispatcher",
    "CommandRegistry",
    "EventDispatcher",
    "EventRegistry",
    "ModuleCommands",
    "ModuleEvents",
    "ModuleRoutes",
    "RouteDispatcher",
    "RouteRegistry",
]
