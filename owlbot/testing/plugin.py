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

"""Pytest plugin for Owlbot module testing.

The fixture layering mirrors :class:`~owlbot.module_loader.ModuleLoader`:

* A shared ``_context_registry`` maps module names to ``ModuleContext``
  instances (production equivalent: ``ModuleLoader._module_contexts``).
* A shared ``_loaded_modules`` set is wired into the dispatchers and
  mutated as modules are registered (production equivalent:
  ``ModuleLoader.loaded_modules``).
* Dispatchers close over the registry for lookups, raising
  :class:`pytest.UsageError` for unknown names.
* ``module_context`` builds the default module's context on demand
  (mirroring ``ModuleLoader.load_module``) and caches it in the registry.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from aiohttp import web
from aioresponses import aioresponses
from ruamel.yaml import YAML

import owlbot
from owlbot.api.config import Config, ModuleConfig
from owlbot.api.context import ModuleContext
from owlbot.api.http_client import HttpClient
from owlbot.api.storage import ModuleStorage
from owlbot.api.templates import ModuleTemplates
from owlbot.http_server import register_response_prepare_hook
from owlbot.module_loader import ModuleLoader
from owlbot.registries.commands import CommandDispatcher, ModuleCommands
from owlbot.registries.events import EventDispatcher, ModuleEvents
from owlbot.registries.routes import ModuleRoutes, RouteDispatcher
from owlbot.sessions import SessionManager
from owlbot.web.sessions import register_browser_session_routes

from .stubs import RecordingOwncastAdminClient, RecordingOwncastClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType

    from aiohttp.test_utils import TestClient


def _make_context_lookup(
    registry: dict[str, ModuleContext],
) -> Callable[[str], ModuleContext]:
    """Build a ``get_module_context`` callable that reads from the registry.

    Unknown names raise :class:`pytest.UsageError` with a message pointing
    the reader at ``module_context`` to correct the setup.
    """

    def lookup(name: str) -> ModuleContext:
        try:
            return registry[name]
        except KeyError as exc:
            msg = (
                f"No module context registered for {name!r}. Use the "
                f"module_context fixture to register the default module."
            )
            raise pytest.UsageError(msg) from exc

    return lookup


@pytest.fixture
def module_pkg() -> ModuleType | None:
    """Package of the Owlbot module under test.

    Override with the imported package (e.g., ``owlbot.builtin_modules.quotes``
    or ``owlbot_modules.my_module``) to auto-wire the module name, templates,
    and the ``module_lifecycle`` / ``registered_handlers`` fixtures for that
    module.
    """
    return None


@pytest.fixture
def module_name(module_pkg: ModuleType | None) -> str:
    """Canonical name of the default module under test.

    Defaults to the last dotted segment of ``module_pkg.__name__``, or
    ``"test_module"`` when ``module_pkg`` is unset. Override this fixture
    directly to set a specific name without importing a package.
    """
    if module_pkg is None:
        return "test_module"
    return module_pkg.__name__.rsplit(".", 1)[-1]


@pytest.fixture
def admin_client_enabled() -> bool:
    """Whether the admin client is wired into the module context."""
    return False


@pytest.fixture
def command_prefix() -> str:
    """Prefix character used for command parsing."""
    return "!"


@pytest.fixture
def handler_timeout() -> float:
    """Timeout in seconds for handler execution."""
    return 30.0


@pytest.fixture
def config_data() -> dict[str, Any]:
    """Seed YAML content for the real :class:`~owlbot.api.config.Config`.

    Override to customize any section (e.g. set ``owlbot.public_base_url``
    or a ``modules.<name>`` block) before ``Config`` loads.
    """
    return {
        "owncast": {"url": "http://localhost:8080"},
        "owlbot": {"public_base_url": "http://localhost:8081"},
    }


@pytest.fixture
def config_path(tmp_path: Path, config_data: dict[str, Any]) -> Path:
    """Write ``config_data`` to ``tmp_path/config.yaml`` and return its path."""
    path = tmp_path / "config.yaml"
    yaml = YAML(typ="safe")
    with path.open("w") as f:
        yaml.dump(config_data, f)
    return path


@pytest.fixture
def config(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Construct a real ``Config`` backed by a tmp YAML file.

    ``OWLBOT_*`` environment variables are cleared for the duration of the
    test so CI-level overrides do not bleed into the fixture.
    """
    for name in list(os.environ):
        if name.startswith("OWLBOT_"):
            monkeypatch.delenv(name, raising=False)
    return Config(config_path)


@pytest_asyncio.fixture
async def storage(module_name: str) -> AsyncIterator[ModuleStorage]:
    """Yield an open in-memory ModuleStorage for the default module."""
    async with ModuleStorage(None, module_name) as s:
        yield s


@pytest.fixture
def owncast_client(config: Config) -> RecordingOwncastClient:
    """Return a recording stub for the Owncast client.

    The stub's ``base_url`` is seeded from ``config.owncast_url`` so it stays
    consistent with any ``config_data`` override the test sets.
    """
    return RecordingOwncastClient(base_url=config.owncast_url)


@pytest.fixture
def admin_client(
    config: Config,
    *,
    admin_client_enabled: bool,
) -> RecordingOwncastAdminClient:
    """Return a recording stub for the Owncast admin client.

    The stub's ``base_url`` is seeded from ``config.owncast_url`` so it stays
    consistent with any ``config_data`` override the test sets.

    :raises pytest.UsageError: If ``admin_client_enabled`` has not been
        overridden to ``True``. Prevents tests from asserting on a
        disconnected stub that handlers never see.
    """
    if not admin_client_enabled:
        raise pytest.UsageError(
            "admin_client requires admin_client_enabled=True; override the "
            "admin_client_enabled fixture to enable the admin client"
        )
    return RecordingOwncastAdminClient(base_url=config.owncast_url)


@pytest.fixture
def module_config(config: Config, module_name: str) -> ModuleConfig:
    """Return a real ``ModuleConfig`` scoped to ``module_name``."""
    return ModuleConfig(config, module_name)


@pytest.fixture
def templates(module_pkg: ModuleType | None, tmp_path: Path) -> ModuleTemplates:
    """Return a Jinja2 ``ModuleTemplates`` renderer for the default module.

    Derives from ``module_pkg`` when set (module templates + core). When unset,
    falls back to a core-only renderer (backed by an empty ``tmp_path``) so
    module-specific templates resolve to ``TemplateNotFound`` rather than
    ``AttributeError``.
    """
    core_dir = Path(owlbot.__file__).parent / "templates"
    if module_pkg is None:
        return ModuleTemplates(tmp_path, core_dir)
    if module_pkg.__file__ is None:
        msg = f"module package {module_pkg.__name__!r} has no __file__"
        raise RuntimeError(msg)
    return ModuleTemplates(Path(module_pkg.__file__).parent, core_dir)


@pytest.fixture
def mocked_http() -> Iterator[aioresponses]:
    """Yield an ``aioresponses`` controller intercepting aiohttp requests.

    Active for every test that uses the ``http`` fixture (directly or via
    ``module_context``). Register canned responses on the yielded object to
    simulate external APIs. Requests to unregistered URLs raise
    ``ConnectionError``, so tests cannot accidentally reach the network.

    Loopback addresses (``127.0.0.1`` and ``localhost``) pass through
    unmocked so the ``route_client`` fixture can reach its own aiohttp
    test server.
    """
    with aioresponses(passthrough=["http://127.0.0.1", "http://localhost"]) as m:
        yield m


@pytest_asyncio.fixture
async def http(mocked_http: aioresponses) -> AsyncIterator[HttpClient]:
    """Yield a started :class:`HttpClient`.

    Depends on ``mocked_http`` so every request is intercepted by the
    aioresponses patch. The underlying aiohttp session is closed when the
    test finishes.
    """
    client = HttpClient()
    await client._start()  # noqa: SLF001  # framework lifecycle reused for tests
    try:
        yield client
    finally:
        await client._close()  # noqa: SLF001  # framework lifecycle reused for tests


@pytest.fixture
def _context_registry() -> dict[str, ModuleContext]:
    """Shared cache of ModuleContexts keyed by module name.

    Mirrors :attr:`owlbot.module_loader.ModuleLoader._module_contexts`. The
    dispatcher fixtures close over this dict for lookups, and
    ``module_context`` populates it on demand.
    """
    return {}


@pytest.fixture
def _loaded_modules() -> set[str]:
    """Shared set of module names currently loaded.

    Mirrors :attr:`owlbot.module_loader.ModuleLoader.loaded_modules`. Wired
    into ``command_dispatcher`` and mutated by ``module_context`` and
    ``registered_handlers`` as modules are registered.
    """
    return set()


@pytest.fixture
def session_manager() -> SessionManager:
    """Return a shared session manager for the test bot instance."""
    return SessionManager()


@pytest.fixture
def command_dispatcher(
    _context_registry: dict[str, ModuleContext],
    _loaded_modules: set[str],
    session_manager: SessionManager,
    config: Config,
    owncast_client: RecordingOwncastClient,
    handler_timeout: float,
    command_prefix: str,
) -> CommandDispatcher:
    """Build a CommandDispatcher that resolves contexts via ``_context_registry``."""
    return CommandDispatcher(
        get_module_context=_make_context_lookup(_context_registry),
        owncast_client=owncast_client,
        handler_timeout=handler_timeout,
        loaded_modules=_loaded_modules,
        command_prefix=command_prefix,
        session_manager=session_manager,
        public_base_url=config.public_base_url,
    )


@pytest.fixture
def event_dispatcher(
    _context_registry: dict[str, ModuleContext],
    session_manager: SessionManager,
    command_dispatcher: CommandDispatcher,
    handler_timeout: float,
) -> EventDispatcher:
    """Build an EventDispatcher that resolves contexts via ``_context_registry``."""
    return EventDispatcher(
        command_dispatch=command_dispatcher.dispatch,
        get_module_context=_make_context_lookup(_context_registry),
        handler_timeout=handler_timeout,
        session_manager=session_manager,
    )


@pytest.fixture
def route_dispatcher(
    _context_registry: dict[str, ModuleContext],
    handler_timeout: float,
    session_manager: SessionManager,
    command_prefix: str,
) -> RouteDispatcher:
    """Build a RouteDispatcher that resolves contexts via ``_context_registry``."""
    return RouteDispatcher(
        get_module_context=_make_context_lookup(_context_registry),
        handler_timeout=handler_timeout,
        session_manager=session_manager,
        command_prefix=command_prefix,
    )


@pytest.fixture
def module_context(
    _context_registry: dict[str, ModuleContext],
    _loaded_modules: set[str],
    module_name: str,
    command_dispatcher: CommandDispatcher,
    event_dispatcher: EventDispatcher,
    route_dispatcher: RouteDispatcher,
    storage: ModuleStorage,
    owncast_client: RecordingOwncastClient,
    module_config: ModuleConfig,
    templates: ModuleTemplates,
    http: HttpClient,
    session_manager: SessionManager,
    request: pytest.FixtureRequest,
    *,
    admin_client_enabled: bool,
) -> ModuleContext:
    """Return the ModuleContext for the default module.

    Mirrors the context-building half of
    :meth:`owlbot.module_loader.ModuleLoader.load_module`: wires the
    name-scoped ``ModuleCommands`` / ``ModuleEvents`` / ``ModuleRoutes``
    against the dispatchers, registers the context in ``_context_registry``,
    and adds the name to ``_loaded_modules``.
    """
    admin_client = (
        request.getfixturevalue("admin_client") if admin_client_enabled else None
    )
    ctx = ModuleContext(
        module_name=module_name,
        config=module_config,
        owncast_client=owncast_client,
        storage=storage,
        commands=ModuleCommands(command_dispatcher, module_name),
        events=ModuleEvents(event_dispatcher, module_name),
        routes=ModuleRoutes(
            route_dispatcher,
            module_name,
            module_config.public_base_url,
        ),
        http=http,
        templates=templates,
        admin_client=admin_client,
    )
    _context_registry[module_name] = ctx
    _loaded_modules.add(module_name)
    return ctx


@pytest_asyncio.fixture
async def route_client(
    route_dispatcher: RouteDispatcher,
    session_manager: SessionManager,
    command_prefix: str,
    config: Config,
    aiohttp_client: Any,
) -> TestClient[Any, Any]:
    """Return an aiohttp test client wired to the RouteDispatcher."""
    app = web.Application()
    register_response_prepare_hook(app, session_manager=session_manager)
    register_browser_session_routes(
        app,
        session_manager=session_manager,
        command_prefix=command_prefix,
        cookie_secure=config.public_base_url.startswith("https://"),
    )
    app.router.add_route(
        "*", "/owlbot/{module_name}/{path:.*}", route_dispatcher.dispatch
    )
    app.router.add_route("*", "/owlbot/{module_name}", route_dispatcher.dispatch)

    client: TestClient[Any, Any] = await aiohttp_client(app)
    return client


@pytest.fixture
def registered_handlers(
    module_pkg: ModuleType | None,
    module_name: str,
    command_dispatcher: CommandDispatcher,
    event_dispatcher: EventDispatcher,
    route_dispatcher: RouteDispatcher,
    _loaded_modules: set[str],
) -> None:
    """Register all @on_command, @on_event, and @on_route handlers from module_pkg.

    Mirrors the registration half of
    :meth:`owlbot.module_loader.ModuleLoader.load_module`: the module's name
    is added to ``_loaded_modules`` after registration so dispatch routes
    commands correctly.

    Prefer ``module_lifecycle`` unless a test specifically needs to skip
    ``@on_setup`` / ``@on_teardown`` hooks (e.g., when the module's setup has
    side effects the test cannot tolerate).

    :raises pytest.UsageError: If ``module_pkg`` has not been overridden.
    """
    if module_pkg is None:
        raise pytest.UsageError(
            "registered_handlers requires module_pkg to be set; override the "
            "module_pkg fixture with the module package under test"
        )
    command_dispatcher.register_from_module(module_pkg, module_name)
    event_dispatcher.register_from_module(module_pkg, module_name)
    route_dispatcher.register_from_module(module_pkg, module_name)
    _loaded_modules.add(module_name)


@pytest_asyncio.fixture
async def module_lifecycle(
    module_pkg: ModuleType | None,
    module_context: ModuleContext,
    registered_handlers: None,  # transitively wires decorator registration
) -> AsyncIterator[None]:
    """Load ``module_pkg`` the way ``ModuleLoader`` does at runtime.

    Depends on ``registered_handlers`` to wire ``@on_command``, ``@on_event``,
    and ``@on_route`` decorators, runs every ``@on_setup`` hook, yields, then
    runs every ``@on_teardown`` hook on test exit.

    Recording-stub call logs are preserved across the yield, so tests can
    observe what ``@on_setup`` did. Tests that want a clean baseline for
    handler-initiated calls should clear ``owncast_client.calls`` (and
    ``admin_client.calls`` when the admin client is enabled) in a local
    fixture that depends on this one.

    :raises pytest.UsageError: If ``module_pkg`` has not been overridden.
    """
    if module_pkg is None:
        raise pytest.UsageError(
            "module_lifecycle requires module_pkg to be set; override the "
            "module_pkg fixture with the module package under test"
        )
    setup_hooks = ModuleLoader._collect_lifecycle_handlers(  # noqa: SLF001  # reuses the real loader's scan
        module_pkg, "_owlbot_setup"
    )
    for hook in setup_hooks:
        await hook(module_context)
    try:
        yield
    finally:
        teardown_hooks = ModuleLoader._collect_lifecycle_handlers(  # noqa: SLF001  # reuses the real loader's scan
            module_pkg, "_owlbot_teardown"
        )
        for hook in teardown_hooks:
            await hook(module_context)
