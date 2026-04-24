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

"""Integration tests for ``OwncastClient`` against a live Owncast server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from owlbot.owncast_http import OwncastError

if TYPE_CHECKING:
    from owlbot.api.owncast_client import OwncastClient

pytestmark = pytest.mark.integration


def test_base_url_matches_server(
    owncast_client: OwncastClient, owncast_server: str
) -> None:
    """The client's base_url property reflects the server it was built for."""
    assert owncast_client.base_url == owncast_server


class TestRoundTrip:
    """End-to-end tests whose effect is verified via readback or return value.

    Either the call is a getter whose return value is the observation, or the
    call is a setter/mutation whose effect is read back within the same test.
    """

    async def test_send_message_returns_success_envelope(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_message posts a chat message and returns the success envelope."""
        result = await owncast_client.send_message("integration test message")
        assert isinstance(result, str)

    async def test_send_message_with_unsanitized_true(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_message accepts raw HTML when unsanitized=True."""
        result = await owncast_client.send_message("<b>bold</b>", unsanitized=True)
        assert isinstance(result, str)

    async def test_send_system_message_returns_success_envelope(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_system_message posts a system message."""
        result = await owncast_client.send_system_message("system notice")
        assert isinstance(result, str)

    async def test_send_action_returns_success_envelope(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_action posts an action message."""
        result = await owncast_client.send_action("does a test thing")
        assert isinstance(result, str)

    async def test_send_user_message_always_returns_400(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_user_message is deprecated in Owncast v0.2.5 and returns 400.

        Validates the deprecation contract documented on
        OwncastClient.send_user_message.
        """
        with pytest.raises(OwncastError) as exc_info:
            await owncast_client.send_user_message()
        assert exc_info.value.status == 400

    async def test_get_chat_history_is_empty_on_fresh_server(
        self, owncast_client: OwncastClient
    ) -> None:
        """get_chat_history returns [] on a fresh server with no sent messages."""
        assert await owncast_client.get_chat_history() == []

    async def test_get_connected_clients_is_empty_on_fresh_server(
        self, owncast_client: OwncastClient
    ) -> None:
        """get_connected_clients returns [] on a fresh server with no viewers."""
        assert await owncast_client.get_connected_clients() == []

    async def test_set_message_visibility_after_sending(
        self, owncast_client: OwncastClient
    ) -> None:
        """set_message_visibility accepts message IDs read from history.

        Sends a message, reads chat history to obtain its id, hides it, then
        shows it again. Validates the full write-read-mutate round trip.
        """
        await owncast_client.send_message("visibility-test message")
        history = await owncast_client.get_chat_history()
        assert history, "chat history should contain the just-sent message"
        message_id = str(history[-1]["id"])
        hide_result = await owncast_client.set_message_visibility(
            [message_id], visible=False
        )
        assert isinstance(hide_result, str)
        show_result = await owncast_client.set_message_visibility(
            [message_id], visible=True
        )
        assert isinstance(show_result, str)

    async def test_get_user_details_for_existing_user(
        self, owncast_client: OwncastClient
    ) -> None:
        """get_user_details returns a dict for a user extracted from chat history.

        The integration bot sends a message, then reads chat history to get
        its own user id, and looks it up. This is the only way to test the
        endpoint end-to-end since Owncast returns 404 for unknown user ids
        (so we can't distinguish "route missing" from "user missing" without
        a real id).
        """
        await owncast_client.send_message("user-details-test message")
        history = await owncast_client.get_chat_history()
        assert history
        user_id = str(history[-1]["user"]["id"])
        details = await owncast_client.get_user_details(user_id)
        assert isinstance(details, dict)

    async def test_get_status_returns_dict(self, owncast_client: OwncastClient) -> None:
        """get_status reports offline on a fresh server.

        The public ``/api/status`` endpoint does not expose ``viewerCount``
        (that is only surfaced to admin callers). ``serverTime`` is always
        populated but its value is a moving target, so only its presence
        is asserted.
        """
        status = await owncast_client.get_status()
        assert isinstance(status, dict)
        assert status["online"] is False
        assert "serverTime" in status


class TestSmoke:
    """Tests that only verify the wrapper reaches the right endpoint.

    The backing effect is either unobservable from the integration client
    (``get_status`` does not surface the configured stream title) or concerns
    clients that are not connected in a fresh install. Assertions validate
    that the request is routed correctly, not that any effect was applied.
    """

    async def test_send_system_message_to_client_accepts_unknown_id(
        self, owncast_client: OwncastClient
    ) -> None:
        """send_system_message_to_client returns success for unknown client IDs.

        A fresh Owncast has no connected clients. Owncast silently accepts
        system messages to unknown client IDs (the message is discarded).
        The assertion only validates the request path reaches the server and
        the envelope parses, not that anyone received the message.
        """
        result = await owncast_client.send_system_message_to_client(1, "hello")
        assert isinstance(result, str)

    async def test_set_stream_title_succeeds(
        self, owncast_client: OwncastClient
    ) -> None:
        """set_stream_title returns the success envelope.

        The /api/status endpoint does not surface streamTitle, so we only
        validate the envelope. Admin-side test covers reading the value back.
        """
        result = await owncast_client.set_stream_title("Integration Test Stream")
        assert isinstance(result, str)
