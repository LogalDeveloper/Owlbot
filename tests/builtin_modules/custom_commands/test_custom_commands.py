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

"""Tests for the custom_commands module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from owlbot.api.event_types import EventType
from owlbot.api.storage import StorageError
from owlbot.builtin_modules import custom_commands as custom_commands_pkg
from owlbot.builtin_modules.custom_commands import setup, teardown
from owlbot.builtin_modules.custom_commands.manager import CommandManager, get_manager
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
    InactiveCommandError,
    InvalidNameError,
    NotCustomCommandError,
)
from owlbot.testing import make_chat_event, make_user

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import ModuleType
    from typing import Any

    from aiohttp.test_utils import TestClient

    from owlbot.api.context import ModuleContext
    from owlbot.api.storage import ModuleStorage
    from owlbot.registries.commands import CommandDispatcher
    from owlbot.registries.events import EventDispatcher
    from owlbot.testing import RecordingOwncastClient


def _send_bodies(client: RecordingOwncastClient) -> list[str]:
    """Extract send_message body strings from recorded calls, in order."""
    return [c.kwargs["body"] for c in client.calls if c.name == "send_message"]


def _register_foreign_command(
    dispatcher: CommandDispatcher, name: str, *, module_name: str = "other_module"
) -> None:
    """Register ``name`` under a foreign module to simulate an unresolved conflict."""

    async def handler(ctx: object) -> None:
        pass

    dispatcher.register(name=name, handler=handler, module_name=module_name)


@pytest.fixture
def module_pkg() -> ModuleType:
    """Return the custom_commands package so the plugin auto-wires fixtures."""
    return custom_commands_pkg


@pytest.fixture
async def repo(storage: ModuleStorage) -> CommandRepository:
    """CommandRepository backed by storage, with schema initialized."""
    repo = CommandRepository(storage)
    await repo.setup()
    return repo


@pytest.fixture
def manager(module_context: ModuleContext, repo: CommandRepository) -> CommandManager:
    """CommandManager built directly for unit tests; does not run @on_setup."""
    return CommandManager(module_context, repo)


class TestTypes:
    """Command dataclass, NAME_RE validation, and domain error hierarchy."""

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
        """Command instances are immutable."""
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

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("greet", id="alpha"),
            pytest.param("hello_world", id="underscore"),
            pytest.param("cmd1", id="trailing-digit"),
            pytest.param("a", id="single-char"),
            pytest.param("test_123", id="mixed"),
        ],
    )
    def test_name_re_valid(self, name: str) -> None:
        """NAME_RE matches valid lowercase alphanumeric identifiers."""
        assert NAME_RE.match(name) is not None

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("Hello", id="uppercase"),
            pytest.param("has space", id="space"),
            pytest.param("special!", id="special-char"),
            pytest.param("with-dash", id="dash"),
            pytest.param("UPPER", id="all-upper"),
        ],
    )
    def test_name_re_invalid(self, name: str) -> None:
        """NAME_RE rejects names with invalid characters."""
        assert NAME_RE.match(name) is None

    @pytest.mark.parametrize(
        "error_cls",
        [
            pytest.param(CommandNotFoundError, id="not-found"),
            pytest.param(InvalidNameError, id="invalid-name"),
            pytest.param(CommandAlreadyExistsError, id="already-exists"),
            pytest.param(NotCustomCommandError, id="not-custom"),
            pytest.param(AliasAlreadyExistsError, id="alias-already-exists"),
            pytest.param(AliasIsCanonicalNameError, id="alias-is-canonical"),
            pytest.param(CannotDeleteByAliasError, id="cannot-delete-by-alias"),
            pytest.param(InactiveCommandError, id="inactive-command"),
        ],
    )
    def test_error_subclasses_command_error(self, error_cls: type[Exception]) -> None:
        """Every domain error subclasses CommandError."""
        assert issubclass(error_cls, CommandError)

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

    def test_inactive_command_error_stores_name(self) -> None:
        """InactiveCommandError stores the command name."""
        err = InactiveCommandError("foo")
        assert err.name == "foo"
        assert isinstance(err, CommandError)


class TestRepository:
    """CommandRepository CRUD, aliases, and counters against real storage."""

    async def test_create_returns_command(self, repo: CommandRepository) -> None:
        """Create returns a populated Command snapshot."""
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
        """Create raises on duplicate command name."""
        await repo.create("greet", "Hello!", 5)
        with pytest.raises(StorageError, match="UNIQUE constraint"):
            await repo.create("greet", "Hi!", 5)

    async def test_get_by_name(self, repo: CommandRepository) -> None:
        """Get retrieves a command by its canonical name."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.get("greet")
        assert cmd.name == "greet"
        assert cmd.response == "Hello!"

    async def test_get_not_found(self, repo: CommandRepository) -> None:
        """Get raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError) as exc_info:
            await repo.get("nonexistent")
        assert exc_info.value.identifier == "nonexistent"

    async def test_delete_returns_command(self, repo: CommandRepository) -> None:
        """Delete returns the deleted command snapshot."""
        await repo.create("greet", "Hello!", 5)
        deleted = await repo.delete("greet")
        assert deleted.name == "greet"
        assert deleted.response == "Hello!"

    async def test_delete_removes_from_db(self, repo: CommandRepository) -> None:
        """Delete makes the command unreachable via get."""
        await repo.create("greet", "Hello!", 5)
        await repo.delete("greet")
        with pytest.raises(CommandNotFoundError):
            await repo.get("greet")

    async def test_delete_not_found(self, repo: CommandRepository) -> None:
        """Delete raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.delete("nonexistent")

    async def test_update_response(self, repo: CommandRepository) -> None:
        """update_response returns an updated Command with the new text."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_response("greet", "Hi there!")
        assert updated.response == "Hi there!"
        assert updated.name == "greet"

    async def test_update_response_bumps_updated_at(
        self, repo: CommandRepository
    ) -> None:
        """update_response advances updated_at."""
        created = await repo.create("greet", "Hello!", 5)
        updated = await repo.update_response("greet", "Hi!")
        assert updated.updated_at != created.updated_at

    async def test_update_response_not_found(self, repo: CommandRepository) -> None:
        """update_response raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_response("nonexistent", "Hi!")

    async def test_update_moderator_flag_enable(self, repo: CommandRepository) -> None:
        """update_moderator_flag sets requires_moderator to True."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_moderator_flag("greet", enabled=True)
        assert updated.requires_moderator is True
        assert updated.cooldown == 5

    async def test_update_moderator_flag_disable(self, repo: CommandRepository) -> None:
        """update_moderator_flag sets requires_moderator to False."""
        await repo.create("greet", "Hello!", 5)
        await repo.update_moderator_flag("greet", enabled=True)
        updated = await repo.update_moderator_flag("greet", enabled=False)
        assert updated.requires_moderator is False

    async def test_update_moderator_flag_not_found(
        self, repo: CommandRepository
    ) -> None:
        """update_moderator_flag raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_moderator_flag("nonexistent", enabled=True)

    async def test_update_cooldown(self, repo: CommandRepository) -> None:
        """update_cooldown sets the cooldown value."""
        await repo.create("greet", "Hello!", 5)
        updated = await repo.update_cooldown("greet", seconds=30)
        assert updated.cooldown == 30
        assert updated.requires_moderator is False

    async def test_update_cooldown_not_found(self, repo: CommandRepository) -> None:
        """update_cooldown raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.update_cooldown("nonexistent", seconds=5)

    async def test_reset_use_count(self, repo: CommandRepository) -> None:
        """reset_use_count sets use_count back to 0."""
        await repo.create("greet", "Hello!", 5)
        await repo.increment_use_count("greet")
        await repo.increment_use_count("greet")
        reset = await repo.reset_use_count("greet")
        assert reset.use_count == 0

    async def test_reset_use_count_not_found(self, repo: CommandRepository) -> None:
        """reset_use_count raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.reset_use_count("nonexistent")

    async def test_increment_use_count(self, repo: CommandRepository) -> None:
        """increment_use_count increases by 1 each call."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.increment_use_count("greet")
        assert cmd.use_count == 1
        cmd = await repo.increment_use_count("greet")
        assert cmd.use_count == 2

    async def test_increment_use_count_not_found(self, repo: CommandRepository) -> None:
        """increment_use_count raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await repo.increment_use_count("nonexistent")

    async def test_list_all(self, repo: CommandRepository) -> None:
        """list_all returns commands sorted by name."""
        await repo.create("bravo", "B", 5)
        await repo.create("alpha", "A", 5)
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "bravo"

    async def test_list_all_empty(self, repo: CommandRepository) -> None:
        """list_all returns empty list when no commands exist."""
        assert await repo.list_all() == []

    async def test_get_by_alias(self, repo: CommandRepository) -> None:
        """Get resolves an alias to its canonical command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        cmd = await repo.get("hi")
        assert cmd.name == "greet"
        assert "hi" in cmd.aliases

    async def test_add_alias(self, repo: CommandRepository) -> None:
        """add_alias adds an alias and returns refreshed command."""
        await repo.create("greet", "Hello!", 5)
        cmd = await repo.add_alias("greet", "hi")
        assert "hi" in cmd.aliases

    async def test_add_multiple_aliases(self, repo: CommandRepository) -> None:
        """add_alias accumulates multiple aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        cmd = await repo.add_alias("greet", "hey")
        assert cmd.aliases == frozenset({"hi", "hey"})

    async def test_add_alias_duplicate(self, repo: CommandRepository) -> None:
        """add_alias raises AliasAlreadyExistsError on duplicate alias."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        with pytest.raises(AliasAlreadyExistsError) as exc_info:
            await repo.add_alias("greet", "hi")
        assert exc_info.value.alias == "hi"
        assert exc_info.value.owner == "greet"

    async def test_remove_alias(self, repo: CommandRepository) -> None:
        """remove_alias removes the alias and returns refreshed command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        alias, cmd = await repo.remove_alias("hi")
        assert alias == "hi"
        assert cmd.name == "greet"
        assert "hi" not in cmd.aliases

    async def test_remove_alias_not_found(self, repo: CommandRepository) -> None:
        """remove_alias raises CommandNotFoundError for missing aliases."""
        with pytest.raises(CommandNotFoundError):
            await repo.remove_alias("nonexistent")

    async def test_delete_cascades_aliases(self, repo: CommandRepository) -> None:
        """Deleting a command cascades to its aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await repo.delete("greet")
        with pytest.raises(CommandNotFoundError):
            await repo.get("hi")

    async def test_list_all_includes_aliases(self, repo: CommandRepository) -> None:
        """list_all populates alias sets for each command."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await repo.add_alias("greet", "hey")
        result = await repo.list_all()
        assert len(result) == 1
        assert result[0].aliases == frozenset({"hi", "hey"})

    async def test_get_counter_missing(self, repo: CommandRepository) -> None:
        """get_counter returns 0 for nonexistent counters."""
        assert await repo.get_counter("deaths") == 0

    async def test_get_counter_existing(self, repo: CommandRepository) -> None:
        """get_counter returns the stored value."""
        await repo.set_counter("deaths", 10)
        assert await repo.get_counter("deaths") == 10

    async def test_set_counter_new(self, repo: CommandRepository) -> None:
        """set_counter inserts a new counter."""
        result = await repo.set_counter("deaths", 42)
        assert result == 42
        assert await repo.get_counter("deaths") == 42

    async def test_set_counter_overwrite(self, repo: CommandRepository) -> None:
        """set_counter overwrites an existing counter."""
        await repo.set_counter("deaths", 10)
        result = await repo.set_counter("deaths", 99)
        assert result == 99

    async def test_update_counter_new(self, repo: CommandRepository) -> None:
        """update_counter creates a counter if it does not exist."""
        result = await repo.update_counter("deaths", 5)
        assert result == 5

    async def test_update_counter_increment(self, repo: CommandRepository) -> None:
        """update_counter adds a positive delta to existing value."""
        await repo.set_counter("deaths", 10)
        result = await repo.update_counter("deaths", 3)
        assert result == 13

    async def test_update_counter_decrement(self, repo: CommandRepository) -> None:
        """update_counter subtracts a negative delta from existing value."""
        await repo.set_counter("deaths", 10)
        result = await repo.update_counter("deaths", -3)
        assert result == 7


class TestManager:
    """CommandManager CRUD, aliases, counters, execute, and lifecycle helpers."""

    async def test_load_all_registers_all(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
    ) -> None:
        """Load_all registers every command in the DB."""
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await manager.load_all()
        assert module_context.commands.exists("greet")
        assert module_context.commands.exists("bye")

    async def test_load_all_skips_conflicts(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
        module_context: ModuleContext,
    ) -> None:
        """Load_all skips commands that conflict with existing triggers."""

        # Pre-register "greet" on a different module so load_all hits its
        # conflict-skip branch when it tries to register the custom command.
        async def handler(ctx: object) -> None:
            pass

        command_dispatcher.register(
            name="greet",
            handler=handler,
            module_name="other_module",
        )
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await manager.load_all()
        greet = command_dispatcher.get("greet")
        assert greet is not None
        assert greet.module_name == "other_module"
        assert module_context.commands.exists("bye")

    async def test_load_all_tracks_inactive_on_conflict(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
        module_context: ModuleContext,
    ) -> None:
        """Load_all leaves conflicting commands unowned in the registry."""
        _register_foreign_command(command_dispatcher, "greet")
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await manager.load_all()
        assert module_context.commands.get("greet") is None
        assert module_context.commands.get("bye") is not None

    async def test_load_all_no_inactive_when_clean(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
    ) -> None:
        """Load_all registers every command when no conflicts exist."""
        await repo.create("greet", "Hello!", 5)
        await manager.load_all()
        assert module_context.commands.get("greet") is not None

    async def test_load_all_registers_aliases(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
    ) -> None:
        """Load_all registers commands together with their aliases."""
        await repo.create("greet", "Hello!", 5)
        await repo.add_alias("greet", "hi")
        await manager.load_all()
        assert module_context.commands.exists("greet")
        assert module_context.commands.exists("hi")

    async def test_load_all_empty(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Load_all does nothing when the DB is empty."""
        await manager.load_all()
        assert module_context.commands.module_commands == {}

    @pytest.mark.parametrize(
        "invoke",
        [
            pytest.param(lambda m: m.edit_command("greet", "New!"), id="edit_command"),
            pytest.param(
                lambda m: m.set_mod_only("greet", enabled=True), id="set_mod_only"
            ),
            pytest.param(lambda m: m.set_cooldown("greet", 30), id="set_cooldown"),
            pytest.param(lambda m: m.reset_use_count("greet"), id="reset_use_count"),
            pytest.param(lambda m: m.add_alias("greet", "hi"), id="add_alias"),
        ],
    )
    async def test_write_raises_for_inactive(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
        invoke: Callable[[CommandManager], Awaitable[object]],
    ) -> None:
        """Writes raise InactiveCommandError when the command is inactive."""
        await repo.create("greet", "Hi!", 5)
        _register_foreign_command(command_dispatcher, "greet")
        with pytest.raises(InactiveCommandError) as exc_info:
            await invoke(manager)
        assert exc_info.value.name == "greet"

    async def test_edit_command_resolves_alias_then_checks_inactive(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
    ) -> None:
        """Writes reject inactive commands addressed through an alias."""
        await repo.create("greet", "Hi!", 5)
        await repo.add_alias("greet", "hi")
        _register_foreign_command(command_dispatcher, "greet")
        with pytest.raises(InactiveCommandError) as exc_info:
            await manager.edit_command("hi", "New!")
        assert exc_info.value.name == "greet"

    async def test_create_command(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Create_command creates and registers a command."""
        cmd = await manager.create_command("greet", "Hello!")
        assert cmd.name == "greet"
        assert cmd.response == "Hello!"
        assert cmd.cooldown == 5
        assert module_context.commands.exists("greet")

    async def test_create_rejects_invalid_name(self, manager: CommandManager) -> None:
        """Create_command raises InvalidNameError for bad names."""
        with pytest.raises(InvalidNameError):
            await manager.create_command("BAD!", "Hello!")

    async def test_create_rejects_conflict(
        self,
        manager: CommandManager,
    ) -> None:
        """Create_command raises CommandAlreadyExistsError on conflict."""
        with pytest.raises(CommandAlreadyExistsError) as exc_info:
            await manager.create_command("about", "Info")
        assert exc_info.value.name == "about"

    async def test_create_rollback_on_db_failure(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If repo.create fails, the command is not registered in the dispatcher."""

        async def boom(*_: object, **__: object) -> Command:
            raise StorageError("simulated DB failure")

        monkeypatch.setattr(repo, "create", boom)
        with pytest.raises(StorageError, match="simulated DB failure"):
            await manager.create_command("greet", "Hello!")
        assert not module_context.commands.exists("greet")

    async def test_edit_command(self, manager: CommandManager) -> None:
        """Edit_command updates the response text."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.edit_command("greet", "Hi there!")
        assert updated.response == "Hi there!"

    async def test_edit_resolves_alias(self, manager: CommandManager) -> None:
        """Edit_command resolves an alias to the canonical command."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        updated = await manager.edit_command("hi", "Hi there!")
        assert updated.name == "greet"
        assert updated.response == "Hi there!"

    async def test_edit_not_found(self, manager: CommandManager) -> None:
        """Edit_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.edit_command("nonexistent", "Hi!")

    async def test_edit_not_custom(
        self,
        manager: CommandManager,
    ) -> None:
        """Edit_command raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.edit_command("about", "Hacked!")

    async def test_delete_command(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Delete_command removes the command and unregisters its trigger."""
        await manager.create_command("greet", "Hello!")
        deleted = await manager.delete_command("greet")
        assert deleted.name == "greet"
        assert not module_context.commands.exists("greet")

    async def test_delete_rejects_alias(self, manager: CommandManager) -> None:
        """Delete_command raises CannotDeleteByAliasError for aliases."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        with pytest.raises(CannotDeleteByAliasError) as exc_info:
            await manager.delete_command("hi")
        assert exc_info.value.alias == "hi"
        assert exc_info.value.canonical == "greet"

    async def test_delete_not_found(self, manager: CommandManager) -> None:
        """Delete_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.delete_command("nonexistent")

    async def test_delete_not_custom(
        self,
        manager: CommandManager,
    ) -> None:
        """Delete_command raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.delete_command("about")

    async def test_delete_command_on_inactive_succeeds(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
    ) -> None:
        """Delete_command works on an unregistered DB-only command."""
        await repo.create("foo", "Hi!", 0)
        deleted = await manager.delete_command("foo")
        assert deleted.name == "foo"
        with pytest.raises(CommandNotFoundError):
            await repo.get("foo")
        assert not module_context.commands.exists("foo")

    async def test_set_mod_only_on(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Set_mod_only enables moderator-only access and re-registers."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_mod_only("greet", enabled=True)
        assert updated.requires_moderator is True
        info = module_context.commands.get("greet")
        assert info is not None
        assert info.requires_moderator is True

    async def test_set_mod_only_off(self, manager: CommandManager) -> None:
        """Set_mod_only disables moderator-only access."""
        await manager.create_command("greet", "Hello!")
        await manager.set_mod_only("greet", enabled=True)
        updated = await manager.set_mod_only("greet", enabled=False)
        assert updated.requires_moderator is False

    async def test_set_mod_only_not_found(self, manager: CommandManager) -> None:
        """Set_mod_only raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.set_mod_only("nonexistent", enabled=True)

    async def test_set_mod_only_not_custom(self, manager: CommandManager) -> None:
        """Set_mod_only raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.set_mod_only("about", enabled=True)

    async def test_set_cooldown(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Set_cooldown updates cooldown and re-registers."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_cooldown("greet", 30)
        assert updated.cooldown == 30
        info = module_context.commands.get("greet")
        assert info is not None
        assert info.cooldown == 30

    async def test_set_cooldown_zero(self, manager: CommandManager) -> None:
        """Set_cooldown accepts zero to disable cooldown."""
        await manager.create_command("greet", "Hello!")
        updated = await manager.set_cooldown("greet", 0)
        assert updated.cooldown == 0

    async def test_set_cooldown_not_found(self, manager: CommandManager) -> None:
        """Set_cooldown raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.set_cooldown("nonexistent", 10)

    async def test_set_cooldown_not_custom(self, manager: CommandManager) -> None:
        """Set_cooldown raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.set_cooldown("about", 10)

    async def test_reset_use_count(self, manager: CommandManager) -> None:
        """Reset_use_count sets use_count back to zero."""
        await manager.create_command("greet", "$(count)")
        await manager.execute_command("greet", [], "tester")
        reset = await manager.reset_use_count("greet")
        assert reset.use_count == 0

    async def test_reset_use_count_not_found(self, manager: CommandManager) -> None:
        """Reset_use_count raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.reset_use_count("nonexistent")

    async def test_reset_use_count_not_custom(self, manager: CommandManager) -> None:
        """Reset_use_count raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.reset_use_count("about")

    async def test_add_alias(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Add_alias registers the alias in the registry and snapshot."""
        await manager.create_command("greet", "Hello!")
        cmd = await manager.add_alias("greet", "hi")
        assert "hi" in cmd.aliases
        assert module_context.commands.exists("hi")

    async def test_add_alias_rejects_canonical(self, manager: CommandManager) -> None:
        """Add_alias raises AliasIsCanonicalNameError for the canonical name."""
        await manager.create_command("greet", "Hello!")
        with pytest.raises(AliasIsCanonicalNameError):
            await manager.add_alias("greet", "greet")

    async def test_add_alias_rejects_external_conflict(
        self, manager: CommandManager
    ) -> None:
        """Add_alias raises CommandAlreadyExistsError on conflict with a built-in."""
        await manager.create_command("greet", "Hello!")
        with pytest.raises(CommandAlreadyExistsError):
            await manager.add_alias("greet", "about")

    async def test_add_alias_command_not_found(self, manager: CommandManager) -> None:
        """Add_alias raises CommandNotFoundError for an unknown command."""
        with pytest.raises(CommandNotFoundError):
            await manager.add_alias("nonexistent", "hi")

    async def test_add_alias_not_custom(self, manager: CommandManager) -> None:
        """Add_alias raises NotCustomCommandError for built-in commands."""
        with pytest.raises(NotCustomCommandError):
            await manager.add_alias("about", "hi")

    async def test_add_alias_invalid_name(self, manager: CommandManager) -> None:
        """Add_alias raises InvalidNameError for bad alias names."""
        await manager.create_command("greet", "Hello!")
        with pytest.raises(InvalidNameError):
            await manager.add_alias("greet", "BAD!")

    async def test_add_alias_rejects_taken(self, manager: CommandManager) -> None:
        """Add_alias raises CommandAlreadyExistsError when the alias is taken."""
        await manager.create_command("greet", "Hello!")
        await manager.create_command("bye", "Goodbye!")
        await manager.add_alias("greet", "hi")
        with pytest.raises(CommandAlreadyExistsError):
            await manager.add_alias("bye", "hi")

    async def test_remove_alias(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """Remove_alias removes the alias and keeps the canonical command."""
        await manager.create_command("greet", "Hello!")
        await manager.add_alias("greet", "hi")
        cmd = await manager.remove_alias("hi")
        assert "hi" not in cmd.aliases
        assert not module_context.commands.exists("hi")
        assert module_context.commands.exists("greet")

    async def test_remove_alias_not_found(self, manager: CommandManager) -> None:
        """Remove_alias raises CommandNotFoundError for missing aliases."""
        with pytest.raises(CommandNotFoundError):
            await manager.remove_alias("nonexistent")

    async def test_reregister_noop_for_inactive(
        self,
        manager: CommandManager,
        module_context: ModuleContext,
    ) -> None:
        """_reregister silently skips when the command is inactive."""
        cmd = await manager.create_command("greet", "Hi!")
        module_context.commands.unregister("greet")
        manager._reregister(cmd)
        assert not module_context.commands.exists("greet")

    async def test_remove_alias_on_inactive_owner_updates_db_only(
        self,
        manager: CommandManager,
        repo: CommandRepository,
        module_context: ModuleContext,
    ) -> None:
        """Remove_alias on a ghost's alias updates the DB without re-registering."""
        await repo.create("foo", "Hi!", 0)
        await repo.add_alias("foo", "bar")
        updated = await manager.remove_alias("bar")
        assert updated.name == "foo"
        assert "bar" not in updated.aliases
        assert not module_context.commands.exists("foo")
        assert not module_context.commands.exists("bar")

    async def test_execute_increments_use_count(self, manager: CommandManager) -> None:
        """Execute_command increments use_count on each call."""
        await manager.create_command("greet", "Hi!")
        first = await manager.execute_command("greet", [], "Alice")
        assert first == "Hi!"
        cmds = await manager.list_commands()
        assert cmds[0].use_count == 1
        await manager.execute_command("greet", [], "Alice")
        cmds = await manager.list_commands()
        assert cmds[0].use_count == 2

    @pytest.mark.parametrize(
        ("template", "args_list", "user", "expected"),
        [
            pytest.param("Hello", [], "Alice", "Hello", id="plain"),
            pytest.param("Count: $(count)", [], "Alice", "Count: 1", id="count-use"),
            pytest.param("Hello $(user)!", [], "Alice", "Hello Alice!", id="user"),
            pytest.param("Arg: $(1)", ["hi"], "Alice", "Arg: hi", id="positional"),
            pytest.param(
                "Deaths: $(count deaths)",
                [],
                "Alice",
                "Deaths: 1",
                id="count-named",
            ),
            pytest.param(
                "Deaths: $(getcount deaths)",
                [],
                "Alice",
                "Deaths: 0",
                id="getcount",
            ),
            pytest.param("$(rand 5 5)", [], "Alice", "5", id="rand-deterministic"),
        ],
    )
    async def test_execute_placeholder_wiring(
        self,
        manager: CommandManager,
        template: str,
        args_list: list[str],
        user: str,
        expected: str,
    ) -> None:
        """Each placeholder is wired through execute_command."""
        await manager.create_command("t", template)
        assert await manager.execute_command("t", args_list, user) == expected

    async def test_execute_placeholder_error_returns_message(
        self, manager: CommandManager
    ) -> None:
        """A placeholder error is returned as the response string, not raised."""
        await manager.create_command("t", "$(count BAD-NAME)")
        result = await manager.execute_command("t", [], "Alice")
        assert result == (
            "Invalid $(count): counter name may only contain "
            "letters, numbers, and underscores"
        )

    async def test_execute_not_found(self, manager: CommandManager) -> None:
        """Execute_command raises CommandNotFoundError for missing commands."""
        with pytest.raises(CommandNotFoundError):
            await manager.execute_command("nonexistent", [], "tester")

    async def test_edit_counter_absolute(self, manager: CommandManager) -> None:
        """Edit_counter sets an absolute counter value."""
        result = await manager.edit_counter("deaths", value=42, relative=False)
        assert result == 42

    async def test_edit_counter_relative_increment(
        self, manager: CommandManager
    ) -> None:
        """Edit_counter increments a counter with a positive delta."""
        await manager.edit_counter("deaths", value=10, relative=False)
        result = await manager.edit_counter("deaths", value=5, relative=True)
        assert result == 15

    async def test_edit_counter_relative_decrement(
        self, manager: CommandManager
    ) -> None:
        """Edit_counter decrements a counter with a negative delta."""
        await manager.edit_counter("deaths", value=10, relative=False)
        result = await manager.edit_counter("deaths", value=-3, relative=True)
        assert result == 7

    async def test_edit_counter_invalid_name(self, manager: CommandManager) -> None:
        """Edit_counter raises InvalidNameError for bad counter names."""
        with pytest.raises(InvalidNameError):
            await manager.edit_counter("BAD!", value=5, relative=False)

    async def test_counter_accessor_roundtrip(self, manager: CommandManager) -> None:
        """Get_counter/set_counter/adjust_counter round-trip through the repo."""
        assert await manager.get_counter("deaths") == 0
        assert await manager.set_counter("deaths", 10) == 10
        assert await manager.get_counter("deaths") == 10
        assert await manager.adjust_counter("deaths", 5) == 15

    async def test_list_commands(self, manager: CommandManager) -> None:
        """List_commands returns all commands ordered by name."""
        await manager.create_command("bravo", "B")
        await manager.create_command("alpha", "A")
        cmds = await manager.list_commands()
        assert [c.name for c in cmds] == ["alpha", "bravo"]

    def test_get_manager_returns_stored_manager(
        self, module_context: ModuleContext, repo: CommandRepository
    ) -> None:
        """Get_manager returns the CommandManager stored in state."""
        mgr = CommandManager(module_context, repo)
        module_context.state["manager"] = mgr
        assert get_manager(module_context) is mgr

    def test_get_manager_raises_if_not_initialized(
        self, module_context: ModuleContext
    ) -> None:
        """Get_manager raises RuntimeError when no manager is stored."""
        with pytest.raises(RuntimeError, match="CommandManager is not initialized"):
            get_manager(module_context)


class TestModuleLifecycle:
    """@on_setup and @on_teardown hooks for the custom_commands module."""

    async def test_setup_creates_tables_and_manager(
        self, module_context: ModuleContext, storage: ModuleStorage
    ) -> None:
        """Setup creates all three tables and stashes a CommandManager."""
        await setup(module_context)

        for table in ("commands", "command_aliases", "counters"):
            row = await storage.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert row is not None
            assert row["name"] == table

        mgr = module_context.state["manager"]
        assert isinstance(mgr, CommandManager)
        assert module_context.config.get("default_cooldown") == 5
        assert module_context.config.get("max_nesting_depth") == 4

    async def test_setup_loads_existing_commands(
        self,
        module_context: ModuleContext,
        repo: CommandRepository,
    ) -> None:
        """Setup loads DB commands and aliases into the registry."""
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)
        await repo.add_alias("greet", "hi")
        await setup(module_context)

        assert module_context.commands.exists("greet")
        assert module_context.commands.exists("bye")
        assert module_context.commands.exists("hi")

    async def test_setup_skips_conflicts_during_load(
        self,
        module_context: ModuleContext,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
    ) -> None:
        """Setup skips DB commands that conflict with existing triggers."""

        # Pre-register "greet" on a different module so setup's load_all hits
        # its conflict-skip branch when it tries to register the custom command.
        async def handler(ctx: object) -> None:
            pass

        command_dispatcher.register(
            name="greet",
            handler=handler,
            module_name="other_module",
        )
        await repo.create("greet", "Hello!", 5)
        await repo.create("bye", "Goodbye!", 0)

        await setup(module_context)  # must not raise

        greet = command_dispatcher.get("greet")
        assert greet is not None
        assert greet.module_name == "other_module"
        assert module_context.commands.exists("bye")

    async def test_teardown_clears_manager(self, module_context: ModuleContext) -> None:
        """Teardown clears the manager from state."""
        await setup(module_context)
        await teardown(module_context)
        assert module_context.state["manager"] is None

    @pytest.mark.usefixtures("registered_handlers")
    async def test_setup_conflict_surfaces_inactive_message_on_edit(
        self,
        module_context: ModuleContext,
        repo: CommandRepository,
        command_dispatcher: CommandDispatcher,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """A ghost from setup is visible as disabled when moderators try to edit it."""
        _register_foreign_command(command_dispatcher, "greet")
        await repo.create("greet", "Hello!", 0)

        await setup(module_context)

        assert module_context.commands.get("greet") is None

        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcommand !greet New!", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _send_bodies(owncast_client)[-1] == (
            "Custom command !greet is disabled because another module "
            "registered the same name. Delete it or resolve the conflict to re-enable."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestAddCommand:
    """!addcommand handler."""

    async def test_creates_command(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addcommand !greet Hi creates and confirms."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addcommand !greet Hello there", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Command !greet created."
        cmds = await get_manager(module_context).list_commands()
        assert cmds[0].name == "greet"
        assert cmds[0].response == "Hello there"

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addcommand with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addcommand", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Usage: !addcommand !<name> <response text>"
        )

    async def test_invalid_name(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addcommand with an invalid name reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addcommand !BAD! Hello", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Invalid command name. Only letters, numbers, and underscores are allowed."
        )

    async def test_conflict(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addcommand with a conflicting name reports the error."""
        await get_manager(module_context).create_command("greet", "Hi")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addcommand !greet Hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Command !greet already exists. Use '!editcommand' to modify it."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestEditCommand:
    """!editcommand handler."""

    async def test_edits_response(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcommand !greet New updates and confirms."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcommand !greet Hi there", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Command !greet updated."
        cmd = (await manager.list_commands())[0]
        assert cmd.response == "Hi there"

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcommand with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcommand", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Usage: !editcommand !<name> <new response>"
        )

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcommand with an unknown name reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcommand !nope X", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcommand on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcommand !about Hacked", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestDeleteCommand:
    """!deletecommand handler."""

    async def test_deletes_command(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand removes the command and confirms."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Command !greet deleted."
        assert await manager.list_commands() == []

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !deletecommand !<name>"

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand with unknown name reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand !nope", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand !about", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )

    async def test_rejects_alias(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand on an alias reports the helpful CannotDelete message."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        await manager.add_alias("greet", "hi")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "!hi is an alias of !greet. "
            "Use !removealias !hi to remove the alias, "
            "or !deletecommand !greet to delete the command."
        )

    async def test_deletes_inactive_command(
        self,
        repo: CommandRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!deletecommand removes an inactive DB row and confirms deletion."""
        await repo.create("foo", "Hi!", 0)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!deletecommand !foo", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _send_bodies(owncast_client)[-1] == "Command !foo deleted."


@pytest.mark.usefixtures("module_lifecycle")
class TestCommandModOnlyCommand:
    """!commandmodonly handler."""

    @pytest.mark.parametrize(
        ("setting", "expected_state"),
        [
            pytest.param("on", "moderator-only", id="on"),
            pytest.param("off", "public", id="off"),
        ],
    )
    async def test_toggle(
        self,
        setting: str,
        expected_state: str,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandmodonly <on|off> updates and confirms."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        if setting == "off":
            await manager.set_mod_only("greet", enabled=True)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body=f"!commandmodonly !greet {setting}", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[-1]
            == f"Command !greet is now {expected_state}."
        )

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandmodonly with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandmodonly !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0] == "Usage: !commandmodonly !<name> <on|off>"
        )

    async def test_bad_setting(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandmodonly with a bad setting reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandmodonly !greet maybe", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Invalid setting. Expected 'on' or 'off'."
        )

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandmodonly on an unknown command reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandmodonly !nope on", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandmodonly on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandmodonly !about on", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestResetCommand:
    """!resetcommand handler."""

    async def test_resets(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!resetcommand zeroes the use_count and confirms."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "$(count)")
        await manager.execute_command("greet", [], "Alice")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!resetcommand !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == "Command !greet counter reset."
        cmd = (await manager.list_commands())[0]
        assert cmd.use_count == 0

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!resetcommand with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!resetcommand", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !resetcommand !<name>"

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!resetcommand with unknown name reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!resetcommand !nope", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!resetcommand on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!resetcommand !about", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestEditCounterCommand:
    """!editcounter handler."""

    async def test_absolute_set(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter <name> <N> sets an absolute value."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter deaths 42", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Set the deaths counter to 42."

    async def test_relative_increment(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter <name> +N increments and formats correctly."""
        await get_manager(module_context).set_counter("deaths", 10)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter deaths +5", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Adjusted the deaths counter by +5 to 15."
        )

    async def test_relative_decrement(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter <name> -N decrements and formats correctly."""
        await get_manager(module_context).set_counter("deaths", 10)
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter deaths -3", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Adjusted the deaths counter by -3 to 7."
        )

    async def test_usage_wrong_arg_count(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter with wrong arg count sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter deaths", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !editcounter <name> <value>"

    async def test_non_integer_value(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter with non-integer value reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter deaths abc", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Invalid value. Must be an integer (e.g. 15, +1, -3)."
        )

    async def test_invalid_name(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!editcounter with invalid name reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!editcounter BAD! 5", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Invalid counter name. Only letters, numbers, and underscores are allowed."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestCommandCooldownCommand:
    """!commandcooldown handler."""

    async def test_sets_cooldown(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown !<name> <seconds> sets and confirms."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !greet 30", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == "Command !greet cooldown set to 30s."

    async def test_zero_disables(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown !<name> 0 reports 'disabled'."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !greet 0", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == "Command !greet cooldown disabled."

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Usage: !commandcooldown !<name> <seconds>"
        )

    async def test_non_integer(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown with non-integer value reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !greet abc", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Invalid cooldown value. Must be a non-negative integer."
        )

    async def test_negative(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown with negative value reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !greet -1", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert (
            _send_bodies(owncast_client)[0]
            == "Cooldown must be a non-negative integer (0 to disable)."
        )

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown with unknown name reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !nope 10", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!commandcooldown on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!commandcooldown !about 10", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestAddAliasCommand:
    """!addalias handler."""

    async def test_adds_alias(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias !greet !hi adds and confirms."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == "Alias !hi added to !greet."

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias with missing args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !addalias !<command> !<alias>"

    async def test_unknown_command(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias on an unknown command reports not-found."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !nope !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "That command does not exist."

    async def test_not_custom(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias on a built-in reports not-custom."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !about !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "That command is not a custom command and cannot be modified."
        )

    async def test_invalid_alias(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias with an invalid alias name reports the error."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet !BAD!", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == (
            "Invalid alias name. Only letters, numbers, and underscores are allowed."
        )

    async def test_alias_is_canonical(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias with alias equal to canonical name reports the error."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet !greet", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == (
            "An alias cannot be the same as the command name."
        )

    async def test_alias_conflicts_with_another_command(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias rejects an alias already owned by another command."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        await manager.create_command("bye", "Bye!")
        await manager.add_alias("greet", "hi")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !bye !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == (
            "Alias !hi conflicts with an existing command."
        )

    async def test_alias_already_assigned_to_inactive_owner(
        self,
        module_context: ModuleContext,
        repo: CommandRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias reaches AliasAlreadyExistsError when prior owner is inactive."""
        await get_manager(module_context).create_command("greet", "Hi!")
        await repo.create("foo", "Hello!", 0)
        await repo.add_alias("foo", "bar")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet !bar", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _send_bodies(owncast_client)[-1] == (
            "Alias !bar is already assigned to !foo."
        )

    async def test_alias_conflicts_with_external(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!addalias with an alias that conflicts with a built-in."""
        await get_manager(module_context).create_command("greet", "Hi!")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!addalias !greet !about", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == (
            "Alias !about conflicts with an existing command."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestRemoveAliasCommand:
    """!removealias handler."""

    async def test_removes_alias(
        self,
        module_context: ModuleContext,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!removealias !hi removes and confirms."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        await manager.add_alias("greet", "hi")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!removealias !hi", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[-1] == "Alias !hi removed from !greet."
        cmds = await manager.list_commands()
        assert cmds[0].aliases == frozenset()

    async def test_usage_missing_args(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!removealias with no args sends usage."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!removealias", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Usage: !removealias !<alias>"

    async def test_unknown(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!removealias with an unknown alias reports the error."""
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!removealias !nope", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == "Alias !nope does not exist."

    async def test_removes_inactive_owner_alias(
        self,
        module_context: ModuleContext,
        repo: CommandRepository,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!removealias clears a ghost's alias from the DB without re-registering."""
        await repo.create("foo", "Hi!", 0)
        await repo.add_alias("foo", "bar")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body="!removealias !bar", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _send_bodies(owncast_client)[-1] == "Alias !bar removed from !foo."
        assert not module_context.commands.exists("bar")


@pytest.mark.usefixtures("module_lifecycle")
class TestInactiveCommandRejection:
    """Cross-cutting check: command-modifying handlers reject inactive commands.

    A custom command becomes "inactive" when another module registers the same
    name, taking ownership in the dispatcher. Every handler that targets an
    existing custom command must report the disabled message rather than
    silently no-op or crash.
    """

    @pytest.mark.parametrize(
        "raw_body",
        [
            pytest.param("!editcommand !greet New!", id="editcommand"),
            pytest.param("!commandmodonly !greet on", id="commandmodonly"),
            pytest.param("!resetcommand !greet", id="resetcommand"),
            pytest.param("!commandcooldown !greet 10", id="commandcooldown"),
            pytest.param("!addalias !greet !hi", id="addalias"),
        ],
    )
    async def test_inactive_command_rejected(
        self,
        module_context: ModuleContext,
        command_dispatcher: CommandDispatcher,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
        raw_body: str,
    ) -> None:
        """Each command-modifying handler reports the disabled message."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hi!")
        module_context.commands.unregister("greet")
        _register_foreign_command(command_dispatcher, "greet")
        user = make_user(is_moderator=True)
        event = make_chat_event(raw_body=raw_body, user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        assert _send_bodies(owncast_client)[-1] == (
            "Custom command !greet is disabled because another module "
            "registered the same name. Delete it or resolve the conflict to re-enable."
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestListCommandsCommand:
    """!listcommands handler."""

    async def test_sends_url(
        self,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """!listcommands sends the public URL of the /list route."""
        event = make_chat_event(raw_body="!listcommands")
        await event_dispatcher.dispatch(EventType.CHAT, event)

        assert _send_bodies(owncast_client)[0] == (
            "Custom commands: http://localhost:8081/owlbot/custom_commands/list"
        )


@pytest.mark.usefixtures("module_lifecycle")
class TestCustomCommandsListRoute:
    """/list HTML route."""

    async def test_list_empty(
        self,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /list with no commands serves the empty-state message."""
        resp = await route_client.get("/owlbot/custom_commands/list")
        assert resp.status == 200
        assert resp.content_type == "text/html"
        body = await resp.text()
        assert "No custom commands defined." in body

    async def test_list_populated(
        self,
        module_context: ModuleContext,
        route_client: TestClient[Any, Any],
    ) -> None:
        """GET /list renders names, aliases, responses, cooldowns, permissions."""
        manager = get_manager(module_context)
        await manager.create_command("greet", "Hello there")
        await manager.add_alias("greet", "hi")
        await manager.set_cooldown("greet", 30)
        await manager.create_command("bye", "Goodbye")
        await manager.set_mod_only("bye", enabled=True)
        await manager.set_cooldown("bye", 0)

        resp = await route_client.get("/owlbot/custom_commands/list")
        assert resp.status == 200
        body = await resp.text()

        assert "!greet" in body
        assert "!bye" in body
        assert "!hi" in body
        assert "Hello there" in body
        assert "Goodbye" in body
        assert "30s" in body
        assert "None" in body
        assert "Moderator" in body
        assert "Everyone" in body


@pytest.mark.usefixtures("module_lifecycle")
class TestModeratorPermissions:
    """Moderator-required commands deny non-moderators."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("addcommand", id="addcommand"),
            pytest.param("editcommand", id="editcommand"),
            pytest.param("deletecommand", id="deletecommand"),
            pytest.param("commandmodonly", id="commandmodonly"),
            pytest.param("resetcommand", id="resetcommand"),
            pytest.param("editcounter", id="editcounter"),
            pytest.param("commandcooldown", id="commandcooldown"),
            pytest.param("addalias", id="addalias"),
            pytest.param("removealias", id="removealias"),
        ],
    )
    async def test_non_moderator_denied(
        self,
        command: str,
        event_dispatcher: EventDispatcher,
        owncast_client: RecordingOwncastClient,
    ) -> None:
        """Non-moderator invocation triggers the dispatcher denial."""
        user = make_user(is_moderator=False)
        event = make_chat_event(raw_body=f"!{command}", user=user)
        await event_dispatcher.dispatch(EventType.CHAT, event)
        denials = [
            c.kwargs["body"]
            for c in owncast_client.calls
            if c.name == "send_system_message_to_client"
        ]
        assert denials == [f"Only moderators can use !{command}."]
