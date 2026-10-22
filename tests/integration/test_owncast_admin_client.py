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

"""Integration tests for ``OwncastAdminClient`` against a live Owncast server."""

from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING

import pytest

from owlbot.api.owncast_admin_client import (
    ExternalAction,
    OwncastAdminClient,
    SocialHandle,
    StreamKey,
    VideoVariant,
)
from owlbot.owncast_http import OwncastError

if TYPE_CHECKING:
    from owlbot.api.http_client import HttpClient
    from owlbot.api.owncast_client import OwncastClient

    from .conftest import AdminCredentials

pytestmark = pytest.mark.integration

# A 1x1 transparent PNG as a base64 data URL for logo/favicon upload tests.
TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def test_base_url_matches_server(
    owncast_admin_client: OwncastAdminClient,
    owncast_server: str,
) -> None:
    """The admin client's base_url property reflects the server it was built for."""
    assert owncast_admin_client.base_url == owncast_server


class TestRoundTrip:
    """End-to-end tests whose effect is verified via readback or return value.

    Setter tests follow a full-flow pattern: read the current value via
    ``get_server_config``, set a different value, read it back, and assert
    the new value differs from the before value and matches the requested
    value. Getter tests without a setter pair read an empty initial state
    and assert the return shape. ``set_admin_password`` is exercised by the
    session-bootstrap rotation in the ``admin_credentials`` fixture;
    ``test_default_credentials_are_rejected`` asserts the rotation took
    effect.
    """

    async def test_default_credentials_are_rejected(
        self,
        owncast_server: str,
        owncast_http: HttpClient,
        admin_credentials: AdminCredentials,  # noqa: ARG002  # side effect: rotates password
    ) -> None:
        """A client built with the default abc123 creds fails with 401.

        Proves the session bootstrap rotation actually changed the
        server-side password. Depends on ``admin_credentials`` so the
        rotation is a prerequisite under any test-selection order.
        """
        default_client = OwncastAdminClient(
            owncast_server,
            "admin",
            "abc123",
            owncast_http,
        )
        with pytest.raises(OwncastError) as exc_info:
            await default_client.get_status()
        assert exc_info.value.status == 401

    async def test_access_token_create_list_delete_round_trip(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Create an access token, confirm it appears, delete it, confirm it's gone."""
        name = f"test-token-{secrets.token_hex(4)}"
        before = await owncast_admin_client.get_access_tokens()
        assert name not in {entry.get("displayName") for entry in before}
        create_result = await owncast_admin_client.create_access_token(
            name, ["CAN_SEND_MESSAGES"]
        )
        assert isinstance(create_result, str)
        with_token = await owncast_admin_client.get_access_tokens()
        matching = [e for e in with_token if e.get("displayName") == name]
        assert len(matching) == 1, f"expected one matching token, got {matching}"
        assert with_token != before
        token_value = str(matching[0]["accessToken"])
        delete_result = await owncast_admin_client.delete_access_token(token_value)
        assert isinstance(delete_result, str)
        after = await owncast_admin_client.get_access_tokens()
        assert name not in {entry.get("displayName") for entry in after}
        assert after != with_token

    async def test_get_status_returns_dict(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_status reports offline with no viewers on a fresh server."""
        status = await owncast_admin_client.get_status()
        assert isinstance(status, dict)
        assert status["online"] is False
        assert status["viewerCount"] == 0

    async def test_get_hardware_stats_returns_dict(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_hardware_stats returns a dict with CPU/mem/disk data."""
        assert isinstance(await owncast_admin_client.get_hardware_stats(), dict)

    async def test_get_logs_returns_list(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_logs returns a list of log entries."""
        assert isinstance(await owncast_admin_client.get_logs(), list)

    async def test_get_warnings_returns_list(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_warnings returns a list of log entries."""
        assert isinstance(await owncast_admin_client.get_warnings(), list)

    async def test_get_active_viewers_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_active_viewers returns [] on a fresh server."""
        assert await owncast_admin_client.get_active_viewers() == []

    async def test_get_viewers_over_time_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_viewers_over_time returns [] for a fresh server with no history."""
        assert await owncast_admin_client.get_viewers_over_time(0) == []

    async def test_get_playback_metrics_returns_dict(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_playback_metrics returns a metrics dict."""
        assert isinstance(await owncast_admin_client.get_playback_metrics(), dict)

    async def test_get_connected_chat_clients_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_connected_chat_clients returns [] on a fresh server."""
        assert await owncast_admin_client.get_connected_chat_clients() == []

    async def test_get_chat_messages_reflects_sent_message(
        self,
        owncast_admin_client: OwncastAdminClient,
        owncast_client: OwncastClient,
    ) -> None:
        """Admin get_chat_messages surfaces messages sent via the integration client.

        Owncast renders messages as Markdown, so the returned body is wrapped
        in a ``<p>`` tag. The assertion matches on substring presence rather
        than exact equality.
        """
        assert await owncast_admin_client.get_chat_messages() == []
        body = f"admin-view-test-{secrets.token_hex(4)}"
        await owncast_client.send_message(body)
        after = await owncast_admin_client.get_chat_messages()
        assert any(body in str(entry.get("body", "")) for entry in after), (
            f"sent message body {body!r} not found in admin view {after}"
        )

    async def test_get_disabled_users_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_disabled_users returns [] on a fresh server.

        Owncast's ``get_disabled_users`` filters bot-authored accounts out of
        the returned list, and the integration test harness only has a bot
        identity available (the admin access token that would be needed to
        create a non-bot user). A real round trip cannot be demonstrated
        from this harness, so ``set_user_enabled`` is covered separately in
        ``TestSmoke``.
        """
        assert await owncast_admin_client.get_disabled_users() == []

    async def test_user_moderator_grant_revoke_round_trip(
        self,
        owncast_admin_client: OwncastAdminClient,
        owncast_client: OwncastClient,
    ) -> None:
        """Grant moderator status to a real user, verify via get_moderators, revoke."""
        await owncast_client.send_message(f"mod-test-{secrets.token_hex(4)}")
        history = await owncast_client.get_chat_history()
        assert history
        user_id = str(history[-1]["user"]["id"])
        assert await owncast_admin_client.get_moderators() == []
        assert isinstance(
            await owncast_admin_client.set_user_moderator(user_id, is_mod=True), str
        )
        mods = await owncast_admin_client.get_moderators()
        assert user_id in {entry.get("id") for entry in mods}, (
            f"user {user_id} not in moderators list {mods}"
        )
        assert isinstance(
            await owncast_admin_client.set_user_moderator(user_id, is_mod=False), str
        )
        after = await owncast_admin_client.get_moderators()
        assert user_id not in {entry.get("id") for entry in after}

    async def test_ban_list_unban_round_trip(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Ban an IP, verify it appears in the list, unban, verify it's gone."""
        ip = "192.0.2.1"  # RFC 5737 documentation address
        before = await owncast_admin_client.get_ip_address_bans()
        assert ip not in {entry.get("ipAddress") for entry in before}
        assert isinstance(await owncast_admin_client.ban_ip_address(ip), str)
        banned = await owncast_admin_client.get_ip_address_bans()
        assert ip in {entry.get("ipAddress") for entry in banned}, (
            f"banned IP {ip} not found in {banned}"
        )
        assert banned != before
        assert isinstance(await owncast_admin_client.unban_ip_address(ip), str)
        after = await owncast_admin_client.get_ip_address_bans()
        assert ip not in {entry.get("ipAddress") for entry in after}
        assert after != banned

    async def test_set_chat_disabled(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_disabled toggles chatDisabled in server config."""
        before = (await owncast_admin_client.get_server_config())["chatDisabled"]
        await owncast_admin_client.set_chat_disabled(disabled=not before)
        after = (await owncast_admin_client.get_server_config())["chatDisabled"]
        assert after is not before

    async def test_set_chat_join_messages_enabled(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_join_messages_enabled toggles chatJoinMessagesEnabled."""
        before = (await owncast_admin_client.get_server_config())[
            "chatJoinMessagesEnabled"
        ]
        await owncast_admin_client.set_chat_join_messages_enabled(enabled=not before)
        after = (await owncast_admin_client.get_server_config())[
            "chatJoinMessagesEnabled"
        ]
        assert after is not before

    async def test_set_chat_established_mode(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_established_mode toggles chatEstablishedUserMode."""
        before = (await owncast_admin_client.get_server_config())[
            "chatEstablishedUserMode"
        ]
        await owncast_admin_client.set_chat_established_mode(enabled=not before)
        after = (await owncast_admin_client.get_server_config())[
            "chatEstablishedUserMode"
        ]
        assert after is not before

    async def test_set_forbidden_usernames(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_forbidden_usernames replaces forbiddenUsernames in server config."""
        before = (await owncast_admin_client.get_server_config())["forbiddenUsernames"]
        new_names = ["integration-forbidden-1", "integration-forbidden-2"]
        assert new_names != before
        await owncast_admin_client.set_forbidden_usernames(new_names)
        after = (await owncast_admin_client.get_server_config())["forbiddenUsernames"]
        assert after == new_names
        assert after != before

    async def test_set_suggested_usernames(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_suggested_usernames replaces suggestedUsernames in server config."""
        before = (await owncast_admin_client.get_server_config())["suggestedUsernames"]
        new_names = ["integration-suggested-1", "integration-suggested-2"]
        assert new_names != before
        await owncast_admin_client.set_suggested_usernames(new_names)
        after = (await owncast_admin_client.get_server_config())["suggestedUsernames"]
        assert after == new_names
        assert after != before

    async def test_set_chat_spam_protection(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_spam_protection toggles chatSpamProtectionEnabled."""
        before = (await owncast_admin_client.get_server_config())[
            "chatSpamProtectionEnabled"
        ]
        await owncast_admin_client.set_chat_spam_protection(enabled=not before)
        after = (await owncast_admin_client.get_server_config())[
            "chatSpamProtectionEnabled"
        ]
        assert after is not before

    async def test_set_chat_slur_filter(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_slur_filter toggles chatSlurFilterEnabled."""
        before = (await owncast_admin_client.get_server_config())[
            "chatSlurFilterEnabled"
        ]
        await owncast_admin_client.set_chat_slur_filter(enabled=not before)
        after = (await owncast_admin_client.get_server_config())[
            "chatSlurFilterEnabled"
        ]
        assert after is not before

    async def test_set_chat_require_authentication(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_chat_require_authentication toggles chatRequireAuthentication."""
        before = (await owncast_admin_client.get_server_config())[
            "chatRequireAuthentication"
        ]
        await owncast_admin_client.set_chat_require_authentication(required=not before)
        after = (await owncast_admin_client.get_server_config())[
            "chatRequireAuthentication"
        ]
        assert after is not before

    async def test_set_server_name(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_server_name replaces instanceDetails.name in server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "name"
        ]
        new_name = f"Integration Test Server {secrets.token_hex(4)}"
        assert new_name != before
        await owncast_admin_client.set_server_name(new_name)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "name"
        ]
        assert after == new_name
        assert after != before

    async def test_set_server_summary(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_server_summary replaces instanceDetails.summary in server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "summary"
        ]
        new_summary = f"For integration tests {secrets.token_hex(4)}."
        assert new_summary != before
        await owncast_admin_client.set_server_summary(new_summary)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "summary"
        ]
        assert after == new_summary
        assert after != before

    async def test_set_stream_title_reflected_in_server_config(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_stream_title replaces instanceDetails.streamTitle in server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "streamTitle"
        ]
        new_title = f"Admin Integration Title {secrets.token_hex(4)}"
        assert new_title != before
        await owncast_admin_client.set_stream_title(new_title)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "streamTitle"
        ]
        assert after == new_title
        assert after != before

    async def test_set_offline_message(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_offline_message replaces instanceDetails.offlineMessage."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "offlineMessage"
        ]
        new_message = f"Be right back {secrets.token_hex(4)}."
        assert new_message != before
        await owncast_admin_client.set_offline_message(new_message)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "offlineMessage"
        ]
        assert after == new_message
        assert after != before

    async def test_set_welcome_message(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_welcome_message replaces instanceDetails.welcomeMessage."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "welcomeMessage"
        ]
        new_message = f"Welcome to integration tests {secrets.token_hex(4)}!"
        assert new_message != before
        await owncast_admin_client.set_welcome_message(new_message)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "welcomeMessage"
        ]
        assert after == new_message
        assert after != before

    async def test_set_tags(self, owncast_admin_client: OwncastAdminClient) -> None:
        """set_tags replaces instanceDetails.tags in server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "tags"
        ]
        new_tags = ["gaming", "music", "integration-test"]
        assert new_tags != before
        await owncast_admin_client.set_tags(new_tags)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "tags"
        ]
        assert after == new_tags
        assert after != before

    async def test_set_social_handles(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_social_handles replaces instanceDetails.socialHandles."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "socialHandles"
        ]
        expected = [{"platform": "mastodon", "url": "https://example.test/@me"}]
        assert expected != before
        await owncast_admin_client.set_social_handles(
            [SocialHandle(platform="mastodon", url="https://example.test/@me")],
        )
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "socialHandles"
        ]
        assert after == expected
        assert after != before

    async def test_set_page_content(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_page_content replaces instanceDetails.extraPageContent."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "extraPageContent"
        ]
        new_content = f"# Integration Test Page {secrets.token_hex(4)}"
        assert new_content != before
        await owncast_admin_client.set_page_content(new_content)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "extraPageContent"
        ]
        assert after == new_content
        assert after != before

    async def test_set_nsfw(self, owncast_admin_client: OwncastAdminClient) -> None:
        """set_nsfw toggles instanceDetails.nsfw in server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "nsfw"
        ]
        await owncast_admin_client.set_nsfw(nsfw=not before)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "nsfw"
        ]
        assert after is not before

    async def test_set_logo_accepts_data_url(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_logo uploads a data URL and server config surfaces the stored file.

        Owncast normalizes the stored filename (e.g. ``logo.png``) based on
        the uploaded content type, so ``before`` and ``after`` can match
        even when the bytes change. Only the truthiness of the stored
        filename is asserted.
        """
        await owncast_admin_client.set_logo(TINY_PNG_DATA_URL)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "logo"
        ]
        assert after

    async def test_favicon_set_and_reset_round_trip(
        self,
        owncast_admin_client: OwncastAdminClient,
        owncast_server: str,
        owncast_http: HttpClient,
    ) -> None:
        """set_favicon then reset_favicon: /favicon.ico serves and restores bytes."""
        png_bytes = base64.b64decode(TINY_PNG_DATA_URL.partition(",")[2])
        async with owncast_http.get(f"{owncast_server}/favicon.ico") as resp:
            before_bytes = await resp.read() if resp.status == 200 else b""
        assert isinstance(
            await owncast_admin_client.set_favicon(TINY_PNG_DATA_URL), str
        )
        async with owncast_http.get(f"{owncast_server}/favicon.ico") as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "image/png"
            uploaded = await resp.read()
        assert uploaded == png_bytes
        assert uploaded != before_bytes
        assert isinstance(await owncast_admin_client.reset_favicon(), str)
        async with owncast_http.get(f"{owncast_server}/favicon.ico") as resp:
            assert resp.status == 200
            restored = await resp.read()
        assert restored == before_bytes
        assert restored != uploaded

    async def test_set_custom_styles(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_custom_styles replaces instanceDetails.customStyles."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "customStyles"
        ]
        new_css = f"body {{ color: #123456; }} /* {secrets.token_hex(4)} */"
        assert new_css != before
        await owncast_admin_client.set_custom_styles(new_css)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "customStyles"
        ]
        assert after == new_css
        assert after != before

    async def test_set_custom_javascript(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_custom_javascript replaces instanceDetails.customJavascript."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "customJavascript"
        ]
        new_js = f"console.log('integration-test-{secrets.token_hex(4)}');"
        assert new_js != before
        await owncast_admin_client.set_custom_javascript(new_js)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "customJavascript"
        ]
        assert after == new_js
        assert after != before

    async def test_set_color_variables(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_color_variables replaces instanceDetails.appearanceVariables."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "appearanceVariables"
        ]
        new_variables = {"primary": "#ff0000", "secondary": "#00ff00"}
        assert new_variables != before
        await owncast_admin_client.set_color_variables(new_variables)
        after = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "appearanceVariables"
        ]
        assert after == new_variables
        assert after != before

    async def test_set_hide_viewer_count(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_hide_viewer_count toggles hideViewerCount in server config."""
        before = (await owncast_admin_client.get_server_config())["hideViewerCount"]
        await owncast_admin_client.set_hide_viewer_count(hide=not before)
        after = (await owncast_admin_client.get_server_config())["hideViewerCount"]
        assert after is not before

    async def test_set_disable_search_indexing(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_disable_search_indexing toggles disableSearchIndexing."""
        before = (await owncast_admin_client.get_server_config())[
            "disableSearchIndexing"
        ]
        await owncast_admin_client.set_disable_search_indexing(disabled=not before)
        after = (await owncast_admin_client.get_server_config())[
            "disableSearchIndexing"
        ]
        assert after is not before

    async def test_set_external_actions(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_external_actions replaces externalActions in server config."""
        before = (await owncast_admin_client.get_server_config())["externalActions"]
        await owncast_admin_client.set_external_actions(
            [
                ExternalAction(
                    url="https://example.test",
                    title="Example",
                    description="A test external action",
                ),
            ],
        )
        after = (await owncast_admin_client.get_server_config())["externalActions"]
        assert after != before
        assert isinstance(after, list)
        assert len(after) == 1
        assert after[0]["url"] == "https://example.test"
        assert after[0]["title"] == "Example"
        assert after[0]["description"] == "A test external action"

    async def test_set_video_codec(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_video_codec replaces videoCodec in server config."""
        before = (await owncast_admin_client.get_server_config())["videoCodec"]
        new_codec = "libx264"
        if new_codec == before:
            new_codec = "h264_vaapi"
        await owncast_admin_client.set_video_codec(new_codec)
        after = (await owncast_admin_client.get_server_config())["videoCodec"]
        assert after == new_codec
        assert after != before

    async def test_set_stream_latency(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_stream_latency replaces videoSettings.latencyLevel."""
        before = (await owncast_admin_client.get_server_config())["videoSettings"][
            "latencyLevel"
        ]
        new_level = 1 if before != 1 else 4
        await owncast_admin_client.set_stream_latency(new_level)
        after = (await owncast_admin_client.get_server_config())["videoSettings"][
            "latencyLevel"
        ]
        assert after == new_level
        assert after != before

    async def test_set_video_variants(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_video_variants replaces videoSettings.videoQualityVariants."""
        before = (await owncast_admin_client.get_server_config())["videoSettings"][
            "videoQualityVariants"
        ]
        await owncast_admin_client.set_video_variants(
            [
                VideoVariant(
                    name="720p",
                    video_bitrate=2500,
                    audio_bitrate=128,
                    scaled_width=1280,
                    scaled_height=720,
                    framerate=30,
                    cpu_usage_level=3,
                ),
            ],
        )
        after = (await owncast_admin_client.get_server_config())["videoSettings"][
            "videoQualityVariants"
        ]
        assert after != before
        assert isinstance(after, list)
        assert len(after) == 1
        assert after[0]["videoBitrate"] == 2500
        assert after[0]["framerate"] == 30
        assert after[0]["cpuUsageLevel"] == 3

    async def test_set_ffmpeg_path(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_ffmpeg_path replaces ffmpegPath in server config.

        Owncast validates that the path exists on disk, so a random path
        would be rejected. ``/bin/sh`` is guaranteed to exist on the
        supported platforms (Linux, macOS).
        """
        before = (await owncast_admin_client.get_server_config())["ffmpegPath"]
        new_path = "/bin/sh"
        assert new_path != before
        await owncast_admin_client.set_ffmpeg_path(new_path)
        after = (await owncast_admin_client.get_server_config())["ffmpegPath"]
        assert after == new_path
        assert after != before

    async def test_set_stream_keys(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_stream_keys replaces streamKeys in server config."""
        before = (await owncast_admin_client.get_server_config())["streamKeys"]
        new_entry = {
            "key": f"integration-test-key-{secrets.token_hex(4)}",
            "comment": "tests",
        }
        assert new_entry not in before
        await owncast_admin_client.set_stream_keys(
            [StreamKey(key=new_entry["key"], comment=new_entry["comment"])],
        )
        after = (await owncast_admin_client.get_server_config())["streamKeys"]
        assert new_entry in after
        assert after != before

    async def test_set_video_serving_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_video_serving_endpoint replaces videoServingEndpoint."""
        before = (await owncast_admin_client.get_server_config())[
            "videoServingEndpoint"
        ]
        new_endpoint = f"https://cdn.example.test/owncast-{secrets.token_hex(4)}"
        assert new_endpoint != before
        await owncast_admin_client.set_video_serving_endpoint(new_endpoint)
        after = (await owncast_admin_client.get_server_config())["videoServingEndpoint"]
        assert after == new_endpoint
        assert after != before

    async def test_set_s3_config(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_s3_config is reflected in server config."""
        before = (await owncast_admin_client.get_server_config())["s3"]
        new_endpoint = f"https://s3.example.test/{secrets.token_hex(4)}"
        new_bucket = f"integration-bucket-{secrets.token_hex(4)}"
        new_region = "us-west-1"
        await owncast_admin_client.set_s3_config(
            enabled=True,
            endpoint=new_endpoint,
            access_key="integration-access-key",
            secret=secrets.token_urlsafe(16),
            bucket=new_bucket,
            region=new_region,
        )
        after = (await owncast_admin_client.get_server_config())["s3"]
        assert after != before
        assert after["enabled"] is True
        # Owncast surfaces the non-sensitive S3 fields; secret is not echoed back.
        assert after.get("endpoint") == new_endpoint
        assert after.get("bucket") == new_bucket
        assert after.get("region") == new_region

    async def test_set_federation_enabled(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_enabled toggles federation.enabled."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "enabled"
        ]
        await owncast_admin_client.set_federation_enabled(enabled=not before)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "enabled"
        ]
        assert after is not before

    async def test_set_federation_activity_private(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_activity_private toggles federation.isPrivate."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "isPrivate"
        ]
        await owncast_admin_client.set_federation_activity_private(private=not before)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "isPrivate"
        ]
        assert after is not before

    async def test_set_federation_show_engagement(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_show_engagement toggles federation.showEngagement."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "showEngagement"
        ]
        await owncast_admin_client.set_federation_show_engagement(enabled=not before)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "showEngagement"
        ]
        assert after is not before

    async def test_set_federation_username(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_username replaces federation.username."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "username"
        ]
        new_username = f"integration-stream-{secrets.token_hex(4)}"
        assert new_username != before
        await owncast_admin_client.set_federation_username(new_username)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "username"
        ]
        assert after == new_username
        assert after != before

    async def test_set_federation_go_live_message(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_go_live_message replaces federation.goLiveMessage."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "goLiveMessage"
        ]
        new_message = f"Integration test: going live {secrets.token_hex(4)}!"
        assert new_message != before
        await owncast_admin_client.set_federation_go_live_message(new_message)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "goLiveMessage"
        ]
        assert after == new_message
        assert after != before

    async def test_set_federation_blocked_domains(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_federation_blocked_domains replaces federation.blockedDomains."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "blockedDomains"
        ]
        new_domains = ["blocked.example.test", "another.example.test"]
        assert new_domains != before
        await owncast_admin_client.set_federation_blocked_domains(new_domains)
        after = (await owncast_admin_client.get_server_config())["federation"][
            "blockedDomains"
        ]
        assert after == new_domains
        assert after != before

    async def test_get_federated_actions_returns_dict(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_federated_actions returns a paginated dict (empty initially)."""
        assert isinstance(await owncast_admin_client.get_federated_actions(), dict)

    async def test_get_followers_returns_dict(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_followers returns a paginated dict (empty initially)."""
        assert isinstance(await owncast_admin_client.get_followers(), dict)

    async def test_get_pending_follow_requests_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_pending_follow_requests returns [] on a fresh server."""
        assert await owncast_admin_client.get_pending_follow_requests() == []

    async def test_get_blocked_followers_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_blocked_followers returns [] on a fresh server."""
        assert await owncast_admin_client.get_blocked_followers() == []

    async def test_set_discord_notifications(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_discord_notifications toggles notifications.discord.enabled.

        get_server_config surfaces ``notifications.discord.enabled`` but not
        the webhook URL (likely because it's treated as a secret), so this
        test only observes the toggle.
        """
        before = (await owncast_admin_client.get_server_config())["notifications"][
            "discord"
        ]["enabled"]
        await owncast_admin_client.set_discord_notifications(
            enabled=not before,
            webhook="https://discord.example.test/webhook",
            go_live_message="Integration live!",
        )
        after = (await owncast_admin_client.get_server_config())["notifications"][
            "discord"
        ]["enabled"]
        assert after is not before

    async def test_set_browser_notifications(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_browser_notifications toggles enabled and replaces goLiveMessage."""
        before = (await owncast_admin_client.get_server_config())["notifications"][
            "browser"
        ]
        new_message = f"Integration browser live message {secrets.token_hex(4)}"
        assert new_message != before.get("goLiveMessage")
        await owncast_admin_client.set_browser_notifications(
            enabled=not before["enabled"], go_live_message=new_message
        )
        after = (await owncast_admin_client.get_server_config())["notifications"][
            "browser"
        ]
        assert after["enabled"] is not before["enabled"]
        assert after["goLiveMessage"] == new_message
        assert after != before

    async def test_webhook_create_list_delete_round_trip(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Create a webhook, confirm it appears, delete it, confirm it's gone."""
        url = f"https://example.test/webhook-{secrets.token_hex(4)}"
        before = await owncast_admin_client.get_webhooks()
        assert url not in {entry.get("url") for entry in before}
        assert isinstance(
            await owncast_admin_client.create_webhook(
                url, ["CHAT"], secret=secrets.token_urlsafe(32)
            ),
            str,
        )
        with_webhook = await owncast_admin_client.get_webhooks()
        matching = [e for e in with_webhook if e.get("url") == url]
        assert len(matching) == 1, f"expected one match, got {matching}"
        assert with_webhook != before
        webhook_id = int(matching[0]["id"])
        assert isinstance(await owncast_admin_client.delete_webhook(webhook_id), str)
        after = await owncast_admin_client.get_webhooks()
        assert url not in {entry.get("url") for entry in after}
        assert after != with_webhook

    async def test_set_directory_enabled(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_directory_enabled toggles yp.enabled in server config."""
        before = (await owncast_admin_client.get_server_config())["yp"]["enabled"]
        await owncast_admin_client.set_directory_enabled(enabled=not before)
        after = (await owncast_admin_client.get_server_config())["yp"]["enabled"]
        assert after is not before

    async def test_get_playback_clients_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """A fresh server has no active video playback clients."""
        assert await owncast_admin_client.get_playback_clients() == []

    async def test_get_users_supports_filters(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Paginated user listing accepts all documented filters."""
        users = await owncast_admin_client.get_users(
            0, 5, search="nonexistent-user", status="all", sort="asc"
        )
        assert users == {"total": 0, "results": []}

    async def test_user_listing_and_deletion_round_trip(
        self,
        owncast_admin_client: OwncastAdminClient,
        owncast_server: str,
        owncast_http: HttpClient,
    ) -> None:
        """Register a user, find it through the admin listing, then delete it."""
        name = f"delete-test-{secrets.token_hex(4)}"
        async with owncast_http.post(
            f"{owncast_server}/api/chat/register", json={"displayName": name}
        ) as response:
            assert response.status == 200
            user = await response.json()
        users = await owncast_admin_client.get_users(search=name, status="active")
        assert users["total"] == 1
        assert users["results"][0]["id"] == user["id"]
        assert isinstance(await owncast_admin_client.delete_user(user["id"]), str)
        assert await owncast_admin_client.get_users(search=name) == {
            "total": 0,
            "results": [],
        }

    async def test_get_directory_followers_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """A new server is not featured by any directory."""
        assert await owncast_admin_client.get_directory_followers() == []

    async def test_get_federated_servers_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """A new server has no outgoing federated server follows."""
        assert await owncast_admin_client.get_federated_servers() == {"servers": []}

    async def test_get_feature_requests_is_empty_on_fresh_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """A new server has no pending directory feature requests."""
        assert await owncast_admin_client.get_feature_requests() == {"requests": []}

    @pytest.mark.parametrize("mode", ["off", "always", "sound-only"])
    async def test_set_autoplay(
        self, owncast_admin_client: OwncastAdminClient, mode: str
    ) -> None:
        """Every documented autoplay mode round-trips through server config."""
        before = (await owncast_admin_client.get_server_config())["instanceDetails"][
            "autoplay"
        ]
        try:
            await owncast_admin_client.set_autoplay(mode)
            after = (await owncast_admin_client.get_server_config())["instanceDetails"][
                "autoplay"
            ]
            assert after == mode
        finally:
            await owncast_admin_client.set_autoplay(before)

    async def test_set_federation_enable_quotes(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Quote support is reflected in federation config."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "enableQuotes"
        ]
        try:
            await owncast_admin_client.set_federation_enable_quotes(enabled=not before)
            after = (await owncast_admin_client.get_server_config())["federation"][
                "enableQuotes"
            ]
            assert after is not before
        finally:
            await owncast_admin_client.set_federation_enable_quotes(enabled=before)

    async def test_set_federation_hide_followers(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Follower-tab visibility is reflected in federation config."""
        before = (await owncast_admin_client.get_server_config())["federation"][
            "hideFollowersTab"
        ]
        try:
            await owncast_admin_client.set_federation_hide_followers(hide=not before)
            after = (await owncast_admin_client.get_server_config())["federation"][
                "hideFollowersTab"
            ]
            assert after is not before
        finally:
            await owncast_admin_client.set_federation_hide_followers(hide=before)


class TestSmoke:
    """Tests that only verify the wrapper reaches the right endpoint.

    The backing effect is either unobservable (``get_server_config`` does not
    surface the value, or the setting applies only at Owncast startup) or
    requires a backend that is not present in a fresh install (Prometheus
    proxy target, federation peers, connected inbound stream). Assertions
    validate that the request is routed correctly (i.e. does not return 404)
    rather than that the intended effect was applied.
    """

    async def test_disconnect_stream_on_idle_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """disconnect_stream returns a response even when no stream is active.

        A fresh Owncast has no connected stream. The endpoint is idempotent:
        Owncast returns a success-style dict or an informational error. Either
        way, the route is validated.
        """
        try:
            result = await owncast_admin_client.disconnect_stream()
        except OwncastError as exc:
            if exc.status == 404:
                raise
            return
        assert isinstance(result, dict)

    async def test_get_prometheus_metrics_returns_text(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """get_prometheus_metrics returns a string (possibly empty)."""
        try:
            result = await owncast_admin_client.get_prometheus_metrics()
        except OwncastError as exc:
            if exc.status == 404:
                raise
            return
        assert isinstance(result, str)

    async def test_post_prometheus_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """post_prometheus reaches the proxy route (not 404)."""
        try:
            await owncast_admin_client.post_prometheus({"ping": True})
        except OwncastError as exc:
            if exc.status == 404:
                raise

    async def test_put_prometheus_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """put_prometheus reaches the proxy route (not 404)."""
        try:
            await owncast_admin_client.put_prometheus({"ping": True})
        except OwncastError as exc:
            if exc.status == 404:
                raise

    async def test_delete_prometheus_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """delete_prometheus reaches the proxy route (not 404)."""
        try:
            await owncast_admin_client.delete_prometheus()
        except OwncastError as exc:
            if exc.status == 404:
                raise

    async def test_set_message_visibility_with_unknown_ids_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_message_visibility accepts unknown IDs without 404ing.

        Owncast's admin visibility endpoint is lenient about unknown IDs and
        returns a success-shape envelope. The assertion validates the request
        path, not the value.
        """
        try:
            result = await owncast_admin_client.set_message_visibility(
                ["000000000"], visible=False
            )
        except OwncastError as exc:
            if exc.status == 404:
                raise
            return
        assert isinstance(result, str)

    async def test_set_user_enabled_for_unknown_user_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_user_enabled reaches the endpoint for a synthetic user id.

        A real round trip is infeasible from this harness because Owncast
        filters bot users out of ``get_disabled_users``, and the integration
        token only ever produces bot identities. Asserts either success
        envelope or non-404 error.
        """
        try:
            result = await owncast_admin_client.set_user_enabled(
                "000000000", enabled=False
            )
        except OwncastError as exc:
            if exc.status == 404:
                raise
            return
        assert isinstance(result, str)

    async def test_set_server_url(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_server_url returns the success envelope.

        get_server_config does not surface the configured server URL (it is
        stored but only used for federation outbound URLs), so this test
        asserts only that the call reaches the server without erroring.
        """
        assert isinstance(
            await owncast_admin_client.set_server_url("https://example.test"), str
        )

    async def test_set_web_server_port(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_web_server_port returns the success envelope.

        Owncast only applies web-server bind changes at startup and
        get_server_config surfaces the live bind value, not the saved
        next-startup value, so a direct readback cannot confirm the write.
        Re-pins to the live bind port for safety.
        """
        config = await owncast_admin_client.get_server_config()
        assert isinstance(
            await owncast_admin_client.set_web_server_port(
                int(config["webServerPort"])
            ),
            str,
        )

    async def test_set_web_server_ip(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_web_server_ip returns the success envelope.

        Like set_web_server_port, this only takes effect at startup and
        get_server_config returns the live bind IP, not the saved value.
        """
        assert isinstance(
            await owncast_admin_client.set_web_server_ip("127.0.0.1"), str
        )

    async def test_set_rtmp_port(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_rtmp_port returns the success envelope.

        Like set_web_server_port, this only takes effect at startup.
        Re-pins to the live bind port for safety.
        """
        config = await owncast_admin_client.get_server_config()
        assert isinstance(
            await owncast_admin_client.set_rtmp_port(int(config["rtmpServerPort"])),
            str,
        )

    async def test_set_socket_host_override(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """set_socket_host_override returns the success envelope.

        get_server_config does not surface the socket host override value, so
        this test asserts only that the call reaches the server without error.
        """
        assert isinstance(await owncast_admin_client.set_socket_host_override(""), str)

    async def test_send_federated_message_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """send_federated_message reaches the route even without federation peers.

        Federation is disabled by default; Owncast may return an error. The
        assertion validates that the route exists (not a 404), not delivery.
        """
        try:
            result = await owncast_admin_client.send_federated_message("hello")
        except OwncastError as exc:
            if exc.status == 404:
                raise
            return
        assert isinstance(result, str)

    async def test_approve_follower_for_unknown_actor_reaches_endpoint(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """approve_follower reaches the route for a synthetic actor IRI.

        No pending follow requests exist. Assertion validates the request path
        (non-404), not approval success.
        """
        try:
            await owncast_admin_client.approve_follower(
                "https://example.test/actors/nobody", approved=True
            )
        except OwncastError as exc:
            if exc.status == 404:
                raise

    async def test_upload_and_delete_emoji(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Upload an emoji, then delete it.

        Owncast's upload endpoint expects a full data URL (it splits on the
        comma separator); passing raw base64 fails with "error splitting
        base64 image data". Neither call is readback-verified, so this
        exercises only the wrappers and routes.
        """
        name = f"test-emoji-{secrets.token_hex(4)}"
        data_url = TINY_PNG_DATA_URL
        assert isinstance(await owncast_admin_client.upload_emoji(name, data_url), str)
        assert isinstance(await owncast_admin_client.delete_emoji(name), str)

    async def test_reset_yp_registration(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """reset_yp_registration returns a response dict."""
        assert isinstance(await owncast_admin_client.reset_yp_registration(), dict)

    async def test_set_rtmp_bind_address(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """A loopback RTMP bind address is accepted without restarting ingest."""
        assert isinstance(
            await owncast_admin_client.set_rtmp_bind_address("127.0.0.1"), str
        )

    async def test_delete_unknown_user(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Deleting a missing user reaches the new admin route."""
        with pytest.raises(OwncastError, match="not found") as exc_info:
            await owncast_admin_client.delete_user("nonexistent-user")
        assert exc_info.value.status == 400

    async def test_remove_unknown_follower(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Removing an unknown follower reaches the new admin route."""
        with pytest.raises(OwncastError) as exc_info:
            await owncast_admin_client.remove_follower(
                "https://example.test/actors/nobody"
            )
        assert exc_info.value.status == 400

    async def test_add_invalid_federated_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Invalid server URLs fail validation at the federated server route."""
        with pytest.raises(OwncastError) as exc_info:
            await owncast_admin_client.add_federated_server("not-a-url")
        assert exc_info.value.status == 400

    async def test_remove_unknown_federated_server(
        self, owncast_admin_client: OwncastAdminClient
    ) -> None:
        """Removing a missing federated server is idempotent in Owncast v0.3.0."""
        assert isinstance(
            await owncast_admin_client.remove_federated_server(999999), str
        )
