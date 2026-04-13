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

"""Event handler registry, module-scoped wrapper, and dispatcher.

Internal infrastructure for managing event handler registration and dispatch.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, NamedTuple, cast

from owlbot.api.context import EventContext, PropagationState
from owlbot.api.event_types import ChatEvent, Event, EventType, log_event
from owlbot.api.events import EventHandler, EventMark, Priority


class HandlerEntry(NamedTuple):
    """A registered event handler with its module name and priority."""

    handler: EventHandler
    module_name: str
    priority: int


type EventHandlerMap = dict[str, list[HandlerEntry]]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

    from owlbot.api.context import ModuleContext

logger = logging.getLogger("owlbot.events")


class EventRegistry:
    """Holds all registered event handlers for a bot instance.

    Instance-scoped to enable proper dependency injection and allow multiple
    bot instances to coexist without sharing state.
    """

    def __init__(self) -> None:
        """Initialize an empty handler registry."""
        # Maps event type values (strings) to lists of
        # (handler, module_name, priority) tuples.
        self._handlers: EventHandlerMap = defaultdict(list)

    def register(
        self,
        handler: EventHandler,
        event_types: tuple[EventType, ...],
        module_name: str,
        priority: int = Priority.NORMAL,
    ) -> None:
        """Register a handler for the given event types.

        Called by the module loader after scanning for decorated functions.

        :param handler: The handler function to register.
        :param event_types: Tuple of EventType values the handler responds to.
        :param module_name: Name of the module this handler belongs to.
        :param priority: Dispatch priority (higher runs first).
            Defaults to Priority.NORMAL.
        """
        # A single handler can respond to multiple event types.
        for event_type in event_types:
            key = event_type.value

            # Insert in descending priority order so the dispatch loop
            # can iterate without a separate sort step. The priority is
            # negated because bisect works in ascending order. bisect.insort
            # uses bisect_right, so same-priority handlers stay in
            # registration (FIFO) order.
            entry = HandlerEntry(handler, module_name, priority)
            bisect.insort(self._handlers[key], entry, key=lambda e: -e.priority)
            module_logger = logging.getLogger(f"owlbot.modules.{module_name}.events")
            module_logger.debug(
                "Registered handler '%s' for %s (priority=%s).",
                handler.__name__,
                event_type.value,
                priority,
            )

    def unregister(self, handler: EventHandler) -> bool:
        """Unregister a handler from all event types it is registered for.

        :param handler: The handler function to unregister.
        :return: True if handler was found and removed, False otherwise.
        """
        module_name = None
        removed_from: list[str] = []
        for key in self._handlers:
            original = self._handlers[key]
            kept = []
            for entry in original:
                if entry.handler is handler:
                    module_name = entry.module_name
                    removed_from.append(key)
                else:
                    kept.append(entry)
            self._handlers[key] = kept

        if module_name and removed_from:
            module_logger = logging.getLogger(f"owlbot.modules.{module_name}.events")
            module_logger.debug(
                "Unregistered handler '%s' for %s.",
                handler.__name__,
                ", ".join(removed_from),
            )
            return True
        return False

    def get(self, event_type: EventType) -> list[HandlerEntry]:
        """Get all handlers registered for a specific event type.

        :param event_type: The event type to look up.
        :return: List of (handler, module_name, priority) tuples.
        """
        return self._handlers.get(event_type.value, [])

    def get_all(self) -> EventHandlerMap:
        """Get a copy of the entire handler registry.

        :return: Dict mapping event type values to handler lists.
        """
        return {
            event_type: entries.copy() for event_type, entries in self._handlers.items()
        }

    def get_handler_module(self, handler: EventHandler) -> str | None:
        """Look up which module registered a given handler.

        :param handler: The handler function to look up.
        :return: The module name if found, None otherwise.
        """
        for handler_list in self._handlers.values():
            for entry in handler_list:
                if entry.handler is handler:
                    return entry.module_name
        return None

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all handlers registered by a specific module.

        :param module_name: The module whose handlers should be removed.
        :return: Number of handlers removed.
        """
        module_logger = logging.getLogger(f"owlbot.modules.{module_name}.events")

        seen: set[EventHandler] = set()
        for key in self._handlers:
            kept = []
            for entry in self._handlers[key]:
                if entry.module_name == module_name:
                    seen.add(entry.handler)
                else:
                    kept.append(entry)
            self._handlers[key] = kept

        if seen:
            module_logger.debug("Unregistered all handlers (%d total).", len(seen))
        return len(seen)

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for @on_event-decorated functions and register them.

        Looks for functions with the ``_owlbot_event`` attribute set by
        the ``@on_event`` decorator and registers each one.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        for obj in vars(module).values():
            if callable(obj):
                event_info = getattr(obj, "_owlbot_event", None)
                if event_info is not None:
                    mark = cast("EventMark", event_info)
                    self.register(
                        obj, mark["event_types"], module_name, mark["priority"]
                    )


class EventDispatcher:
    """Dispatches events to registered handlers sequentially by priority.

    After all event handlers complete, command dispatch is triggered for
    CHAT events (via the injected command_dispatch callback).
    """

    def __init__(
        self,
        command_dispatch: Callable[[ChatEvent], Awaitable[None]],
        get_module_context: Callable[[str], ModuleContext],
        handler_timeout: float,
    ) -> None:
        """Initialize the event dispatcher.

        Creates and owns a :class:`EventRegistry` internally.

        :param command_dispatch: Async callback to dispatch
            commands for CHAT events.
        :param get_module_context: Callable that looks up a
            ModuleContext by module name.
        :param handler_timeout: Timeout for individual handlers in seconds.
        """
        self._handler_registry = EventRegistry()
        self._command_dispatch = command_dispatch
        self._get_module_context = get_module_context
        self._handler_timeout = handler_timeout

    def register(
        self,
        handler: EventHandler,
        event_types: tuple[EventType, ...],
        module_name: str,
        priority: int = Priority.NORMAL,
    ) -> None:
        """Register a handler for the given event types.

        A single handler can respond to multiple event types; the registry
        stores a separate entry per event type. This is the shared entry
        point for both decorator-based registration (via ``register_from_module``)
        and dynamic registration (via ``ModuleEvents.register``).

        :param handler: The handler function to register.
        :param event_types: Tuple of EventType values the handler responds to.
        :param module_name: Name of the module this handler
            belongs to.
        :param priority: Dispatch priority (higher runs first).
            Defaults to Priority.NORMAL.
        """
        self._handler_registry.register(handler, event_types, module_name, priority)

    def unregister(self, handler: EventHandler) -> bool:
        """Unregister a handler from all event types it is registered for.

        Unlike commands (looked up by name string), event handlers are
        identified by object identity. A handler registered for multiple
        event types is removed from every one of them in a single call.

        :param handler: The handler function to unregister.
        :return: True if handler was found and removed, False otherwise.
        """
        return self._handler_registry.unregister(handler)

    def get_by_module(self, module_name: str) -> EventHandlerMap:
        """Get all handlers registered by a specific module, grouped by event type.

        The registry stores handlers grouped by event type, not by module,
        so this filters across all event types to collect a single module's
        handlers.

        :param module_name: The module whose handlers to return.
        :return: Dict mapping event type values to handler lists for that module.
        """
        result: EventHandlerMap = {}
        for event_type_value, handler_list in self._handler_registry.get_all().items():
            filtered = [
                entry for entry in handler_list if entry.module_name == module_name
            ]
            if filtered:
                result[event_type_value] = filtered
        return result

    def get_handler_module(self, handler: EventHandler) -> str | None:
        """Reverse-lookup which module registered a given handler.

        Scans all event types since handlers are stored by event type,
        not by module.

        :param handler: The handler function to look up.
        :return: The module name if found, None otherwise.
        """
        return self._handler_registry.get_handler_module(handler)

    def register_from_module(self, module: ModuleType, module_name: str) -> None:
        """Scan a Python module for ``@on_event``-decorated functions and register them.

        This is the import-phase entry point: the module loader calls it once
        per module. Decorator attributes are read here and passed as explicit
        parameters to ``register()``, so no decorator markers are used after
        this point.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        self._handler_registry.register_from_module(module, module_name)

    def unregister_by_module(self, module_name: str) -> int:
        """Remove all handlers registered by a specific module.

        Used during module teardown to clean up all of a module's handlers
        in one call, regardless of which event types they were registered for.

        :param module_name: The module whose handlers should be removed.
        :return: Number of handlers removed.
        """
        return self._handler_registry.unregister_by_module(module_name)

    async def dispatch(self, event_type: EventType, event: Event) -> None:
        """Dispatch an event to handlers sequentially by priority, then to commands.

        :param event_type: The type of event to dispatch.
        :param event: The parsed event instance.
        """
        log_event(event_type, event)

        handler_entries = self._handler_registry.get(event_type)

        # Shared state for propagation control (all handlers see the same instance).
        propagation = PropagationState()

        # Phase 1: Event handlers (sequential, already sorted by priority descending).
        if handler_entries:
            logger.debug(
                "Dispatching %s to %d handler(s).", event_type, len(handler_entries)
            )

            for handler, module_name, _priority in handler_entries:
                # Has propagation been stopped by a previous handler?
                if propagation.stopped:
                    reason = propagation.reason
                    logger.debug(
                        "Propagation stopped%s",
                        ": " + reason if reason else ".",
                    )
                    break

                await self._call_handler(handler, event, module_name, propagation)
        else:
            logger.debug("No handlers registered for event type: %s", event_type)

        # Phase 2: Command dispatch (CHAT events only, if not cancelled).
        if event_type == EventType.CHAT and isinstance(event, ChatEvent):
            # Has propagation been stopped?
            if propagation.stopped:
                reason = propagation.reason
                logger.debug(
                    "Command dispatch skipped%s",
                    ": " + reason if reason else ".",
                )
            else:
                try:
                    await self._command_dispatch(event)
                except Exception:
                    logger.exception("Command dispatch failed.")

    async def _call_handler(
        self,
        handler: EventHandler,
        event: Event,
        module_name: str,
        propagation: PropagationState,
    ) -> None:
        """Call a single handler with timeout enforcement.

        :param handler: The handler function to call.
        :param event: The event to pass to the handler.
        :param module_name: The module that owns this handler.
        :param propagation: Shared propagation state for this dispatch cycle.
        """
        handler_name = handler.__name__
        logger.debug("Calling handler: %s from module: %s", handler_name, module_name)

        module_ctx = self._get_module_context(module_name)

        ctx: EventContext[Event] = EventContext(
            event=event,
            module=module_ctx,
            _propagation=propagation,
        )

        try:
            start = time.perf_counter()
            await asyncio.wait_for(handler(ctx), timeout=self._handler_timeout)
            elapsed = (time.perf_counter() - start) * 1000

            logger.debug("Handler '%s' completed in %.1fms.", handler_name, elapsed)
        except TimeoutError:
            logger.warning(
                "Handler '%s' from module '%s' cancelled after %ss timeout.",
                handler_name,
                module_name,
                self._handler_timeout,
            )
        except Exception:
            logger.exception(
                "Handler '%s' from module '%s' raised exception.",
                handler_name,
                module_name,
            )


class ModuleEvents:
    """Module-scoped wrapper around EventDispatcher.

    This wrapper auto-supplies the module name for registration operations,
    so modules don't need to pass their own name back into the API.
    Follows the same pattern as ModuleCommands and ModuleRoutes.
    """

    def __init__(self, dispatcher: EventDispatcher, module_name: str) -> None:
        """Initialize the module-scoped handler wrapper.

        :param dispatcher: The EventDispatcher that owns the handler registry.
        :param module_name: The name of the module using this wrapper.
        """
        self._dispatcher = dispatcher
        self._module_name = module_name

    @property
    def module_events(self) -> EventHandlerMap:
        """Handlers registered by this module only, grouped by event type."""
        return self._dispatcher.get_by_module(self._module_name)

    def register(
        self,
        handler: EventHandler,
        event_types: tuple[EventType, ...],
        priority: int = Priority.NORMAL,
    ) -> None:
        """Register an event handler for this module.

        The module name is automatically supplied.

        :param handler: The handler function to register.
        :param event_types: Tuple of EventType values the handler responds to.
        :param priority: Dispatch priority (higher runs first).
            Defaults to Priority.NORMAL.
        """
        self._dispatcher.register(
            handler=handler,
            event_types=event_types,
            module_name=self._module_name,
            priority=priority,
        )

    def unregister(self, handler: EventHandler) -> bool:
        """Unregister a handler from all event types it is registered for.

        Only handlers registered by this module can be unregistered.

        :param handler: The handler function to unregister.
        :return: True if handler was found and removed, False if not found or not owned.
        """
        # Only allow unregistering handlers owned by this module.
        if self._dispatcher.get_handler_module(handler) != self._module_name:
            return False
        return self._dispatcher.unregister(handler)

    def get(self, event_type: EventType) -> list[HandlerEntry]:
        """Get handlers registered by this module for a specific event type.

        :param event_type: The event type to look up.
        :return: List of (handler, module_name, priority) tuples for this module only.
        """
        return self._dispatcher.get_by_module(self._module_name).get(
            event_type.value, []
        )
