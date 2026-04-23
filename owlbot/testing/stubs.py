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

"""Recording stubs for the Owncast HTTP clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from owlbot.api.owncast_admin_client import (
        ExternalAction,
        OwncastAdminClient,
        SocialHandle,
        StreamKey,
        VideoVariant,
    )
    from owlbot.api.owncast_client import OwncastClient

    # Stubs inherit from the real classes at type-check time so that code
    # passing a stub where the real instance is expected (notably plugin.py)
    # type-checks. At runtime the base is ``object`` to avoid the real
    # ``__init__`` requiring live HTTP/config dependencies.
    _ClientBase = OwncastClient
    _AdminBase = OwncastAdminClient
else:
    _ClientBase = object
    _AdminBase = object


class RecordedCall(NamedTuple):
    """A single recorded method invocation on a recording stub.

    Compares equal to a plain ``(name, kwargs)`` tuple, so assertions written
    against tuple literals continue to work.
    """

    name: str
    kwargs: dict[str, Any]


class RecordingOwncastClient(_ClientBase):
    """Stub mirroring :class:`~owlbot.api.owncast_client.OwncastClient`.

    Each public async method records ``(method_name, kwargs_dict)`` to
    :attr:`calls` and returns an empty default of the declared return type.
    """

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        """Initialize with an empty call log and the given base URL."""
        self.calls: list[RecordedCall] = []
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        """The Owncast server base URL passed at construction."""
        return self._base_url

    async def get_status(self) -> dict[str, Any]:
        """Get the public server status."""
        self.calls.append(RecordedCall("get_status", {}))
        return {}

    async def send_message(self, body: str, *, unsanitized: bool = False) -> str:
        """Send a chat message visible to all viewers."""
        self.calls.append(
            RecordedCall("send_message", {"body": body, "unsanitized": unsanitized})
        )
        return ""

    async def send_system_message(self, body: str, *, unsanitized: bool = False) -> str:
        """Send a system message visible to all viewers."""
        self.calls.append(
            RecordedCall(
                "send_system_message", {"body": body, "unsanitized": unsanitized}
            )
        )
        return ""

    async def send_action(self, body: str, *, unsanitized: bool = False) -> str:
        """Send an action message (like IRC /me)."""
        self.calls.append(
            RecordedCall("send_action", {"body": body, "unsanitized": unsanitized})
        )
        return ""

    async def send_system_message_to_client(
        self, client_id: int, body: str, *, unsanitized: bool = False
    ) -> str:
        """Send a private system message to a specific viewer."""
        self.calls.append(
            RecordedCall(
                "send_system_message_to_client",
                {"client_id": client_id, "body": body, "unsanitized": unsanitized},
            )
        )
        return ""

    async def set_message_visibility(
        self, message_ids: list[str], *, visible: bool
    ) -> str:
        """Hide or show chat messages (moderation)."""
        self.calls.append(
            RecordedCall(
                "set_message_visibility",
                {"message_ids": message_ids, "visible": visible},
            )
        )
        return ""

    async def get_chat_history(self) -> list[dict[str, Any]]:
        """Fetch recent chat messages."""
        self.calls.append(RecordedCall("get_chat_history", {}))
        return []

    async def get_connected_clients(self) -> list[dict[str, Any]]:
        """Get list of currently connected viewers."""
        self.calls.append(RecordedCall("get_connected_clients", {}))
        return []

    async def set_stream_title(self, title: str) -> str:
        """Update the stream title."""
        self.calls.append(RecordedCall("set_stream_title", {"title": title}))
        return ""


class RecordingOwncastAdminClient(_AdminBase):
    """Stub mirroring :class:`~owlbot.api.owncast_admin_client.OwncastAdminClient`.

    Each public async method records ``(method_name, kwargs_dict)`` to
    :attr:`calls` and returns an empty default of the declared return type.
    """

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        """Initialize with an empty call log and the given base URL."""
        self.calls: list[RecordedCall] = []
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        """The Owncast server base URL passed at construction."""
        return self._base_url

    async def get_status(self) -> dict[str, Any]:
        """Get the current server status including stream info and viewer count."""
        self.calls.append(RecordedCall("get_status", {}))
        return {}

    async def get_active_viewers(self) -> list[dict[str, Any]]:
        """Get a list of currently active viewers."""
        self.calls.append(RecordedCall("get_active_viewers", {}))
        return []

    async def get_viewers_over_time(self, window_start: int) -> list[dict[str, Any]]:
        """Get viewer count data over time for charting."""
        self.calls.append(
            RecordedCall("get_viewers_over_time", {"window_start": window_start})
        )
        return []

    async def get_hardware_stats(self) -> dict[str, Any]:
        """Get server hardware statistics (CPU, memory, disk)."""
        self.calls.append(RecordedCall("get_hardware_stats", {}))
        return {}

    async def get_server_config(self) -> dict[str, Any]:
        """Get the full server configuration."""
        self.calls.append(RecordedCall("get_server_config", {}))
        return {}

    async def disconnect_stream(self) -> dict[str, Any]:
        """Disconnect the current inbound stream."""
        self.calls.append(RecordedCall("disconnect_stream", {}))
        return {}

    async def get_playback_metrics(self) -> dict[str, Any]:
        """Get playback quality metrics."""
        self.calls.append(RecordedCall("get_playback_metrics", {}))
        return {}

    async def get_logs(self) -> list[dict[str, Any]]:
        """Get server logs."""
        self.calls.append(RecordedCall("get_logs", {}))
        return []

    async def get_warnings(self) -> list[dict[str, Any]]:
        """Get server warning and error logs."""
        self.calls.append(RecordedCall("get_warnings", {}))
        return []

    async def get_chat_messages(self) -> list[dict[str, Any]]:
        """Get chat messages from the admin perspective."""
        self.calls.append(RecordedCall("get_chat_messages", {}))
        return []

    async def get_connected_chat_clients(self) -> list[dict[str, Any]]:
        """Get currently connected chat clients."""
        self.calls.append(RecordedCall("get_connected_chat_clients", {}))
        return []

    async def set_message_visibility(
        self, message_ids: list[str], *, visible: bool
    ) -> str:
        """Hide or show chat messages."""
        self.calls.append(
            RecordedCall(
                "set_message_visibility",
                {"message_ids": message_ids, "visible": visible},
            )
        )
        return ""

    async def set_user_enabled(self, user_id: str, *, enabled: bool) -> str:
        """Enable or disable a chat user."""
        self.calls.append(
            RecordedCall("set_user_enabled", {"user_id": user_id, "enabled": enabled})
        )
        return ""

    async def get_disabled_users(self) -> list[dict[str, Any]]:
        """Get a list of disabled chat users."""
        self.calls.append(RecordedCall("get_disabled_users", {}))
        return []

    async def set_user_moderator(self, user_id: str, *, is_mod: bool) -> str:
        """Grant or revoke moderator status for a user."""
        self.calls.append(
            RecordedCall("set_user_moderator", {"user_id": user_id, "is_mod": is_mod})
        )
        return ""

    async def get_moderators(self) -> list[dict[str, Any]]:
        """Get a list of moderator users."""
        self.calls.append(RecordedCall("get_moderators", {}))
        return []

    async def ban_ip_address(self, ip: str) -> str:
        """Ban an IP address from chat."""
        self.calls.append(RecordedCall("ban_ip_address", {"ip": ip}))
        return ""

    async def unban_ip_address(self, ip: str) -> str:
        """Remove an IP address ban."""
        self.calls.append(RecordedCall("unban_ip_address", {"ip": ip}))
        return ""

    async def get_ip_address_bans(self) -> list[dict[str, Any]]:
        """Get a list of banned IP addresses."""
        self.calls.append(RecordedCall("get_ip_address_bans", {}))
        return []

    async def set_stream_title(self, title: str) -> str:
        """Set the stream title."""
        self.calls.append(RecordedCall("set_stream_title", {"title": title}))
        return ""

    async def set_server_name(self, name: str) -> str:
        """Set the server name."""
        self.calls.append(RecordedCall("set_server_name", {"name": name}))
        return ""

    async def set_server_summary(self, summary: str) -> str:
        """Set the server summary."""
        self.calls.append(RecordedCall("set_server_summary", {"summary": summary}))
        return ""

    async def set_welcome_message(self, message: str) -> str:
        """Set the welcome message shown to new viewers."""
        self.calls.append(RecordedCall("set_welcome_message", {"message": message}))
        return ""

    async def set_offline_message(self, message: str) -> str:
        """Set the message shown when the stream is offline."""
        self.calls.append(RecordedCall("set_offline_message", {"message": message}))
        return ""

    async def set_page_content(self, content: str) -> str:
        """Set the custom page content (HTML/markdown below the player)."""
        self.calls.append(RecordedCall("set_page_content", {"content": content}))
        return ""

    async def set_admin_password(self, password: str) -> str:
        """Change the admin password."""
        self.calls.append(RecordedCall("set_admin_password", {"password": password}))
        return ""

    async def set_server_url(self, url: str) -> str:
        """Set the public server URL."""
        self.calls.append(RecordedCall("set_server_url", {"url": url}))
        return ""

    async def set_logo(self, base64_data_url: str) -> str:
        """Set the server logo from a base64 data URL."""
        self.calls.append(
            RecordedCall("set_logo", {"base64_data_url": base64_data_url})
        )
        return ""

    async def set_custom_styles(self, css: str) -> str:
        """Set custom CSS styles for the web interface."""
        self.calls.append(RecordedCall("set_custom_styles", {"css": css}))
        return ""

    async def set_custom_javascript(self, js: str) -> str:
        """Set custom JavaScript for the web interface."""
        self.calls.append(RecordedCall("set_custom_javascript", {"js": js}))
        return ""

    async def set_color_variables(self, variables: dict[str, Any]) -> str:
        """Set the custom color variables for the web interface."""
        self.calls.append(RecordedCall("set_color_variables", {"variables": variables}))
        return ""

    async def set_tags(self, tags: list[str]) -> str:
        """Set the server tags."""
        self.calls.append(RecordedCall("set_tags", {"tags": tags}))
        return ""

    async def set_social_handles(self, handles: list[SocialHandle]) -> str:
        """Set the social media handles displayed on the page."""
        self.calls.append(RecordedCall("set_social_handles", {"handles": handles}))
        return ""

    async def set_external_actions(self, actions: list[ExternalAction]) -> str:
        """Set the external actions (buttons/links in the player)."""
        self.calls.append(RecordedCall("set_external_actions", {"actions": actions}))
        return ""

    async def set_chat_disabled(self, *, disabled: bool) -> str:
        """Enable or disable the chat."""
        self.calls.append(RecordedCall("set_chat_disabled", {"disabled": disabled}))
        return ""

    async def set_chat_join_messages_enabled(self, *, enabled: bool) -> str:
        """Enable or disable chat join messages."""
        self.calls.append(
            RecordedCall("set_chat_join_messages_enabled", {"enabled": enabled})
        )
        return ""

    async def set_chat_established_mode(self, *, enabled: bool) -> str:
        """Enable or disable established user mode for chat."""
        self.calls.append(
            RecordedCall("set_chat_established_mode", {"enabled": enabled})
        )
        return ""

    async def set_chat_spam_protection(self, *, enabled: bool) -> str:
        """Enable or disable chat spam protection."""
        self.calls.append(
            RecordedCall("set_chat_spam_protection", {"enabled": enabled})
        )
        return ""

    async def set_chat_slur_filter(self, *, enabled: bool) -> str:
        """Enable or disable the chat slur filter."""
        self.calls.append(RecordedCall("set_chat_slur_filter", {"enabled": enabled}))
        return ""

    async def set_forbidden_usernames(self, names: list[str]) -> str:
        """Set the list of forbidden usernames."""
        self.calls.append(RecordedCall("set_forbidden_usernames", {"names": names}))
        return ""

    async def set_suggested_usernames(self, names: list[str]) -> str:
        """Set the list of suggested usernames for new viewers."""
        self.calls.append(RecordedCall("set_suggested_usernames", {"names": names}))
        return ""

    async def set_nsfw(self, *, nsfw: bool) -> str:
        """Set the NSFW flag for the server."""
        self.calls.append(RecordedCall("set_nsfw", {"nsfw": nsfw}))
        return ""

    async def set_directory_enabled(self, *, enabled: bool) -> str:
        """Enable or disable listing in the Owncast directory."""
        self.calls.append(RecordedCall("set_directory_enabled", {"enabled": enabled}))
        return ""

    async def set_hide_viewer_count(self, *, hide: bool) -> str:
        """Show or hide the viewer count."""
        self.calls.append(RecordedCall("set_hide_viewer_count", {"hide": hide}))
        return ""

    async def set_disable_search_indexing(self, *, disabled: bool) -> str:
        """Enable or disable search engine indexing."""
        self.calls.append(
            RecordedCall("set_disable_search_indexing", {"disabled": disabled})
        )
        return ""

    async def set_stream_keys(self, keys: list[StreamKey]) -> str:
        """Set the stream keys."""
        self.calls.append(RecordedCall("set_stream_keys", {"keys": keys}))
        return ""

    async def set_video_variants(self, variants: list[VideoVariant]) -> str:
        """Set the video output variants (quality levels)."""
        self.calls.append(RecordedCall("set_video_variants", {"variants": variants}))
        return ""

    async def set_video_codec(self, codec: str) -> str:
        """Set the video codec."""
        self.calls.append(RecordedCall("set_video_codec", {"codec": codec}))
        return ""

    async def set_video_serving_endpoint(self, endpoint: str) -> str:
        """Set the video serving endpoint (e.g., for CDN)."""
        self.calls.append(
            RecordedCall("set_video_serving_endpoint", {"endpoint": endpoint})
        )
        return ""

    async def set_stream_latency(self, level: int) -> str:
        """Set the stream latency level."""
        self.calls.append(RecordedCall("set_stream_latency", {"level": level}))
        return ""

    async def set_ffmpeg_path(self, path: str) -> str:
        """Set the path to the ffmpeg binary."""
        self.calls.append(RecordedCall("set_ffmpeg_path", {"path": path}))
        return ""

    async def set_socket_host_override(self, host: str) -> str:
        """Set the WebSocket host override."""
        self.calls.append(RecordedCall("set_socket_host_override", {"host": host}))
        return ""

    async def set_rtmp_port(self, port: int) -> str:
        """Set the RTMP server port."""
        self.calls.append(RecordedCall("set_rtmp_port", {"port": port}))
        return ""

    async def set_web_server_port(self, port: int) -> str:
        """Set the web server port."""
        self.calls.append(RecordedCall("set_web_server_port", {"port": port}))
        return ""

    async def set_web_server_ip(self, ip: str) -> str:
        """Set the web server bind IP address."""
        self.calls.append(RecordedCall("set_web_server_ip", {"ip": ip}))
        return ""

    async def set_s3_config(
        self,
        *,
        enabled: bool,
        endpoint: str,
        access_key: str,
        secret: str,
        bucket: str,
        region: str,
    ) -> str:
        """Set the S3 storage configuration."""
        self.calls.append(
            RecordedCall(
                "set_s3_config",
                {
                    "enabled": enabled,
                    "endpoint": endpoint,
                    "access_key": access_key,
                    "secret": secret,
                    "bucket": bucket,
                    "region": region,
                },
            )
        )
        return ""

    async def set_discord_notifications(
        self, *, enabled: bool, webhook: str, go_live_message: str
    ) -> str:
        """Set the Discord notification configuration."""
        self.calls.append(
            RecordedCall(
                "set_discord_notifications",
                {
                    "enabled": enabled,
                    "webhook": webhook,
                    "go_live_message": go_live_message,
                },
            )
        )
        return ""

    async def set_browser_notifications(
        self, *, enabled: bool, go_live_message: str
    ) -> str:
        """Set the browser notification configuration."""
        self.calls.append(
            RecordedCall(
                "set_browser_notifications",
                {"enabled": enabled, "go_live_message": go_live_message},
            )
        )
        return ""

    async def upload_emoji(self, name: str, data_base64: str) -> str:
        """Upload a custom emoji."""
        self.calls.append(
            RecordedCall("upload_emoji", {"name": name, "data_base64": data_base64})
        )
        return ""

    async def delete_emoji(self, name: str) -> str:
        """Delete a custom emoji."""
        self.calls.append(RecordedCall("delete_emoji", {"name": name}))
        return ""

    async def get_webhooks(self) -> list[dict[str, Any]]:
        """Get all registered webhooks."""
        self.calls.append(RecordedCall("get_webhooks", {}))
        return []

    async def create_webhook(self, url: str, events: list[str]) -> str:
        """Create a new webhook."""
        self.calls.append(
            RecordedCall("create_webhook", {"url": url, "events": events})
        )
        return ""

    async def delete_webhook(self, webhook_id: int) -> str:
        """Delete a webhook."""
        self.calls.append(RecordedCall("delete_webhook", {"webhook_id": webhook_id}))
        return ""

    async def get_access_tokens(self) -> list[dict[str, Any]]:
        """Get all access tokens."""
        self.calls.append(RecordedCall("get_access_tokens", {}))
        return []

    async def create_access_token(self, name: str, scopes: list[str]) -> str:
        """Create a new access token."""
        self.calls.append(
            RecordedCall("create_access_token", {"name": name, "scopes": scopes})
        )
        return ""

    async def delete_access_token(self, token: str) -> str:
        """Delete an access token."""
        self.calls.append(RecordedCall("delete_access_token", {"token": token}))
        return ""

    async def get_followers(self, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        """Get a paginated list of followers."""
        self.calls.append(
            RecordedCall("get_followers", {"offset": offset, "limit": limit})
        )
        return {}

    async def get_pending_follow_requests(self) -> list[dict[str, Any]]:
        """Get pending follow requests."""
        self.calls.append(RecordedCall("get_pending_follow_requests", {}))
        return []

    async def get_blocked_followers(self) -> list[dict[str, Any]]:
        """Get blocked and rejected followers."""
        self.calls.append(RecordedCall("get_blocked_followers", {}))
        return []

    async def approve_follower(self, actor_iri: str, *, approved: bool) -> str:
        """Approve or reject a follow request."""
        self.calls.append(
            RecordedCall(
                "approve_follower", {"actor_iri": actor_iri, "approved": approved}
            )
        )
        return ""

    async def send_federated_message(self, message: str) -> str:
        """Send a message to all followers via federation."""
        self.calls.append(RecordedCall("send_federated_message", {"message": message}))
        return ""

    async def set_federation_enabled(self, *, enabled: bool) -> str:
        """Enable or disable federation (ActivityPub)."""
        self.calls.append(RecordedCall("set_federation_enabled", {"enabled": enabled}))
        return ""

    async def set_federation_username(self, name: str) -> str:
        """Set the federation (ActivityPub) username."""
        self.calls.append(RecordedCall("set_federation_username", {"name": name}))
        return ""

    async def set_federation_go_live_message(self, message: str) -> str:
        """Set the message sent to followers when going live."""
        self.calls.append(
            RecordedCall("set_federation_go_live_message", {"message": message})
        )
        return ""

    async def set_federation_blocked_domains(self, domains: list[str]) -> str:
        """Set the list of blocked federation domains."""
        self.calls.append(
            RecordedCall("set_federation_blocked_domains", {"domains": domains})
        )
        return ""
