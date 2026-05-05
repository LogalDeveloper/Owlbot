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

"""Regression tests for connect token and session management."""

from __future__ import annotations

from datetime import timedelta

import pytest

from owlbot.sessions import (
    DEFAULT_CONNECT_DESTINATION_PATH,
    ConnectTokenRedemptionError,
    SessionManager,
    make_session_url_for,
)
from owlbot.testing import make_user


class TestSessionManager:
    """Focused tests for the in-memory session manager."""

    def test_issue_connect_token_tracks_internal_destination(self) -> None:
        """Issued tokens retain the internal Owlbot destination path."""
        manager = SessionManager()
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", is_authenticated=True),
        )
        connect_token = manager.get_connect_token(token)

        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/demo/panel"

    def test_issue_connect_token_defaults_to_connect_status_destination(self) -> None:
        """Issued tokens default to the built-in connect status page."""
        manager = SessionManager()
        token = manager.issue_connect_token(
            user=make_user(id="alice", is_authenticated=True),
        )
        connect_token = manager.get_connect_token(token)

        assert connect_token is not None
        assert connect_token.destination_path == DEFAULT_CONNECT_DESTINATION_PATH

    def test_issue_connect_token_replaces_pending_token_for_same_user(self) -> None:
        """Issuing a new token invalidates older pending tokens for that user."""
        manager = SessionManager()
        first_alice_token = manager.issue_connect_token(
            "/owlbot/demo/first",
            user=make_user(id="alice", is_authenticated=True),
        )
        bob_token = manager.issue_connect_token(
            "/owlbot/demo/bob",
            user=make_user(id="bob", is_authenticated=True),
        )

        second_alice_token = manager.issue_connect_token(
            "/owlbot/demo/second",
            user=make_user(id="alice", is_authenticated=True),
        )

        assert manager.get_connect_token(first_alice_token) is None
        assert manager.get_connect_token(bob_token) is not None
        connect_token = manager.get_connect_token(second_alice_token)
        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/demo/second"
        assert first_alice_token not in manager._connect_tokens_by_token
        assert manager._connect_token_by_user_id == {
            "alice": second_alice_token,
            "bob": bob_token,
        }

    @pytest.mark.parametrize(
        "destination_path",
        [
            pytest.param("/outside/demo/panel", id="outside-owlbot"),
            pytest.param("https://example.com/owlbot/demo/panel", id="absolute-url"),
            pytest.param("/owlbot", id="missing-trailing-path"),
            pytest.param("//example.com/owlbot/demo/panel", id="protocol-relative-url"),
        ],
    )
    def test_issue_connect_token_rejects_non_owlbot_destination(
        self, destination_path: str
    ) -> None:
        """Connect tokens only allow redirects within Owlbot's own routes."""
        manager = SessionManager()

        with pytest.raises(ValueError, match="within /owlbot"):
            manager.issue_connect_token(
                destination_path,
                user=make_user(id="alice", is_authenticated=True),
            )

    @pytest.mark.parametrize(
        "destination_path",
        [
            pytest.param("/owlbot/demo/../admin", id="parent-segment"),
            pytest.param("/owlbot/demo/./panel", id="current-segment"),
            pytest.param("/owlbot/demo/%2e%2e/admin", id="encoded-parent-segment"),
            pytest.param("/owlbot/demo/%2E/panel", id="encoded-current-segment"),
        ],
    )
    def test_issue_connect_token_rejects_dot_segment_destination(
        self, destination_path: str
    ) -> None:
        """Connect-token redirects reject browser-resolved dot segments."""
        manager = SessionManager()

        with pytest.raises(ValueError, match="dot segments"):
            manager.issue_connect_token(
                destination_path,
                user=make_user(id="alice", is_authenticated=True),
            )

    @pytest.mark.parametrize(
        "destination_path",
        [
            pytest.param(r"/owlbot/demo\..\admin", id="backslash"),
            pytest.param("/owlbot/demo/%5cadmin", id="encoded-backslash"),
        ],
    )
    def test_issue_connect_token_rejects_backslash_destination(
        self, destination_path: str
    ) -> None:
        """Connect-token redirects reject browser-normalized backslashes."""
        manager = SessionManager()

        with pytest.raises(ValueError, match="URL path separators"):
            manager.issue_connect_token(
                destination_path,
                user=make_user(id="alice", is_authenticated=True),
            )

    def test_redeem_connect_token_replaces_existing_session(self) -> None:
        """Redeeming a connect token can replace an existing browser session."""
        manager = SessionManager()
        existing = manager.create_session(make_user(id="guest"))
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", is_authenticated=True),
        )

        redeemed = manager.redeem_connect_token(
            token,
            replacing_session_id=existing.session_id,
        )

        assert redeemed.user.id == "alice"
        assert redeemed.session_id != token
        assert manager.get_session(existing.session_id) is None
        assert manager.get_session(redeemed.session_id) is redeemed

    def test_redeem_connect_token_rejects_invalid_token(self) -> None:
        """Unknown connect tokens are rejected."""
        manager = SessionManager()

        with pytest.raises(ConnectTokenRedemptionError, match="invalid connect token"):
            manager.redeem_connect_token("not-a-real-token")

    def test_redeem_connect_token_rejects_expired_token(self) -> None:
        """Expired connect tokens are rejected and evicted."""
        manager = SessionManager(connect_token_ttl=timedelta(seconds=0))
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", is_authenticated=True),
        )

        with pytest.raises(ConnectTokenRedemptionError, match="connect token expired"):
            manager.redeem_connect_token(token)

        assert manager.get_connect_token(token) is None
        assert token not in manager._connect_tokens_by_token
        assert "alice" not in manager._connect_token_by_user_id

    def test_redeem_connect_token_deletes_token_when_claimed(self) -> None:
        """Redeemed connect tokens are evicted immediately."""
        manager = SessionManager()
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", is_authenticated=True),
        )

        manager.redeem_connect_token(token)

        assert manager.get_connect_token(token) is None
        assert token not in manager._connect_tokens_by_token
        assert "alice" not in manager._connect_token_by_user_id

        with pytest.raises(ConnectTokenRedemptionError, match="invalid connect token"):
            manager.redeem_connect_token(token)

    def test_refresh_user_updates_all_sessions_for_matching_user(self) -> None:
        """Refreshing a user snapshot updates all active sessions for that user."""
        manager = SessionManager()
        first = manager.create_session(make_user(id="alice", display_name="Alice"))
        second = manager.create_session(make_user(id="alice", display_name="Alice"))

        updated_user = make_user(
            id="alice",
            display_name="Alice Renamed",
            is_authenticated=True,
            is_moderator=True,
        )
        manager.refresh_user(updated_user)

        refreshed_first = manager.get_session(first.session_id)
        refreshed_second = manager.get_session(second.session_id)

        assert refreshed_first is not None
        assert refreshed_second is not None
        assert refreshed_first.user.display_name == "Alice Renamed"
        assert refreshed_second.user.display_name == "Alice Renamed"
        assert refreshed_first.is_authenticated is True
        assert refreshed_second.is_moderator is True

    def test_refresh_user_updates_pending_tokens_before_redemption(self) -> None:
        """Refreshing a user snapshot updates unredeemed connect tokens too."""
        manager = SessionManager()
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", display_name="Alice"),
        )

        updated_user = make_user(
            id="alice",
            display_name="Alice Renamed",
            is_authenticated=True,
            is_moderator=True,
        )
        manager.refresh_user(updated_user)

        session = manager.redeem_connect_token(token)

        assert session.user.display_name == "Alice Renamed"
        assert session.is_authenticated is True
        assert session.is_moderator is True

    def test_get_session_drops_expired_session(self) -> None:
        """Expired sessions are removed on read."""
        manager = SessionManager(session_ttl=timedelta(seconds=0))
        session = manager.create_session(make_user(id="alice"))

        assert manager.get_session(session.session_id) is None

    def test_clear_expired_removes_expired_tokens_and_sessions(self) -> None:
        """Periodic cleanup removes expired tokens and sessions."""
        manager = SessionManager(
            connect_token_ttl=timedelta(seconds=0),
            session_ttl=timedelta(seconds=0),
        )
        token = manager.issue_connect_token(
            "/owlbot/demo/panel",
            user=make_user(id="alice", is_authenticated=True),
        )
        session = manager.create_session(make_user(id="alice"))

        manager.clear_expired()

        assert manager.get_connect_token(token) is None
        assert token not in manager._connect_tokens_by_token
        assert "alice" not in manager._connect_token_by_user_id
        assert manager.get_session(session.session_id) is None

    async def test_start_and_close_cancel_cleanup_task_cleanly(self) -> None:
        """Background cleanup can be started and shut down cleanly."""
        manager = SessionManager()

        manager.start()
        try:
            assert manager._cleanup_task is not None
            assert manager._cleanup_task.done() is False
            assert manager._cleanup_task.get_name() == "Session Manager - Cleanup loop"
        finally:
            await manager.close()

        assert manager._cleanup_task is None


class TestMakeSessionUrlFor:
    """Tests for the shared protected-route URL builder."""

    def test_accepts_module_relative_path(self) -> None:
        """Session URLs add the module namespace to relative paths."""
        session_manager = SessionManager()
        session_url_for = make_session_url_for(
            session_manager=session_manager,
            public_base_url="https://example.com",
            module_name="test_module",
            user=make_user(id="alice"),
        )

        url = session_url_for("panel")
        token = url.rsplit("/", 1)[-1]
        connect_token = session_manager.get_connect_token(token)

        assert url.startswith("https://example.com/owlbot/connect/")
        assert connect_token is not None
        assert connect_token.destination_path == "/owlbot/test_module/panel"

    def test_strips_public_base_url_trailing_slash(self) -> None:
        """Generated connect URLs do not include a double slash before /owlbot."""
        session_manager = SessionManager()
        session_url_for = make_session_url_for(
            session_manager=session_manager,
            public_base_url="https://example.com/",
            module_name="test_module",
            user=make_user(id="alice"),
        )

        assert session_url_for("/panel").startswith(
            "https://example.com/owlbot/connect/"
        )

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("../panel", id="relative-parent"),
            pytest.param("/../panel", id="absolute-parent"),
            pytest.param("/../../admin", id="multi-parent"),
            pytest.param("nested/../../other", id="nested-parent"),
            pytest.param("/%2e%2e/panel", id="encoded-parent"),
        ],
    )
    def test_rejects_paths_outside_module_namespace(self, path: str) -> None:
        """Module-provided session paths cannot escape their namespace."""
        session_manager = SessionManager()
        session_url_for = make_session_url_for(
            session_manager=session_manager,
            public_base_url="https://example.com",
            module_name="test_module",
            user=make_user(id="alice"),
        )

        with pytest.raises(ValueError, match="module namespace"):
            session_url_for(path)
