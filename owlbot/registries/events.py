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
import logging
import time
from typing import TYPE_CHECKING, NamedTuple, cast

from ..api.context import PropagationState
from ..api.event_types import ChatEvent, Event, EventType, log_event
from ..api.events import EventHandler, EventMark, Priority


class HandlerEntry(NamedTuple):
    """A registered event handler with its module name and priority."""

    handler: EventHandler
    module_name: str
    priority: int


type EventHandlerMap = dict[str, list[HandlerEntry]]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType

    from ..api.context import EventContext, ModuleContext

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
        self._handlers: EventHandlerMap = {}

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

            if key not in self._handlers:
                self._handlers[key] = []

            # Add the handler to the list. Multiple handlers
            # for the same event are allowed.
            self._handlers[key].append(HandlerEntry(handler, module_name, priority))
            module_logger = logging.getLogger(f"owlbot.modules.{module_name}.events")
            module_logger.debug(
                f"Registered handler '{handler.__name__}' for {event_type.value} "
                f"(priority={priority})."
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
                f"Unregistered handler '{handler.__name__}' "
                f"for {', '.join(removed_from)}."
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
            module_logger.debug(f"Unregistered all handlers ({len(seen)} total).")
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

        # Phase 1: Event handlers (sequential, sorted by priority descending).
        if handler_entries:
            # Sort handlers by priority (highest first).
            # Python's sort is stable, so handlers at the same priority
            # run in their original registration order.
            sorted_handlers = sorted(
                handler_entries,
                key=lambda entry: entry.priority,
                reverse=True,
            )

            logger.debug(
                f"Dispatching {event_type} to {len(sorted_handlers)} handler(s)."
            )

            for handler, module_name, _priority in sorted_handlers:
                # Has propagation been stopped by a previous handler?
                if propagation.stopped:
                    reason = propagation.reason
                    logger.debug(
                        f"Propagation stopped{': ' + reason if reason else '.'}"
                    )
                    break

                await self._call_handler(
                    handler, event, event_type, module_name, propagation
                )
        else:
            logger.debug(f"No handlers registered for event type: {event_type}")

        # Phase 2: Command dispatch (CHAT events only, if not cancelled).
        if event_type == EventType.CHAT and isinstance(event, ChatEvent):
            # Has propagation been stopped?
            if propagation.stopped:
                reason = propagation.reason
                logger.debug(
                    f"Command dispatch skipped{': ' + reason if reason else '.'}"
                )
            else:
                try:
                    await self._command_dispatch(event)
                except Exception as e:
                    logger.exception(f"Command dispatch failed: {e}")

    async def _call_handler(
        self,
        handler: EventHandler,
        event: Event,
        event_type: EventType,
        module_name: str,
        propagation: PropagationState,
    ) -> None:
        """Call a single handler with timeout enforcement and transaction management.

        :param handler: The handler function to call.
        :param event: The event to pass to the handler.
        :param event_type: The type of event being dispatched.
        :param module_name: The module that owns this handler.
        :param propagation: Shared propagation state for this dispatch cycle.
        """
        from ..api.context import EventContext

        handler_name = handler.__name__
        logger.debug(f"Calling handler: {handler_name} from module: {module_name}")

        module_ctx = self._get_module_context(module_name)

        ctx: EventContext[Event] = EventContext(
            event=event,
            module=module_ctx,
            _propagation=propagation,
        )

        # Call the handler with timeout enforcement to prevent
        # runaway handlers from blocking everything.
        # The checkout acquires a pooled connection for the
        # duration of this handler invocation.
        async with module_ctx.storage._checkout():
            try:
                start = time.perf_counter()
                await asyncio.wait_for(handler(ctx), timeout=self._handler_timeout)
                elapsed = (time.perf_counter() - start) * 1000

                # Handler succeeded, commit any database changes.
                await module_ctx.storage._commit()

                logger.debug(f"Handler '{handler_name}' completed in {elapsed:.1f}ms.")
            except TimeoutError:
                # Handler took too long. Rollback any partial changes.
                await module_ctx.storage._rollback()
                logger.warning(
                    f"Handler '{handler_name}' from module '{module_name}' "
                    f"cancelled after {self._handler_timeout}s timeout."
                )
            except Exception as e:
                # Handler raised an exception. Rollback any partial changes.
                await module_ctx.storage._rollback()
                logger.exception(
                    f"Handler '{handler_name}' from module "
                    f"'{module_name}' raised exception: {e}"
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
