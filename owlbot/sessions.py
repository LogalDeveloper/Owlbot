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

"""In-memory management for one-time connect tokens and browser sessions."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from owlbot.api.event_types import User

SESSION_COOKIE_NAME = "owlbot_session"
DEFAULT_CONNECT_DESTINATION_PATH = "/owlbot/connect"
DEFAULT_CONNECT_TOKEN_TTL = timedelta(minutes=5)
DEFAULT_SESSION_TTL = timedelta(hours=1)
_CLEANUP_INTERVAL_SECONDS = 60.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ConnectToken:
    """A one-time connect token issued to a specific user."""

    token: str = field(repr=False)
    destination_path: str
    user: User
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """A browser session associated with an Owncast user snapshot."""

    session_id: str = field(repr=False)
    user: User
    created_at: datetime
    expires_at: datetime

    @property
    def is_authenticated(self) -> bool:
        """Whether the session user is authenticated with Owncast."""
        return self.user.is_authenticated

    @property
    def is_moderator(self) -> bool:
        """Whether the session user currently has moderator privileges."""
        return self.user.is_moderator


class ConnectTokenRedemptionError(Exception):
    """Raised when a connect token cannot be redeemed into a session."""


class SessionManager:
    """Issue connect tokens and manage expiring browser sessions."""

    def __init__(
        self,
        *,
        connect_token_ttl: timedelta = DEFAULT_CONNECT_TOKEN_TTL,
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
    ) -> None:
        """Initialize the in-memory session state manager."""
        self._connect_token_ttl = connect_token_ttl
        self._session_ttl = session_ttl
        self._connect_tokens_by_token: dict[str, ConnectToken] = {}
        self._connect_token_by_user_id: dict[str, str] = {}
        self._sessions_by_id: dict[str, BrowserSession] = {}
        self._session_ids_by_user_id: dict[str, set[str]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

    def issue_connect_token(
        self,
        destination_path: str = DEFAULT_CONNECT_DESTINATION_PATH,
        *,
        user: User,
    ) -> str:
        """Create a one-time token that redeems into an Owlbot session."""
        parts = urlsplit(destination_path)
        if parts.scheme or parts.netloc or not parts.path.startswith("/owlbot/"):
            msg = "session destination must stay within /owlbot"
            raise ValueError(msg)

        decoded_path = unquote(parts.path)
        if "\\" in decoded_path:
            msg = "session destination must use URL path separators"
            raise ValueError(msg)
        if any(segment in {".", ".."} for segment in decoded_path.split("/")):
            msg = "session destination must not contain dot segments"
            raise ValueError(msg)

        now = _utc_now()
        token = secrets.token_urlsafe(32)
        # Token strings are the primary key for pending connections. Collisions
        # are extraordinarily unlikely, but a duplicate would overwrite another
        # user's pending token in the canonical token index.
        while token in self._connect_tokens_by_token:
            token = secrets.token_urlsafe(32)
        self._store_connect_token(
            ConnectToken(
                token=token,
                destination_path=destination_path,
                user=user,
                issued_at=now,
                expires_at=now + self._connect_token_ttl,
            )
        )
        return token

    def get_connect_token(self, token: str) -> ConnectToken | None:
        """Return the tracked connect token, if it still exists."""
        return self._connect_tokens_by_token.get(token)

    def redeem_connect_token(
        self,
        token: str,
        *,
        replacing_session_id: str | None = None,
    ) -> BrowserSession:
        """Consume a connect token and return the resulting browser session."""
        connect_token = self._pop_connect_token(token)
        if connect_token is None:
            raise ConnectTokenRedemptionError("invalid connect token")
        if connect_token.expires_at <= _utc_now():
            raise ConnectTokenRedemptionError("connect token expired")

        return self.create_session(
            connect_token.user,
            replacing_session_id=replacing_session_id,
        )

    def create_session(
        self,
        user: User,
        *,
        replacing_session_id: str | None = None,
    ) -> BrowserSession:
        """Create a new browser session, optionally replacing an older one."""
        if replacing_session_id is not None:
            self.invalidate_session(replacing_session_id)

        now = _utc_now()
        session_id = secrets.token_urlsafe(32)
        # Session IDs are the primary key for browser sessions. Collisions are
        # extraordinarily unlikely, but a duplicate would overwrite another
        # browser's active session in the canonical session index.
        while session_id in self._sessions_by_id:
            session_id = secrets.token_urlsafe(32)
        session = BrowserSession(
            session_id=session_id,
            user=user,
            created_at=now,
            expires_at=now + self._session_ttl,
        )
        self._store_session(session)
        return session

    def get_session(self, session_id: str | None) -> BrowserSession | None:
        """Return a live session by id, evicting it first if expired."""
        if not session_id:
            return None

        session = self._sessions_by_id.get(session_id)
        if session is None:
            return None
        if session.expires_at <= _utc_now():
            self.invalidate_session(session_id)
            return None
        return session

    def invalidate_session(self, session_id: str | None) -> None:
        """Remove a session from all in-memory indexes."""
        if session_id is None:
            return

        session = self._sessions_by_id.pop(session_id, None)
        if session is None:
            return

        session_ids = self._session_ids_by_user_id.get(session.user.id)
        if session_ids is None:
            return
        session_ids.discard(session_id)
        if not session_ids:
            self._session_ids_by_user_id.pop(session.user.id, None)

    def refresh_user(self, user: User) -> None:
        """Update active sessions and pending connect tokens for the user id."""
        connect_token_id = self._connect_token_by_user_id.get(user.id)
        connect_token = (
            self._connect_tokens_by_token.get(connect_token_id)
            if connect_token_id is not None
            else None
        )
        if connect_token is not None:
            self._connect_tokens_by_token[connect_token.token] = replace(
                connect_token,
                user=user,
            )

        for session_id in list(self._session_ids_by_user_id.get(user.id, set())):
            session = self._sessions_by_id.get(session_id)
            if session is not None:
                self._sessions_by_id[session_id] = replace(session, user=user)

    def clear_expired(self) -> None:
        """Purge expired connect-token and browser-session state."""
        now = _utc_now()

        expired_tokens = [
            token
            for token, connect_token in self._connect_tokens_by_token.items()
            if connect_token.expires_at <= now
        ]
        for token in expired_tokens:
            self._pop_connect_token(token)

        expired_session_ids = [
            session_id
            for session_id, session in self._sessions_by_id.items()
            if session.expires_at <= now
        ]
        for session_id in expired_session_ids:
            self.invalidate_session(session_id)

    def start(self) -> None:
        """Start the background cleanup task if it is not already running."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(),
                name="Session Manager - Cleanup loop",
            )

    async def close(self) -> None:
        """Cancel and await the background cleanup task, if present."""
        if self._cleanup_task is None:
            return

        self._cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._cleanup_task
        self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            self.clear_expired()

    def _store_connect_token(self, connect_token: ConnectToken) -> None:
        """Store a connect token, replacing any pending token for its user."""
        previous_token = self._connect_token_by_user_id.get(connect_token.user.id)
        if previous_token is not None:
            self._connect_tokens_by_token.pop(previous_token, None)
        self._connect_tokens_by_token[connect_token.token] = connect_token
        self._connect_token_by_user_id[connect_token.user.id] = connect_token.token

    def _pop_connect_token(self, token: str) -> ConnectToken | None:
        """Remove a connect token from both token indexes."""
        connect_token = self._connect_tokens_by_token.pop(token, None)
        if connect_token is None:
            return None
        if self._connect_token_by_user_id.get(connect_token.user.id) == token:
            self._connect_token_by_user_id.pop(connect_token.user.id, None)
        return connect_token

    def _store_session(self, session: BrowserSession) -> None:
        """Store a browser session in all session indexes."""
        self._sessions_by_id[session.session_id] = session
        self._session_ids_by_user_id.setdefault(session.user.id, set()).add(
            session.session_id
        )
