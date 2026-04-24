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

"""Tests for owlbot.testing recording stubs and stub config."""

from __future__ import annotations

import inspect
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest

from owlbot.api.owncast_admin_client import OwncastAdminClient
from owlbot.api.owncast_client import OwncastClient
from owlbot.testing.stubs import (
    RecordedCall,
    RecordingOwncastAdminClient,
    RecordingOwncastClient,
)


def _public_async_methods(cls: type) -> set[str]:
    """Return the set of public (non-underscore) async method names on cls."""
    return {
        name
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_") and inspect.iscoroutinefunction(method)
    }


def _synthesize_value(param_name: str, annotation: Any) -> Any:
    """Build a distinct, type-appropriate value for a parameter.

    Values differ per parameter so that a transposed key or value inside a
    stub's ``RecordedCall`` dict literal is caught by equality against the
    originally-passed kwargs.
    """
    origin = get_origin(annotation)
    if origin in (types.UnionType, Union):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return _synthesize_value(param_name, non_none[0])
    if origin is list:
        return []
    if origin is dict:
        return {}
    if annotation is str:
        return f"stub:{param_name}"
    if annotation is int:
        return hash(param_name) & 0xFFFF
    if annotation is bool:
        return True
    msg = f"Unsupported annotation {annotation!r} for parameter {param_name!r}"
    raise TypeError(msg)


@pytest.mark.parametrize(
    ("source", "stub"),
    [
        pytest.param(OwncastClient, RecordingOwncastClient, id="owncast-client"),
        pytest.param(
            OwncastAdminClient, RecordingOwncastAdminClient, id="owncast-admin-client"
        ),
    ],
)
class TestStubClientParity:
    """Guards against drift between real clients and their recording stubs.

    These tests fail when the real client gains, loses, or changes a public
    async method without a matching update to the stub. The stub is meant
    to be a hand-written 1:1 mirror; CI enforces that invariant here.
    """

    def test_stub_covers_every_public_async_method(
        self, source: type, stub: type
    ) -> None:
        """Every public async method on the source must exist on the stub."""
        missing = _public_async_methods(source) - _public_async_methods(stub)
        assert not missing, f"Stub missing methods: {sorted(missing)}"

    def test_stub_has_no_extra_public_async_methods(
        self, source: type, stub: type
    ) -> None:
        """The stub must not expose methods the real client does not have."""
        extra = _public_async_methods(stub) - _public_async_methods(source)
        assert not extra, f"Stub has extra methods: {sorted(extra)}"

    def test_stub_signatures_match(self, source: type, stub: type) -> None:
        """Each stub method's signature must match the source's exactly."""
        mismatches: list[str] = []
        for name in sorted(_public_async_methods(source)):
            source_sig = inspect.signature(getattr(source, name))
            stub_sig = inspect.signature(getattr(stub, name))
            if source_sig != stub_sig:
                mismatches.append(
                    f"  {name}:\n    source: {source_sig}\n    stub:   {stub_sig}"
                )
        assert not mismatches, "Signature mismatches:\n" + "\n".join(mismatches)


@pytest.mark.parametrize(
    ("source", "stub"),
    [
        pytest.param(OwncastClient, RecordingOwncastClient, id="owncast-client"),
        pytest.param(
            OwncastAdminClient, RecordingOwncastAdminClient, id="owncast-admin-client"
        ),
    ],
)
class TestStubRecordingFidelity:
    """Ensures each stub method records the exact args it received.

    ``TestStubClientParity`` guards method signatures. These tests guard the
    hand-written ``RecordedCall(name, {...})`` literals inside each stub body:
    call every public async method with distinct synthesized kwargs and
    confirm the recorded call name and kwargs dict match exactly. Catches
    typos in keys (``"user": user_id`` vs ``"user_id": user_id``) and value
    transpositions that signature parity cannot see.
    """

    async def test_every_method_records_passed_kwargs_faithfully(
        self, source: type, stub: type
    ) -> None:
        """Every public async method records its method name and kwargs."""
        instance = stub()
        mismatches: list[str] = []
        for name in sorted(_public_async_methods(source)):
            hints = get_type_hints(getattr(source, name))
            sig = inspect.signature(getattr(source, name))
            kwargs = {
                p_name: _synthesize_value(p_name, hints[p_name])
                for p_name in sig.parameters
                if p_name != "self"
            }
            instance.calls.clear()
            await getattr(instance, name)(**kwargs)
            if len(instance.calls) != 1:
                mismatches.append(
                    f"{name}: expected 1 recorded call, got {len(instance.calls)}"
                )
                continue
            call = instance.calls[0]
            if call.name != name:
                mismatches.append(f"{name}: recorded under wrong name {call.name!r}")
            if call.kwargs != kwargs:
                mismatches.append(
                    f"{name}:\n    expected: {kwargs}\n    recorded: {call.kwargs}"
                )
        assert not mismatches, "Recording fidelity failures:\n" + "\n".join(mismatches)


class TestRecordingOwncastClient:
    """Exercises the RecordingOwncastClient stub."""

    async def test_send_message_records_call(self) -> None:
        """send_message appends the method name and all args to calls."""
        client = RecordingOwncastClient()
        result = await client.send_message("hello")
        assert result == ""
        assert client.calls == [
            ("send_message", {"body": "hello", "unsanitized": False})
        ]

    async def test_unsanitized_true_is_recorded(self) -> None:
        """unsanitized=True is included in the kwargs dict."""
        client = RecordingOwncastClient()
        await client.send_message("hello", unsanitized=True)
        assert client.calls == [
            ("send_message", {"body": "hello", "unsanitized": True})
        ]

    async def test_unsanitized_false_is_recorded(self) -> None:
        """unsanitized=False (the default) is recorded explicitly."""
        client = RecordingOwncastClient()
        await client.send_message("hello", unsanitized=False)
        assert client.calls == [
            ("send_message", {"body": "hello", "unsanitized": False})
        ]

    async def test_send_system_message_to_client_records_args(self) -> None:
        """send_system_message_to_client records all arguments."""
        client = RecordingOwncastClient()
        result = await client.send_system_message_to_client(42, "whisper")
        assert result == ""
        assert client.calls == [
            (
                "send_system_message_to_client",
                {"client_id": 42, "body": "whisper", "unsanitized": False},
            )
        ]

    async def test_multiple_calls_recorded_in_order(self) -> None:
        """Multiple calls are recorded in the order they happen."""
        client = RecordingOwncastClient()
        await client.send_message("first")
        await client.send_system_message("second")
        await client.send_action("third")
        assert len(client.calls) == 3
        assert client.calls[0] == (
            "send_message",
            {"body": "first", "unsanitized": False},
        )
        assert client.calls[1] == (
            "send_system_message",
            {"body": "second", "unsanitized": False},
        )
        assert client.calls[2] == (
            "send_action",
            {"body": "third", "unsanitized": False},
        )

    async def test_get_chat_history_returns_empty_list(self) -> None:
        """get_chat_history returns [] and records the call."""
        client = RecordingOwncastClient()
        result = await client.get_chat_history()
        assert result == []
        assert client.calls == [("get_chat_history", {})]

    async def test_get_connected_clients_returns_empty_list(self) -> None:
        """get_connected_clients returns [] and records the call."""
        client = RecordingOwncastClient()
        result = await client.get_connected_clients()
        assert result == []
        assert client.calls == [("get_connected_clients", {})]

    async def test_get_status_returns_empty_dict(self) -> None:
        """get_status returns {} and records the call."""
        client = RecordingOwncastClient()
        result = await client.get_status()
        assert result == {}
        assert client.calls == [("get_status", {})]

    async def test_post_methods_return_empty_string(self) -> None:
        """All post-style methods return empty string."""
        client = RecordingOwncastClient()
        assert await client.send_message("x") == ""
        assert await client.send_system_message("x") == ""
        assert await client.send_action("x") == ""
        assert await client.send_system_message_to_client(1, "x") == ""
        assert await client.set_message_visibility(["a"], visible=True) == ""
        assert await client.set_stream_title("t") == ""

    def test_base_url_property(self) -> None:
        """base_url returns the stub default."""
        client = RecordingOwncastClient()
        assert client.base_url == "http://localhost:8080"

    async def test_set_message_visibility_records_args(self) -> None:
        """set_message_visibility records message_ids and visible."""
        client = RecordingOwncastClient()
        await client.set_message_visibility(["msg-1", "msg-2"], visible=False)
        assert client.calls == [
            (
                "set_message_visibility",
                {"message_ids": ["msg-1", "msg-2"], "visible": False},
            )
        ]

    async def test_set_stream_title_records_args(self) -> None:
        """set_stream_title records the title argument."""
        client = RecordingOwncastClient()
        await client.set_stream_title("New Title")
        assert client.calls == [("set_stream_title", {"title": "New Title"})]

    async def test_send_system_message_records_call(self) -> None:
        """send_system_message records body."""
        client = RecordingOwncastClient()
        await client.send_system_message("sys msg")
        assert client.calls == [
            ("send_system_message", {"body": "sys msg", "unsanitized": False})
        ]

    async def test_send_action_records_call(self) -> None:
        """send_action records body."""
        client = RecordingOwncastClient()
        await client.send_action("does a thing")
        assert client.calls == [
            ("send_action", {"body": "does a thing", "unsanitized": False})
        ]

    async def test_recorded_call_exposes_name_and_kwargs_attributes(self) -> None:
        """Recorded calls expose .name and .kwargs for attribute-style access."""
        client = RecordingOwncastClient()
        await client.send_message("hello")
        call = client.calls[0]
        assert call.name == "send_message"
        assert call.kwargs == {"body": "hello", "unsanitized": False}

    def test_recorded_call_equals_plain_tuple(self) -> None:
        """RecordedCall compares equal to a (name, kwargs) tuple, per its docstring.

        Many tests assert against tuple literals
        (e.g. ``client.calls == [("send_message", {...})]``); this test locks
        the contract directly so a refactor of RecordedCall surfaces here
        rather than as scattered per-method failures.
        """
        assert RecordedCall("x", {"a": 1}) == ("x", {"a": 1})
        assert RecordedCall("x", {}) != ("x", {"a": 1})
        assert RecordedCall("x", {"a": 1}) != ("y", {"a": 1})


class TestRecordingOwncastAdminClient:
    """Exercises the RecordingOwncastAdminClient stub."""

    async def test_get_status_records_and_returns_dict(self) -> None:
        """get_status records the call and returns {}."""
        client = RecordingOwncastAdminClient()
        result = await client.get_status()
        assert result == {}
        assert client.calls == [("get_status", {})]

    async def test_set_user_enabled_records_args(self) -> None:
        """set_user_enabled records user_id and enabled."""
        client = RecordingOwncastAdminClient()
        await client.set_user_enabled("user-123", enabled=False)
        assert client.calls == [
            ("set_user_enabled", {"user_id": "user-123", "enabled": False})
        ]

    async def test_set_user_moderator_records_args(self) -> None:
        """set_user_moderator records user_id and is_mod."""
        client = RecordingOwncastAdminClient()
        await client.set_user_moderator("user-456", is_mod=True)
        assert client.calls == [
            ("set_user_moderator", {"user_id": "user-456", "is_mod": True})
        ]

    def test_base_url_property(self) -> None:
        """base_url returns the stub default."""
        client = RecordingOwncastAdminClient()
        assert client.base_url == "http://localhost:8080"

    async def test_get_active_viewers_returns_empty_list(self) -> None:
        """get_active_viewers returns [] and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.get_active_viewers()
        assert result == []
        assert client.calls == [("get_active_viewers", {})]

    async def test_get_hardware_stats_returns_empty_dict(self) -> None:
        """get_hardware_stats returns {} and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.get_hardware_stats()
        assert result == {}
        assert client.calls == [("get_hardware_stats", {})]

    async def test_get_server_config_returns_empty_dict(self) -> None:
        """get_server_config returns {} and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.get_server_config()
        assert result == {}
        assert client.calls == [("get_server_config", {})]

    async def test_ban_ip_address_records_args(self) -> None:
        """ban_ip_address records the ip argument."""
        client = RecordingOwncastAdminClient()
        await client.ban_ip_address("192.168.1.1")
        assert client.calls == [("ban_ip_address", {"ip": "192.168.1.1"})]

    async def test_unban_ip_address_records_args(self) -> None:
        """unban_ip_address records the ip argument."""
        client = RecordingOwncastAdminClient()
        await client.unban_ip_address("10.0.0.1")
        assert client.calls == [("unban_ip_address", {"ip": "10.0.0.1"})]

    async def test_disconnect_stream_returns_empty_dict(self) -> None:
        """disconnect_stream returns {} and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.disconnect_stream()
        assert result == {}
        assert client.calls == [("disconnect_stream", {})]

    async def test_get_logs_returns_empty_list(self) -> None:
        """get_logs returns [] and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.get_logs()
        assert result == []
        assert client.calls == [("get_logs", {})]

    async def test_get_warnings_returns_empty_list(self) -> None:
        """get_warnings returns [] and records the call."""
        client = RecordingOwncastAdminClient()
        result = await client.get_warnings()
        assert result == []
        assert client.calls == [("get_warnings", {})]
