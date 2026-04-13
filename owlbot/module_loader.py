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

"""Dynamic module loading for Owlbot."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from . import builtin_modules
from .api.config import Config, ModuleConfig
from .api.context import ModuleContext
from .api.owncast_admin_client import OwncastAdminClient
from .api.owncast_client import OwncastClient
from .api.storage import ModuleStorage
from .api.templates import ModuleTemplates
from .builtin_modules import BUILTIN_MODULE_NAMES
from .registries.commands import CommandDispatcher, ModuleCommands
from .registries.events import EventDispatcher, ModuleEvents
from .registries.routes import ModuleRoutes, RouteDispatcher

if TYPE_CHECKING:
    from types import ModuleType

    from .api.http_client import HttpClient
    from .api.lifecycle import LifecycleHandler

logger = logging.getLogger("owlbot.modules")

RESERVED_MODULE_NAMES: frozenset[str] = frozenset({"static"})


class ModuleLoadError(Exception):
    """Raised when a module fails to load."""


class ModuleLoader:
    """Discovers and loads Owlbot modules from built-in and user directories."""

    def __init__(
        self,
        modules_dir: str | Path,
        config: Config,
        http_client: HttpClient,
    ) -> None:
        """Initialize the module loader.

        :param modules_dir: Path to the user modules directory.
        :param config: Configuration object for checking module enable/disable state.
        :param http_client: Shared HTTP client for API clients and modules.
        """
        self.modules_dir = Path(modules_dir).resolve()
        self.config = config
        self.http_client = http_client
        self._module_contexts: dict[str, ModuleContext] = {}
        self.loaded_modules: set[str] = set()

        self.owncast_client = OwncastClient(
            config.owncast_url,
            config.owncast_access_token,
            http_client,
        )

        # Optional admin client (only created when admin.enabled is true).
        self.admin_client: OwncastAdminClient | None = None
        if config.admin_enabled:
            self.admin_client = OwncastAdminClient(
                config.owncast_url,
                config.admin_username,
                config.admin_password,
                http_client,
            )

        self.command_dispatcher = CommandDispatcher(
            get_module_context=self._require_module_context,
            owncast_client=self.owncast_client,
            handler_timeout=config.handler_timeout,
            loaded_modules=self.loaded_modules,
            command_prefix=config.command_prefix,
        )
        self.event_dispatcher = EventDispatcher(
            command_dispatch=self.command_dispatcher.dispatch,
            get_module_context=self._require_module_context,
            handler_timeout=config.handler_timeout,
        )
        self.route_dispatcher = RouteDispatcher(
            get_module_context=self._require_module_context,
            handler_timeout=config.handler_timeout,
        )

        self._core_template_dir = Path(__file__).resolve().parent / "templates"

        logger.debug(
            "ModuleLoader initialized (user modules directory: %s)",
            self.modules_dir,
        )

    def get_module_context(self, module_name: str) -> ModuleContext | None:
        """Look up a module's context by name.

        :param module_name: The module name.
        :return: The ModuleContext if the module is loaded, None otherwise.
        """
        return self._module_contexts.get(module_name)

    def _require_module_context(self, module_name: str) -> ModuleContext:
        """Look up a module's context, raising if the module is not loaded.

        Used by dispatchers where the module is guaranteed to be loaded.

        :param module_name: The module name.
        :return: The ModuleContext.
        :raises RuntimeError: If the module is not loaded.
        """
        ctx = self._module_contexts.get(module_name)
        if ctx is None:
            raise RuntimeError(f"Module '{module_name}' is not loaded")
        return ctx

    def discover_module_names(self) -> list[str]:
        """Discover all loadable modules from both built-in and user directories.

        Built-in modules ship with the package. User modules are discovered
        from the configured modules directory. If a user module has the same
        name as a built-in, the user module takes precedence and a warning is
        logged.

        :return: Sorted list of module names.
        """
        logger.debug("Discovering modules...")
        user_names = self._discover_user_module_names()

        reserved = user_names & RESERVED_MODULE_NAMES
        for name in sorted(reserved):
            logger.warning(
                "User module '%s' uses a reserved name and will be skipped.", name
            )
        user_names -= reserved

        overrides = user_names & BUILTIN_MODULE_NAMES
        for name in sorted(overrides):
            logger.info(
                "User module '%s' found; built-in module of "
                "the same name will be skipped.",
                name,
            )

        merged = BUILTIN_MODULE_NAMES | user_names
        modules = sorted(merged)
        builtin_count = len(BUILTIN_MODULE_NAMES - user_names)
        user_count = len(user_names)
        logger.info(
            "Discovered %d module(s) (%d built-in, %d user): %s",
            len(modules),
            builtin_count,
            user_count,
            ", ".join(modules) if modules else "none",
        )
        return modules

    def _discover_user_module_names(self) -> set[str]:
        """Scan the user modules directory for loadable modules.

        Supports both single-file modules (``name.py``) and package modules
        (``name/__init__.py``).  Files and directories starting with underscore
        are ignored.  If both forms exist for the same name, the module is
        listed once.

        :return: Set of user module names.
        """
        logger.debug("Scanning user modules directory: %s", self.modules_dir)
        if not self.modules_dir.exists():
            logger.debug("User modules directory does not exist: %s", self.modules_dir)
            return set()

        found: set[str] = set()

        # Single-file modules (*.py).
        for path in self.modules_dir.glob("*.py"):
            if path.name.startswith("_"):
                continue
            found.add(path.stem)

        # Package modules (directory with __init__.py).
        for path in self.modules_dir.iterdir():
            if not path.is_dir() or path.name.startswith("_"):
                continue
            if (path / "__init__.py").exists():
                found.add(path.name)

        if found:
            logger.debug(
                "Found %d user module(s): %s", len(found), ", ".join(sorted(found))
            )
        return found

    async def load_all_modules(self) -> list[str]:
        """Discover and load all enabled modules using two-phase loading.

        **Phase 1:** Import every module, create contexts, and register all
        decorated handlers (``@on_event``, ``@on_command``, ``@on_route``).

        **Phase 2:** Run ``@on_setup`` hooks for each successfully imported module.

        This ordering guarantees that all decorated (static) commands are
        registered before any module's ``@on_setup`` hooks attempt dynamic
        registration, preventing conflicts when an ``@on_setup`` hook tries to
        register a name already claimed by a decorated command.

        :return: Names of successfully loaded modules.
        """
        discovered = self.discover_module_names()
        logger.info("Loading %d module(s)...", len(discovered))

        imported: list[str] = []
        loaded: list[str] = []
        disabled_count = 0
        failed_count = 0

        # Phase 1: Import all modules and register decorated handlers.
        for module_name in discovered:
            try:
                await self.load_module(module_name, _run_setup=False)
                imported.append(module_name)
            except (FileNotFoundError, ModuleLoadError) as e:
                if "disabled in config" in str(e):
                    disabled_count += 1
                else:
                    logger.exception("Module import failed.")
                    failed_count += 1

        # Phase 2: Run @on_setup hooks for each imported module.
        logger.debug(
            "Import phase complete (%d imported). Running setup handlers...",
            len(imported),
        )
        for module_name in imported:
            try:
                await self._run_module_setup(module_name)
                loaded.append(module_name)
                logger.info("Loaded module '%s'.", module_name)
            except ModuleLoadError:
                logger.exception("Module setup failed.")
                failed_count += 1

        parts = [f"{len(loaded)} loaded"]
        if disabled_count:
            parts.append(f"{disabled_count} disabled")
        if failed_count:
            parts.append(f"{failed_count} failed")
        logger.info("Module loading complete: %s", ", ".join(parts))
        return loaded

    async def load_module(self, module_name: str, *, _run_setup: bool = True) -> None:
        """Load a single module by name.

        If both a package and single-file form exist for the same name,
        the package form is used. Checks if the module is enabled in
        config before loading. After the module is executed, decorated
        handlers (@on_event, @on_command) are scanned and registered.
        If the module defines any ``@on_setup`` hooks, they are called
        as the final step of loading (unless ``_run_setup`` is False,
        in which case setup is deferred).

        :param module_name: Name of the module to load (without .py extension).
        :param _run_setup: Whether to run the module's @on_setup hooks.
            When False, import and handler registration happen but setup
            is deferred.  Used internally by ``load_all_modules()`` to
            implement two-phase loading.
        :raises ValueError: If the module is already loaded.
        :raises FileNotFoundError: If the module file cannot be found.
        :raises ModuleLoadError: If the module is disabled in config,
            or if the module fails to load or setup fails.
        """
        if module_name in self.loaded_modules:
            raise ValueError(f"Module '{module_name}' is already loaded")

        if module_name in RESERVED_MODULE_NAMES:
            raise ModuleLoadError(
                f"Module name '{module_name}' is reserved and cannot be used"
            )

        logger.debug("Attempting to load module: %s", module_name)

        if not self.config.is_module_enabled(module_name):
            raise ModuleLoadError(f"Module '{module_name}' is disabled in config")

        module_path = self._resolve_module_path(module_name)

        try:
            # Load the module into an isolated namespace to prevent conflicts.
            # Using "owlbot_modules." prefix keeps these separate
            # from normal Python packages.
            spec = importlib.util.spec_from_file_location(
                f"owlbot_modules.{module_name}", module_path
            )

            if spec is None or spec.loader is None:
                raise ModuleLoadError(
                    f"Failed to create module spec for '{module_name}'"
                )

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"owlbot_modules.{module_name}"] = module

            logger.debug("Executing module: %s", module_name)
            spec.loader.exec_module(module)

            module_dir = module_path.parent
            module_templates = ModuleTemplates(module_dir, self._core_template_dir)

            scoped_config = ModuleConfig(self.config, module_name)
            storage = ModuleStorage(self.config.storage_dir, module_name)
            module_commands = ModuleCommands(self.command_dispatcher, module_name)
            module_events = ModuleEvents(self.event_dispatcher, module_name)
            module_routes = ModuleRoutes(
                self.route_dispatcher, module_name, self.config.public_base_url
            )
            module_ctx = ModuleContext(
                module_name=module_name,
                config=scoped_config,
                owncast_client=self.owncast_client,
                storage=storage,
                commands=module_commands,
                events=module_events,
                routes=module_routes,
                http=self.http_client,
                templates=module_templates,
                admin_client=self.admin_client,
            )
            self._module_contexts[module_name] = module_ctx

            self._register_module_handlers(module, module_name)

            self.loaded_modules.add(module_name)

            if _run_setup:
                await self._run_module_setup(module_name)
                logger.info("Loaded module '%s'.", module_name)
            else:
                logger.debug("Imported module '%s' (setup deferred).", module_name)

        except ModuleLoadError:
            raise
        except Exception as e:
            # Close storage if it was created before the failure.
            cleanup_ctx = self._module_contexts.get(module_name)
            if cleanup_ctx:
                try:
                    # Framework lifecycle; private to module authors.
                    await cleanup_ctx.storage._close()  # noqa: SLF001
                except Exception:
                    logger.exception(
                        "Failed to close storage for module '%s' "
                        "during load error cleanup.",
                        module_name,
                    )
            self._cleanup_module(module_name)
            raise ModuleLoadError(f"Failed to load module '{module_name}': {e}") from e

    async def unload_module(self, module_name: str) -> bool:
        """Unload a module, calling its teardown and cleaning up all state.

        :param module_name: The module to unload.
        :return: True if module was unloaded, False if not found.
        """
        logger.debug("Unloading module '%s'", module_name)
        if module_name not in self.loaded_modules:
            logger.warning("Cannot unload '%s': not loaded", module_name)
            return False

        module_ctx = self._module_contexts.get(module_name)

        module = sys.modules.get(f"owlbot_modules.{module_name}")
        teardown_funcs = (
            self._collect_lifecycle_handlers(module, "_owlbot_teardown")
            if module
            else []
        )
        if teardown_funcs and module_ctx:
            for teardown_func in teardown_funcs:
                try:
                    logger.debug("Running @on_teardown for module: %s", module_name)
                    await teardown_func(module_ctx)
                    logger.debug("Teardown completed for module: %s", module_name)
                except Exception:
                    logger.exception("Teardown failed for module '%s'.", module_name)
        else:
            logger.debug("Module '%s' has no @on_teardown handlers.", module_name)

        if module_ctx:
            try:
                # Framework lifecycle; private to module authors.
                await module_ctx.storage._close()  # noqa: SLF001
            except Exception:
                logger.exception(
                    "Failed to close storage for module '%s'.", module_name
                )

        self._cleanup_module(module_name)
        logger.info("Unloaded module '%s'.", module_name)
        return True

    async def unload_all_modules(self) -> None:
        """Unload all modules, calling teardown and cleaning up all state.

        Called during bot shutdown to allow modules to clean up resources.
        """
        if not self.loaded_modules:
            return

        logger.info("Unloading %d module(s)...", len(self.loaded_modules))
        for module_name in list(self.loaded_modules):
            await self.unload_module(module_name)
        logger.info("All module unload complete.")

    async def _run_module_setup(self, module_name: str) -> None:
        """Run a module's ``@on_setup`` hooks if any are defined.

        Each storage operation within a setup handler auto-commits
        independently.  If any handler fails, storage is closed and the
        module is fully cleaned up.

        :param module_name: Name of the module whose setup to run.
        :raises ModuleLoadError: If any @on_setup handler raises an exception.
        """
        module = sys.modules.get(f"owlbot_modules.{module_name}")
        setup_funcs = (
            self._collect_lifecycle_handlers(module, "_owlbot_setup") if module else []
        )
        if not setup_funcs:
            logger.debug("Module '%s' has no @on_setup handlers.", module_name)
            return

        module_ctx = self._module_contexts[module_name]
        try:
            for setup_func in setup_funcs:
                logger.debug("Running @on_setup for module: %s", module_name)
                await setup_func(module_ctx)
            logger.debug("Setup completed for module: %s", module_name)
        except Exception as e:
            # Framework lifecycle; private to module authors.
            await module_ctx.storage._close()  # noqa: SLF001
            self._cleanup_module(module_name)
            raise ModuleLoadError(
                f"Setup failed for module '{module_name}': {e}"
            ) from e

    def _resolve_module_path(self, module_name: str) -> Path:
        """Resolve the filesystem path for a module by name.

        User modules are checked first (package form, then single-file).
        If no user module is found and the name is a built-in, the built-in
        path is returned.

        :param module_name: The module name to resolve.
        :return: The resolved path to the module file.
        :raises FileNotFoundError: If neither form exists.
        """
        # Check user modules directory first (allows overriding built-ins).
        if self.modules_dir.exists():
            package_path = self.modules_dir / module_name / "__init__.py"
            if package_path.exists():
                return package_path

            single_path = self.modules_dir / f"{module_name}.py"
            if single_path.exists():
                return single_path

        # Fall back to built-in modules.
        if module_name in BUILTIN_MODULE_NAMES:
            return self._resolve_builtin_module_path(module_name)

        raise FileNotFoundError(f"Module not found: '{module_name}'")

    @staticmethod
    def _resolve_builtin_module_path(module_name: str) -> Path:
        """Locate a built-in module's ``__init__.py`` inside the package.

        :param module_name: Name of the built-in module.
        :return: Path to the module's ``__init__.py``.
        """
        return (
            Path(builtin_modules.__file__).resolve().parent
            / module_name
            / "__init__.py"
        )

    def _register_module_handlers(self, module: ModuleType, module_name: str) -> None:
        """Scan a module for decorated handlers and register them.

        Delegates to each registry's ``register_from_module()`` method,
        which knows how to find its own decorator markers.

        :param module: The loaded Python module to scan.
        :param module_name: Name of the module (for ownership tracking).
        """
        self.event_dispatcher.register_from_module(module, module_name)
        self.command_dispatcher.register_from_module(module, module_name)
        self.route_dispatcher.register_from_module(module, module_name)

    @staticmethod
    def _collect_lifecycle_handlers(
        module: ModuleType, marker: str
    ) -> list[LifecycleHandler]:
        """Collect callables from a module that have a given marker attribute.

        Scans ``vars(module)`` for callable objects where ``getattr(obj, marker)``
        is truthy.  Used to find ``@on_setup`` (marker ``"_owlbot_setup"``) and
        ``@on_teardown`` (marker ``"_owlbot_teardown"``) handlers.

        :param module: The loaded Python module to scan.
        :param marker: The attribute name to look for (e.g. ``"_owlbot_setup"``).
        :return: List of matching callables.
        """
        return [
            cast("LifecycleHandler", obj)
            for obj in vars(module).values()
            if callable(obj) and getattr(obj, marker, False)
        ]

    def _cleanup_module(self, module_name: str) -> None:
        """Remove all state associated with a module.

        Removes the top-level module entry and any submodule entries
        (for package-style modules) from ``sys.modules``.

        :param module_name: The module to clean up.
        """
        prefix = f"owlbot_modules.{module_name}"
        # Remove submodules first (e.g. owlbot_modules.custom_commands.handler),
        # then the top-level entry.
        for key in [k for k in sys.modules if k.startswith(f"{prefix}.")]:
            sys.modules.pop(key, None)
        sys.modules.pop(prefix, None)
        self._module_contexts.pop(module_name, None)
        self.loaded_modules.discard(module_name)
        self.event_dispatcher.unregister_by_module(module_name)
        self.command_dispatcher.unregister_by_module(module_name)
        self.route_dispatcher.unregister_by_module(module_name)
        logger.debug("Cleaned up module '%s'.", module_name)
