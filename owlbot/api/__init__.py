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

"""Public API for Owlbot modules.

This package provides all the APIs modules use to interact with Owlbot:

- **event_types**: Event types and dataclasses (EventType, ChatEvent, User, etc.)
- **context**: Handler contexts (ModuleContext, EventContext,
  CommandContext, RouteContext)
- **events**: Event registration (@on_event, Priority)
- **commands**: Command registration (@on_command, CommandEvent)
- **routes**: HTTP route registration (@on_route, RouteInfo)
- **lifecycle**: Lifecycle hooks (@on_setup, @on_teardown)
- **storage**: SQLite storage (ModuleStorage)
- **config**: Configuration (ModuleConfig)
- **owncast_client**: Owncast client (OwncastClient)
- **owncast_admin_client**: Owncast admin client (OwncastAdminClient)
- **ModuleCommands, ModuleEvents, ModuleRoutes**: Module-scoped service wrappers
  (re-exported from registries)
"""

# Event types and dataclasses (pure data).
# Module-scoped service wrappers (re-exported from registries).
from ..registries.commands import ModuleCommands
from ..registries.events import ModuleEvents
from ..registries.routes import ModuleRoutes

# Command system (module-facing).
from .commands import (
    CommandEvent,
    CommandHandler,
    CommandInfo,
    on_command,
)

# Configuration.
from .config import ModuleConfig

# Context objects (dependency injection).
from .context import (
    CommandContext,
    EventContext,
    ModuleContext,
    RouteContext,
)
from .event_types import (
    ChatEvent,
    Event,
    EventType,
    NameChangedEvent,
    StreamStartedEvent,
    StreamStatus,
    StreamStoppedEvent,
    StreamTitleUpdatedEvent,
    User,
    UserJoinedEvent,
    UserPartedEvent,
    VisibilityUpdateEvent,
)

# Event handler registration (module-facing).
from .events import (
    EventHandler,
    Priority,
    on_event,
)

# HTTP client.
from .http_client import HttpClient

# Lifecycle hooks (module-facing).
from .lifecycle import (
    LifecycleHandler,
    on_setup,
    on_teardown,
)

# Owncast admin client.
from .owncast_admin_client import (
    ExternalAction,
    OwncastAdminClient,
    SocialHandle,
    StreamKey,
    VideoVariant,
)

# Owncast client.
from .owncast_client import OwncastClient, OwncastError

# HTTP routes (module-facing).
from .routes import (
    RouteHandler,
    RouteInfo,
    on_route,
)

# Storage API.
from .storage import ModuleStorage, StorageError

__all__ = [
    "ChatEvent",
    "CommandContext",
    "CommandEvent",
    "CommandHandler",
    "CommandInfo",
    "Event",
    "EventContext",
    "EventHandler",
    "EventType",
    "ExternalAction",
    "HttpClient",
    "LifecycleHandler",
    "ModuleCommands",
    "ModuleConfig",
    "ModuleContext",
    "ModuleEvents",
    "ModuleRoutes",
    "ModuleStorage",
    "NameChangedEvent",
    "OwncastAdminClient",
    "OwncastClient",
    "OwncastError",
    "Priority",
    "RouteContext",
    "RouteHandler",
    "RouteInfo",
    "SocialHandle",
    "StorageError",
    "StreamKey",
    "StreamStartedEvent",
    "StreamStatus",
    "StreamStoppedEvent",
    "StreamTitleUpdatedEvent",
    "User",
    "UserJoinedEvent",
    "UserPartedEvent",
    "VideoVariant",
    "VisibilityUpdateEvent",
    "on_command",
    "on_event",
    "on_route",
    "on_setup",
    "on_teardown",
]
