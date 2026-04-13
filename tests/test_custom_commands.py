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

"""Tests for the custom_commands module: types, repository, and manager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from owlbot.api.context import ModuleContext
from owlbot.api.storage import ModuleStorage, StorageError
from owlbot.builtin_modules.custom_commands.manager import (
    CommandManager,
    get_manager,
)
from owlbot.builtin_modules.custom_commands.repository import CommandRepository
from owlbot.builtin_modules.custom_commands.types import (
    NAME_RE,
    AliasAlreadyExistsError,
    AliasIsCanonicalNameError,
    CannotDeleteByAliasError,
    Command,
    CommandAlreadyExistsError,
    CommandError,
    CommandNotFoundError,
    InvalidNameError,
    NotCustomCommandError,
)


class TestCommandFromRow:
    """Command.from_row() database row conversion."""

    def test_builds_from_row(self) -> None:
        """from_row constructs a Command with correct field values."""
        row = {
            "name": "greet",
            "response": "Hello $(user)!",
            "use_count": 5,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "requires_moderator": 0,
            "cooldown": 10,
        }
        cmd = Command.from_row(row)  # type: ignore[arg-type]
        assert cmd.name == "greet"
        assert cmd.response == "Hello $(user)!"
        assert cmd.use_count == 5
        assert cmd.requires_moderator is False
        assert cmd.cooldown == 10
        assert cmd.aliases == frozenset()

    def test_builds_with_aliases(self) -> None:
        """from_row populates aliases and coerces requires_moderator to bool."""
        row = {
            "name": "greet",
            "response": "Hello!",
            "use_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "requires_moderator": 1,
            "cooldown": 0,
        }
        aliases = frozenset({"hi", "hey"})
        cmd = Command.from_row(row, aliases)  # type: ignore[arg-type]
        assert cmd.requires_moderator is True
        assert cmd.aliases == {"hi", "hey"}

    def test_frozen(self) -> None:
        """Command instances are immutable (frozen dataclass)."""
        row = {
            "name": "x",
            "response": "y",
            "use_count": 0,
            "created_at": "",
            "updated_at": "",
            "requires_moderator": 0,
            "cooldown": 0,
        }
        cmd = Command.from_row(row)  # type: ignore[arg-type]
        with pytest.raises(AttributeError):
            cmd.name = "z"  # type: ignore[misc]


class TestCommandErrors:
    """Domain error hierarchy and stored context."""

    def test_all_inherit_from_command_error(self) -> None:
        """All domain errors are subclasses of CommandError."""
        assert issubclass(CommandNotFoundError, CommandError)
        assert issubclass(InvalidNameError, CommandError)
        assert issubclass(CommandAlreadyExistsError, CommandError)
        assert issubclass(NotCustomCommandError, CommandError)
        assert issubclass(AliasAlreadyExistsError, CommandError)
        assert issubclass(AliasIsCanonicalNameError, CommandError)
        assert issubclass(CannotDeleteByAliasError, CommandError)

    def test_not_found_stores_identifier(self) -> None:
        """CommandNotFoundError stores the identifier and includes it in str."""
        err = CommandNotFoundError("greet")
        assert err.identifier == "greet"
        assert "greet" in str(err)

    def test_invalid_name_stores_name(self) -> None:
        """InvalidNameError stores the invalid name."""
        err = InvalidNameError("BAD NAME!")
        assert err.name == "BAD NAME!"

    def test_already_exists_stores_name(self) -> None:
        """CommandAlreadyExistsError stores the conflicting name."""
        err = CommandAlreadyExistsError("greet")
        assert err.name == "greet"

    def test_not_custom_stores_name(self) -> None:
        """NotCustomCommandError stores the built-in command name."""
        err = NotCustomCommandError("about")
        assert err.name == "about"

    def test_alias_already_exists_stores_context(self) -> None:
        """AliasAlreadyExistsError stores alias and owner."""
        err = AliasAlreadyExistsError("hi", "greet")
        assert err.alias == "hi"
        assert err.owner == "greet"

    def test_alias_is_canonical_stores_alias(self) -> None:
        """AliasIsCanonicalNameError stores the alias."""
        err = AliasIsCanonicalNameError("greet")
        assert err.alias == "greet"

    def test_cannot_delete_alias_stores_context(self) -> None:
        """CannotDeleteByAliasError stores alias and canonical name."""
        err = CannotDeleteByAliasError("hi", "greet")
        assert err.alias == "hi"
        assert err.canonical == "greet"


class TestNamePattern:
    """NAME_RE validates identifiers."""

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("greet", id="alpha"),
            pytest.param("hello_world", id="underscore"),
            pytest.param("cmd1", id="trailing_digit"),
            pytest.param("a", id="single_char"),
            pytest.param("test_123", id="mixed"),
        ],
    )
    def test_valid_names(self, name: str) -> None:
        """NAME_RE matches valid lowercase alphanumeric identifiers."""
        assert NAME_RE.match(name) is not None

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("Hello", id="uppercase"),
            pytest.param("has space", id="space"),
            pytest.param("special!", id="special_char"),
            pytest.param("with-dash", id="dash"),
            pytest.param("UPPER", id="all_upper"),
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        """NAME_RE rejects names with invalid characters or patterns."""
        assert NAME_RE.match(name) is None


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---- Repository fixtures ----


@pytest.fixture
async def cmd_storage() -> AsyncIterator[ModuleStorage]:
    """Yield an open in-memory ModuleStorage."""
    async with ModuleStorage(None, "custom_commands") as storage:
        yield storage


@pytest.fixture
async def repo(cmd_storage: ModuleStorage) -> CommandRepository:
    """CommandRepository backed by cmd_storage, with schema initialized."""
    r = CommandRepository(cmd_storage)
    await r.setup()
    return r


# ---- Repository command tests ----


class TestRepositoryCreate:
    """CommandRepository.create() inserts new commands."""

    async def test_create_returns_command(self, repo: CommandRepository) -> None:
        """create() returns a Command snapshot with correct fields."""
        cmd = await repo.create("greet", "Hello!", 5)
        assert cmd.name == "greet"
        assert cmd.response == "Hello!"
        assert cmd.use_count == 0
        assert cmd.requires_moderator is False
        assert cmd.cooldown == 5
        assert cmd.aliases == frozenset()
        assert cmd.created_at is not None
        assert cmd.updated_at is not None

    async def test_create_duplicate_raises(self, repo: CommandRepository) -> None:
        """create() raises on duplicate command name."""
        await repo.create("greet", "Hello!", 5)
        with pytest.raises(StorageError, match="UNIQUE constraint"):
            await repo.create("greet", "Hi!", 5)


class TestRepositoryGet:
    """CommandRepository.get() fetches by name or alias."""

    async def test_get_by_name(self, repo: CommandRepository) -> None:
        """get() retrieves a command by its canonical name."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.get("greet")
        assert cmd.name == "greet"
        assert cmd.response == "Hello!"

    async def test_get_not_found(self, repo: CommandRepository) -> None:
        """get() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError) as exc_info:
            await repo.get("nonexistent")
        assert exc_info.value.identifier == "nonexistent"


class TestRepositoryDelete:
    """CommandRepository.delete() removes commands."""

    async def test_delete_returns_command(self, repo: CommandRepository) -> None:
        """delete() returns the deleted command snapshot."""
        await repo.create("greet", "Hello!", 5)
        deleted = await repo.delete("greet")
        assert deleted.name == "greet"

    async def test_delete_removes_from_db(self, repo: CommandRepository) -> None:
        """delete() makes the command unreachable via get()."""
        await repo.create("greet", "Hello!", 5)
        await repo.delete("greet")
        with pytest.raises(CommandNotFoundError):
            await repo.get("greet")

    async def test_delete_not_found(self, repo: CommandRepository) -> None:
        """delete() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.delete("nonexistent")


class TestRepositoryUpdateResponse:
    """CommandRepository.update_response() changes a command's response."""

    async def test_update_response(self, repo: CommandRepository) -> None:
        """update_response() returns updated Command with new response."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_response("greet", "Hi there!")
        assert updated.response == "Hi there!"
        assert updated.name == "greet"

    async def test_update_response_changes_updated_at(
        self, repo: CommandRepository
    ) -> None:
        """update_response() bumps updated_at timestamp."""
        created = await repo.create("greet", "Hello!", 5)
        updated = await repo.update_response("greet", "Hi!")
        assert updated.updated_at != created.updated_at

    async def test_update_response_not_found(self, repo: CommandRepository) -> None:
        """update_response() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_response("nonexistent", "Hi!")


class TestRepositoryUpdateModeratorFlag:
    """CommandRepository.update_moderator_flag() changes the mod-only flag."""

    async def test_enable(self, repo: CommandRepository) -> None:
        """update_moderator_flag() sets requires_moderator to True."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_moderator_flag("greet", enabled=True)
        assert updated.requires_moderator is True
        assert updated.cooldown == 5

    async def test_disable(self, repo: CommandRepository) -> None:
        """update_moderator_flag() sets requires_moderator to False."""
        await repo.create("greet", "Hello!", 5)
        await repo.update_moderator_flag("greet", enabled=True)
        updated = await repo.update_moderator_flag("greet", enabled=False)
        assert updated.requires_moderator is False

    async def test_not_found(self, repo: CommandRepository) -> None:
        """update_moderator_flag() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_moderator_flag("nonexistent", enabled=True)


class TestRepositoryUpdateCooldown:
    """CommandRepository.update_cooldown() changes the cooldown duration."""

    async def test_update(self, repo: CommandRepository) -> None:
        """update_cooldown() sets the cooldown value."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_cooldown("greet", seconds=30)
        assert updated.cooldown == 30
        assert updated.requires_moderator is False

    async def test_not_found(self, repo: CommandRepository) -> None:
        """update_cooldown() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_cooldown("nonexistent", seconds=5)


class TestRepositoryResetUseCount:
    """CommandRepository.reset_use_count() zeroes the counter."""

    async def test_reset_use_count(self, repo: CommandRepository) -> None:
        """reset_use_count() sets use_count back to 0."""
        await repo.create("greet", "Hello!", 5)
        await repo.increment_use_count("greet")
        await repo.increment_use_count("greet")
        reset = await repo.reset_use_count("greet")
        assert reset.use_count == 0

    async def test_reset_not_found(self, repo: CommandRepository) -> None:
        """reset_use_count() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.reset_use_count("nonexistent")


class TestRepositoryIncrementUseCount:
    """CommandRepository.increment_use_count() bumps the counter."""

    async def test_increment(self, repo: CommandRepository) -> None:
        """increment_use_count() increases use_count by 1 each call."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.increment_use_count("greet")
        assert cmd.use_count == 1
        cmd = await repo.increment_use_count("greet")
        assert cmd.use_count == 2

    async def test_increment_not_found(self, repo: CommandRepository) -> None:
        """increment_use_count() raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.increment_use_count("nonexistent")


class TestRepositoryListAll:
    """CommandRepository.list_all() returns all commands."""

    async def test_list_all(self, repo: CommandRepository) -> None:
        """list_all() returns commands sorted by name."""
        await repo.create("bravo", "B", 5)
        await repo.create("alpha", "A", 5)
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "bravo"

    async def test_list_all_empty(self, repo: CommandRepository) -> None:
        """list_all() returns empty list when no commands exist."""
        assert await repo.list_all() == []


# ---- Repository alias tests ----


class TestRepositoryGetByAlias:
    """CommandRepository.get() resolves aliases."""

    async def test_get_by_alias(self, repo: CommandRepository) -> None:
        """get() resolves an alias to its canonical command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        cmd = await repo.get("hi")
        assert cmd.name == "greet"
        assert "hi" in cmd.aliases


class TestRepositoryAddAlias:
    """CommandRepository.add_alias() adds aliases."""

    async def test_add_alias(self, repo: CommandRepository) -> None:
        """add_alias() adds an alias and returns refreshed command."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.add_alias("greet", "hi")
        assert "hi" in cmd.aliases

    async def test_add_multiple_aliases(self, repo: CommandRepository) -> None:
        """add_alias() accumulates multiple aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        cmd = await repo.add_alias("greet", "hey")
        assert cmd.aliases == frozenset({"hi", "hey"})

    async def test_add_alias_duplicate(self, repo: CommandRepository) -> None:
        """add_alias() raises AliasAlreadyExistsError for duplicate aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        with pytest.raises(AliasAlreadyExistsError) as exc_info:
            await repo.add_alias("greet", "hi")
        assert exc_info.value.alias == "hi"
        assert exc_info.value.owner == "greet"


class TestRepositoryRemoveAlias:
    """CommandRepository.remove_alias() removes aliases."""

    async def test_remove_alias(self, repo: CommandRepository) -> None:
        """remove_alias() removes the alias and returns refreshed command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        alias, cmd = await repo.remove_alias("hi")
        assert alias == "hi"
        assert "hi" not in cmd.aliases

    async def test_remove_alias_not_found(self, repo: CommandRepository) -> None:
        """remove_alias() raises CommandNotFoundError for missing aliases."""
        with pytest.raises(CommandNotFoundError):
            await repo.remove_alias("nonexistent")


class TestRepositoryDeleteCascade:
    """Deleting a command cascades to its aliases."""

    async def test_delete_cascades_aliases(self, repo: CommandRepository) -> None:
        """delete() removes associated aliases via CASCADE."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await repo.delete("greet")
        with pytest.raises(CommandNotFoundError):
            await repo.get("hi")


class TestRepositoryListAllWithAliases:
    """CommandRepository.list_all() includes aliases."""

    async def test_list_all_includes_aliases(self, repo: CommandRepository) -> None:
        """list_all() populates alias sets for each command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await repo.add_alias("greet", "hey")
        result = await repo.list_all()
        assert len(result) == 1
        assert result[0].aliases == frozenset({"hi", "hey"})


# ---- Repository counter tests ----


class TestRepositoryGetCounter:
    """CommandRepository.get_counter() reads counter values."""

    async def test_get_counter_missing(self, repo: CommandRepository) -> None:
        """get_counter() returns 0 for nonexistent counters."""
        assert await repo.get_counter("deaths") == 0

    async def test_get_counter_existing(self, repo: CommandRepository) -> None:
        """get_counter() returns the stored value."""
        await repo.set_counter("deaths", 10)
        assert await repo.get_counter("deaths") == 10


class TestRepositorySetCounter:
    """CommandRepository.set_counter() sets absolute values."""

    async def test_set_counter_new(self, repo: CommandRepository) -> None:
        """set_counter() inserts a new counter with the given value."""
        result = await repo.set_counter("deaths", 42)
        assert result == 42
        assert await repo.get_counter("deaths") == 42

    async def test_set_counter_overwrite(self, repo: CommandRepository) -> None:
        """set_counter() overwrites an existing counter."""
        await repo.set_counter("deaths", 10)
        result = await repo.set_counter("deaths", 99)
        assert result == 99


class TestRepositoryUpdateCounter:
    """CommandRepository.update_counter() applies relative deltas."""

    async def test_update_counter_new(self, repo: CommandRepository) -> None:
        """update_counter() creates a counter if it does not exist."""
        result = await repo.update_counter("deaths", 5)
        assert result == 5

    async def test_update_counter_increment(self, repo: CommandRepository) -> None:
        """update_counter() adds a positive delta to existing value."""
        await repo.set_counter("deaths", 10)
        result = await repo.update_counter("deaths", 3)
        assert result == 13

    async def test_update_counter_decrement(self, repo: CommandRepository) -> None:
        """update_counter() subtracts a negative delta from existing value."""
        await repo.set_counter("deaths", 10)
        result = await repo.update_counter("deaths", -3)
        assert result == 7


# ---- Manager stubs and fixtures ----


class _StubModuleCommands:
    """Minimal ModuleCommands stand-in that tracks registrations."""

    def __init__(self, prefix: str = "!") -> None:
        self.prefix = prefix
        self._registered: dict[str, dict[str, Any]] = {}
        self._all_triggers: set[str] = set()
        self._external_triggers: set[str] = set()

    def seed_external(self, name: str) -> None:
        """Pre-register a trigger as belonging to another module."""
        self._external_triggers.add(name)

    def register(
        self,
        name: str,
        handler: Any,
        *,
        aliases: list[str] | tuple[str, ...] | None = None,
        requires_authenticated: bool = False,
        requires_moderator: bool = False,
        cooldown: int = 0,
    ) -> None:
        all_new = {name} | set(aliases or [])
        for trigger in all_new:
            if trigger in self._all_triggers or trigger in self._external_triggers:
                raise ValueError(
                    f"Command trigger '{trigger}' conflicts with existing command"
                )
        self._registered[name] = {
            "handler": handler,
            "aliases": set(aliases or []),
            "requires_authenticated": requires_authenticated,
            "requires_moderator": requires_moderator,
            "cooldown": cooldown,
        }
        self._all_triggers.update(all_new)

    def unregister(self, name: str) -> bool:
        if name not in self._registered:
            return False
        info = self._registered.pop(name)
        self._all_triggers.discard(name)
        for a in info["aliases"]:
            self._all_triggers.discard(a)
        return True

    def exists(self, trigger: str) -> bool:
        return trigger in self._all_triggers or trigger in self._external_triggers


class _StubConfig:
    """Minimal ModuleConfig stand-in."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values: dict[str, Any] = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def register_defaults(self, defaults: dict[str, Any]) -> None:
        for key, value in defaults.items():
            self._values.setdefault(key, value)


@pytest.fixture
def stub_commands() -> _StubModuleCommands:
    """Return a fresh stub command registry."""
    return _StubModuleCommands()


@pytest.fixture
def cmd_ctx(
    cmd_storage: ModuleStorage, stub_commands: _StubModuleCommands
) -> ModuleContext:
    """ModuleContext with real storage and stub commands/config."""
    return ModuleContext(
        module_name="custom_commands",
        config=_StubConfig({"default_cooldown": 5, "max_nesting_depth": 4}),  # type: ignore[arg-type]
        owncast_client=None,  # type: ignore[arg-type]
        storage=cmd_storage,
        commands=stub_commands,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        routes=None,  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        templates=None,  # type: ignore[arg-type]
        admin_client=None,
    )


@pytest.fixture
def manager(cmd_ctx: ModuleContext, repo: CommandRepository) -> CommandManager:
    """CommandManager backed by real repo and stub commands."""
    return CommandManager(cmd_ctx, repo)


# ---- Manager tests ----


class TestManagerLoadAll:
    """CommandManager.load_all() loads commands into the registry."""

    async def test_load_all(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """load_all registers all commands from the database."""
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await manager.load_all()
        assert stub_commands.exists("greet")
        assert stub_commands.exists("bye")

    async def test_load_all_skips_conflicts(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """load_all skips commands that conflict with existing triggers."""
        stub_commands.seed_external("greet")
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await manager.load_all()
        assert not stub_commands._registered.get("greet")
        assert stub_commands.exists("bye")

    async def test_load_all_with_aliases(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """load_all registers commands together with their aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await manager.load_all()
        assert stub_commands.exists("greet")
        assert stub_commands.exists("hi")

    async def test_load_all_empty(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """load_all does nothing when the database is empty."""
        await manager.load_all()
        assert len(stub_commands._registered) == 0


class TestManagerCreateCommand:
    """CommandManager.create_command() creates and registers commands."""

    async def test_create_command(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """create_command creates and registers a command."""
        cmd = await manager.create_command("greet", "Hello!")
        assert cmd.name == "greet"
        assert cmd.response == "Hello!"
        assert cmd.cooldown == 5  # default_cooldown from config
        assert stub_commands.exists("greet")

    async def test_create_rejects_invalid_name(self, manager: CommandManager) -> None:
        """create_command raises InvalidNameError for bad names."""
        with pytest.raises(InvalidNameError):
            await manager.create_command("BAD!", "Hello!")

    async def test_create_rejects_conflict(
        self,
        manager: CommandManager,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """create_command raises CommandAlreadyExistsError on conflict."""
        stub_commands.seed_external("about")
        with pytest.raises(CommandAlreadyExistsError) as exc_info:
            await manager.create_command("about", "Info")
        assert exc_info.value.name == "about"

    async def test_create_rollback_on_db_failure(
        self,
        cmd_ctx: ModuleContext,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """If DB insert fails, the command is not registered."""
        # Create a manager with a repo whose storage is closed.
        closed_storage = ModuleStorage(None, "closed")
        bad_repo = CommandRepository(closed_storage)
        mgr = CommandManager(cmd_ctx, bad_repo)
        with pytest.raises((StorageError, AttributeError)):
            await mgr.create_command("greet", "Hello!")
        assert not stub_commands.exists("greet")


class TestManagerEditCommand:
    """CommandManager.edit_command() updates a command's response."""

    async def test_edit_command(self, manager: CommandManager) -> None:
        """edit_command updates the response text."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.edit_command("greet", "Hi there!")
        assert updated.response == "Hi there!"

    async def test_edit_resolves_alias(self, manager: CommandManager) -> None:
        """edit_command resolves an alias to the canonical command."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        updated = await manager.edit_command("hi", "Hi there!")
        assert updated.name == "greet"
        assert updated.response == "Hi there!"

    async def test_edit_not_found(self, manager: CommandManager) -> None:
        """edit_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.edit_command("nonexistent", "Hi!")

    async def test_edit_not_custom(
        self,
        manager: CommandManager,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """edit_command raises NotCustomCommandError for built-in commands."""
        stub_commands.seed_external("about")
        with pytest.raises(NotCustomCommandError):
            await manager.edit_command("about", "Hacked!")


class TestManagerDeleteCommand:
    """CommandManager.delete_command() deletes and unregisters commands."""

    async def test_delete_command(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """delete_command removes the command and unregisters its trigger."""
        await manager.create_command("greet", "Hello!")
        deleted = await manager.delete_command("greet")
        assert deleted.name == "greet"
        assert not stub_commands.exists("greet")

    async def test_delete_rejects_alias(self, manager: CommandManager) -> None:
        """delete_command raises CannotDeleteByAliasError for aliases."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        with pytest.raises(CannotDeleteByAliasError) as exc_info:
            await manager.delete_command("hi")
        assert exc_info.value.alias == "hi"
        assert exc_info.value.canonical == "greet"

    async def test_delete_not_found(self, manager: CommandManager) -> None:
        """delete_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.delete_command("nonexistent")


class TestManagerSetModOnly:
    """CommandManager.set_mod_only() updates moderator access."""

    async def test_set_mod_only_on(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """set_mod_only enables moderator-only access."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_mod_only("greet", enabled=True)
        assert updated.requires_moderator is True
        assert stub_commands._registered["greet"]["requires_moderator"] is True

    async def test_set_mod_only_off(self, manager: CommandManager) -> None:
        """set_mod_only disables moderator-only access."""
        await manager.create_command("greet", "Hello!")
        await manager.set_mod_only("greet", enabled=True)
        updated = await manager.set_mod_only("greet", enabled=False)
        assert updated.requires_moderator is False


class TestManagerSetCooldown:
    """CommandManager.set_cooldown() updates cooldown."""

    async def test_set_cooldown(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """set_cooldown updates the cooldown and re-registers the command."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_cooldown("greet", 30)
        assert updated.cooldown == 30
        assert stub_commands._registered["greet"]["cooldown"] == 30

    async def test_set_cooldown_zero(self, manager: CommandManager) -> None:
        """set_cooldown accepts zero to disable cooldown."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_cooldown("greet", 0)
        assert updated.cooldown == 0


class TestManagerResetUseCount:
    """CommandManager.reset_use_count() zeroes the counter."""

    async def test_reset_use_count(self, manager: CommandManager) -> None:
        """reset_use_count sets the counter back to zero."""
        await manager.create_command("greet", "$(count)")
        await manager.execute_command("greet", [], "tester")
        reset = await manager.reset_use_count("greet")
        assert reset.use_count == 0


class TestManagerAddAlias:
    """CommandManager.add_alias() adds and re-registers aliases."""

    async def test_add_alias(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """add_alias registers a new alias for the command."""
        await manager.create_command("greet", "Hello!")
        cmd = await manager.add_alias("greet", "hi")
        assert "hi" in cmd.aliases
        assert stub_commands.exists("hi")

    async def test_add_alias_rejects_canonical(self, manager: CommandManager) -> None:
        """add_alias raises AliasIsCanonicalNameError for the canonical name."""
        await manager.create_command("greet", "Hello!")
        with pytest.raises(AliasIsCanonicalNameError):
            await manager.add_alias("greet", "greet")

    async def test_add_alias_rejects_conflict(
        self,
        manager: CommandManager,
        stub_commands: _StubModuleCommands,
    ) -> None:
        """add_alias raises CommandAlreadyExistsError on trigger conflict."""
        stub_commands.seed_external("about")
        await manager.create_command("greet", "Hello!")
        with pytest.raises(CommandAlreadyExistsError):
            await manager.add_alias("greet", "about")

    async def test_add_alias_invalid_name(self, manager: CommandManager) -> None:
        """add_alias raises InvalidNameError for bad alias names."""
        await manager.create_command("greet", "Hello!")
        with pytest.raises(InvalidNameError):
            await manager.add_alias("greet", "BAD!")


class TestManagerRemoveAlias:
    """CommandManager.remove_alias() removes and re-registers aliases."""

    async def test_remove_alias(
        self, manager: CommandManager, stub_commands: _StubModuleCommands
    ) -> None:
        """remove_alias removes the alias and keeps the canonical command."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        cmd = await manager.remove_alias("hi")
        assert "hi" not in cmd.aliases
        assert not stub_commands.exists("hi")
        assert stub_commands.exists("greet")

    async def test_remove_alias_not_found(self, manager: CommandManager) -> None:
        """remove_alias raises CommandNotFoundError for missing aliases."""
        with pytest.raises(CommandNotFoundError):
            await manager.remove_alias("nonexistent")


class TestManagerExecuteCommand:
    """CommandManager.execute_command() increments count and processes response."""

    async def test_execute_plain(self, manager: CommandManager) -> None:
        """execute_command returns the plain response text."""
        await manager.create_command("greet", "Hello!")
        response = await manager.execute_command("greet", [], "tester")
        assert response == "Hello!"

    async def test_execute_increments_use_count(self, manager: CommandManager) -> None:
        """execute_command increments use_count on each call."""
        await manager.create_command("greet", "Count: $(count)")
        r1 = await manager.execute_command("greet", [], "tester")
        assert r1 == "Count: 1"
        r2 = await manager.execute_command("greet", [], "tester")
        assert r2 == "Count: 2"

    async def test_execute_user_placeholder(self, manager: CommandManager) -> None:
        """execute_command substitutes the $(user) placeholder."""
        await manager.create_command("greet", "Hello $(user)!")
        response = await manager.execute_command("greet", [], "Alice")
        assert response == "Hello Alice!"

    async def test_execute_not_found(self, manager: CommandManager) -> None:
        """execute_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.execute_command("nonexistent", [], "tester")


class TestManagerEditCounter:
    """CommandManager.edit_counter() sets/adjusts named counters."""

    async def test_edit_counter_absolute(self, manager: CommandManager) -> None:
        """edit_counter sets an absolute counter value."""
        result = await manager.edit_counter("deaths", value=42, relative=False)
        assert result == 42

    async def test_edit_counter_relative_increment(
        self, manager: CommandManager
    ) -> None:
        """edit_counter increments a counter with a positive delta."""
        await manager.edit_counter("deaths", value=10, relative=False)
        result = await manager.edit_counter("deaths", value=5, relative=True)
        assert result == 15

    async def test_edit_counter_relative_decrement(
        self, manager: CommandManager
    ) -> None:
        """edit_counter decrements a counter with a negative delta."""
        await manager.edit_counter("deaths", value=10, relative=False)
        result = await manager.edit_counter("deaths", value=-3, relative=True)
        assert result == 7

    async def test_edit_counter_invalid_name(self, manager: CommandManager) -> None:
        """edit_counter raises InvalidNameError for bad counter names."""
        with pytest.raises(InvalidNameError):
            await manager.edit_counter("BAD!", value=5, relative=False)


class TestManagerCounterAccessor:
    """CommandManager as CounterAccessor protocol for placeholders."""

    async def test_get_counter(self, manager: CommandManager) -> None:
        """Return 0 for missing counter, correct value for existing."""
        assert await manager.get_counter("deaths") == 0
        await manager.set_counter("deaths", 10)
        assert await manager.get_counter("deaths") == 10

    async def test_set_counter(self, manager: CommandManager) -> None:
        """Set a counter to an absolute value."""
        result = await manager.set_counter("deaths", 42)
        assert result == 42

    async def test_adjust_counter(self, manager: CommandManager) -> None:
        """Adjust a counter by a relative delta."""
        await manager.set_counter("deaths", 10)
        result = await manager.adjust_counter("deaths", 5)
        assert result == 15

    async def test_execute_with_named_counter(self, manager: CommandManager) -> None:
        """Execute a command that uses a named counter placeholder."""
        await manager.create_command("deaths", "Deaths: $(count deaths)")
        r1 = await manager.execute_command("deaths", [], "tester")
        assert r1 == "Deaths: 1"
        r2 = await manager.execute_command("deaths", [], "tester")
        assert r2 == "Deaths: 2"

    async def test_execute_with_getcount(self, manager: CommandManager) -> None:
        """Execute a command that reads a named counter."""
        await manager.create_command("show", "Deaths: $(getcount deaths)")
        await manager.set_counter("deaths", 42)
        response = await manager.execute_command("show", [], "tester")
        assert response == "Deaths: 42"


class TestGetManager:
    """get_manager() helper."""

    def test_returns_manager(
        self, cmd_ctx: ModuleContext, repo: CommandRepository
    ) -> None:
        """get_manager returns the manager stored in context state."""
        mgr = CommandManager(cmd_ctx, repo)
        cmd_ctx.state["manager"] = mgr
        assert get_manager(cmd_ctx) is mgr

    def test_raises_if_not_initialized(self, cmd_ctx: ModuleContext) -> None:
        """get_manager raises RuntimeError when no manager is stored."""
        with pytest.raises(RuntimeError, match="CommandManager is not initialized"):
            get_manager(cmd_ctx)
