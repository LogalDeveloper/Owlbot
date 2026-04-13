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

"""Async HTTP client for the Owncast Admin API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

from .owncast_client import OwncastClient

if TYPE_CHECKING:
    from .http_client import HttpClient


@dataclass(frozen=True, slots=True)
class StreamKey:
    """A stream key for ingest authentication.

    :param key: The stream key string.
    :param comment: A human-readable label (e.g., "OBS - Main PC").
    """

    key: str
    comment: str

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> StreamKey:
        """Deserialize from the Owncast API format."""
        return cls(key=data["key"], comment=data.get("comment", ""))

    def _to_dict(self) -> dict[str, Any]:
        """Serialize to the Owncast API format."""
        return {"key": self.key, "comment": self.comment}


@dataclass(frozen=True, slots=True)
class VideoVariant:
    """A video output variant (quality level) for transcoding.

    :param name: Display name for this variant (e.g., "720p").
    :param video_bitrate: Video bitrate in kbps.
    :param audio_bitrate: Audio bitrate in kbps.
    :param scaled_width: Output width in pixels.
    :param scaled_height: Output height in pixels.
    :param framerate: Output framerate.
    :param cpu_usage_level: CPU usage level (1-5, higher = better quality).
    :param is_audio_passthrough: Whether to pass audio through without re-encoding.
    :param is_video_passthrough: Whether to pass video through without re-encoding.
    """

    name: str
    video_bitrate: int
    audio_bitrate: int
    scaled_width: int
    scaled_height: int
    framerate: int
    cpu_usage_level: int
    is_audio_passthrough: bool = True
    is_video_passthrough: bool = False

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> VideoVariant:
        """Deserialize from the Owncast API format.

        Handles both setter format (``isAudioPassthrough``) and getter
        format (``audioPassthrough``) since the Owncast API uses
        different key names for reads vs writes.
        """
        return cls(
            name=data.get("name", ""),
            video_bitrate=data.get("videoBitrate", 0),
            audio_bitrate=data.get("audioBitrate", 0),
            scaled_width=data.get("scaledWidth", 0),
            scaled_height=data.get("scaledHeight", 0),
            framerate=data.get("framerate", 0),
            cpu_usage_level=data.get("cpuUsageLevel", 3),
            is_audio_passthrough=data.get(
                "audioPassthrough", data.get("isAudioPassthrough", True)
            ),
            is_video_passthrough=data.get(
                "videoPassthrough", data.get("isVideoPassthrough", False)
            ),
        )

    def _to_dict(self) -> dict[str, Any]:
        """Serialize to the Owncast API format."""
        return {
            "name": self.name,
            "videoBitrate": self.video_bitrate,
            "audioBitrate": self.audio_bitrate,
            "scaledWidth": self.scaled_width,
            "scaledHeight": self.scaled_height,
            "framerate": self.framerate,
            "cpuUsageLevel": self.cpu_usage_level,
            "isAudioPassthrough": self.is_audio_passthrough,
            "isVideoPassthrough": self.is_video_passthrough,
        }


@dataclass(frozen=True, slots=True)
class SocialHandle:
    """A social media handle displayed on the server page.

    :param platform: Platform name (e.g., "twitter", "mastodon", "discord").
    :param url: URL to the social profile or invite.
    """

    platform: str
    url: str

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> SocialHandle:
        """Deserialize from the Owncast API format."""
        return cls(platform=data.get("platform", ""), url=data.get("url", ""))

    def _to_dict(self) -> dict[str, Any]:
        """Serialize to the Owncast API format."""
        return {"platform": self.platform, "url": self.url}


@dataclass(frozen=True, slots=True)
class ExternalAction:
    """An external action button displayed in the player UI.

    :param url: The URL the button links to.
    :param title: Button label text.
    :param description: Tooltip or description of the action.
    :param icon: URL to an icon image.
    :param open_externally: Whether to open in a new tab instead of an iframe.
    """

    url: str
    title: str
    description: str = ""
    icon: str = ""
    open_externally: bool = True

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> ExternalAction:
        """Deserialize from the Owncast API format."""
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            open_externally=data.get("openExternally", True),
        )

    def _to_dict(self) -> dict[str, Any]:
        """Serialize to the Owncast API format."""
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "openExternally": self.open_externally,
        }


class OwncastAdminClient(OwncastClient):
    """Async client for the Owncast Admin API.

    Uses HTTP Basic Authentication to access admin endpoints.
    All methods raise :class:`~owlbot.api.owncast_client.OwncastError` on HTTP errors.

    Uses a shared :class:`~owlbot.api.http_client.HttpClient` for HTTP
    transport (inherited from :class:`OwncastClient`).
    """

    def __init__(
        self, base_url: str, username: str, password: str, http_client: HttpClient
    ) -> None:
        """Initialize the admin client.

        :param base_url: The Owncast server URL (e.g., "https://stream.logal.dev").
        :param username: Admin username.
        :param password: Admin password.
        :param http_client: Shared HTTP client for making requests.
        """
        super().__init__(base_url, access_token="", http_client=http_client)
        self._logger = logging.getLogger("owlbot.owncast_admin_client")
        self._headers = None
        self._auth = aiohttp.BasicAuth(username, password)
        self._logger.debug(
            "Owncast admin API client initialized for: %s", self._base_url
        )

    async def get_status(self) -> dict[str, Any]:
        """Get the current server status including stream info and viewer count.

        :return: Server status dict.
        """
        return dict(await self._get("/api/admin/status"))

    async def get_active_viewers(self) -> list[dict[str, Any]]:
        """Get a list of currently active viewers.

        :return: Viewer list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/viewers"))

    async def get_viewers_over_time(self, window_start: int) -> list[dict[str, Any]]:
        """Get viewer count data over time for charting.

        :param window_start: Unix timestamp (seconds since epoch)
            for the start of the window.
        :return: Viewer count data points as returned by the Owncast API.
        """
        return list(
            await self._get(
                "/api/admin/viewersOverTime", params={"windowStart": str(window_start)}
            )
        )

    async def get_hardware_stats(self) -> dict[str, Any]:
        """Get server hardware statistics (CPU, memory, disk).

        :return: Hardware stats dict.
        """
        return dict(await self._get("/api/admin/hardwarestats"))

    async def get_server_config(self) -> dict[str, Any]:
        """Get the full server configuration.

        :return: Server configuration dict.
        """
        return dict(await self._get("/api/admin/serverconfig"))

    async def disconnect_stream(self) -> dict[str, Any]:
        """Disconnect the current inbound stream.

        :return: API response confirming the disconnect.
        """
        self._logger.info("Disconnecting inbound stream.")
        return dict(await self._get("/api/admin/disconnect"))

    async def get_chat_messages(self) -> list[dict[str, Any]]:
        """Get chat messages from the admin perspective.

        :return: Chat message list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/chat/messages"))

    async def get_connected_chat_clients(self) -> list[dict[str, Any]]:
        """Get currently connected chat clients.

        :return: Client list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/chat/clients"))

    async def set_message_visibility(
        self, message_ids: list[str], *, visible: bool
    ) -> str:
        """Hide or show chat messages.

        :param message_ids: List of message IDs to modify.
        :param visible: True to show messages, False to hide them.
        :return: Success message from the server.
        """
        action = "Showing" if visible else "Hiding"
        ids = ", ".join(message_ids)
        self._logger.info("%s %d message(s): %s", action, len(message_ids), ids)
        return await self._post(
            "/api/admin/chat/messagevisibility",
            {"idArray": message_ids, "visible": visible},
        )

    async def set_user_enabled(self, user_id: str, *, enabled: bool) -> str:
        """Enable or disable a chat user.

        :param user_id: The user ID to modify.
        :param enabled: True to enable, False to disable the user.
        :return: Success message from the server.
        """
        action = "Enabling" if enabled else "Disabling"
        self._logger.info("%s user %s.", action, user_id)
        return await self._post(
            "/api/admin/chat/users/setenabled",
            {"userId": user_id, "enabled": enabled},
        )

    async def get_disabled_users(self) -> list[dict[str, Any]]:
        """Get a list of disabled chat users.

        :return: User list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/chat/users/disabled"))

    async def set_user_moderator(self, user_id: str, *, is_mod: bool) -> str:
        """Grant or revoke moderator status for a user.

        :param user_id: The user ID to modify.
        :param is_mod: True to grant moderator, False to revoke.
        :return: Success message from the server.
        """
        action = "Granting" if is_mod else "Revoking"
        self._logger.info("%s moderator status for user %s.", action, user_id)
        return await self._post(
            "/api/admin/chat/users/setmoderator",
            {"userId": user_id, "isModerator": is_mod},
        )

    async def get_moderators(self) -> list[dict[str, Any]]:
        """Get a list of moderator users.

        :return: User list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/chat/users/moderators"))

    async def ban_ip_address(self, ip: str) -> str:
        """Ban an IP address from chat.

        :param ip: The IP address to ban.
        :return: Success message from the server.
        """
        self._logger.info("Banning IP address: %s", ip)
        return await self._post("/api/admin/chat/users/ipbans/create", {"value": ip})

    async def unban_ip_address(self, ip: str) -> str:
        """Remove an IP address ban.

        :param ip: The IP address to unban.
        :return: Success message from the server.
        """
        self._logger.info("Unbanning IP address: %s", ip)
        return await self._post("/api/admin/chat/users/ipbans/remove", {"value": ip})

    async def get_ip_address_bans(self) -> list[dict[str, Any]]:
        """Get a list of banned IP addresses.

        :return: IP ban list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/chat/users/ipbans"))

    async def set_stream_title(self, title: str) -> str:
        """Set the stream title.

        :param title: The new stream title.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/streamtitle", title, "stream title"
        )

    async def set_server_name(self, name: str) -> str:
        """Set the server name.

        :param name: The new server name.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/name", name, "server name"
        )

    async def set_server_summary(self, summary: str) -> str:
        """Set the server summary.

        :param summary: The new server summary.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/serversummary", summary, "server summary"
        )

    async def set_welcome_message(self, message: str) -> str:
        """Set the welcome message shown to new viewers.

        :param message: The new welcome message.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/welcomemessage", message, "welcome message"
        )

    async def set_offline_message(self, message: str) -> str:
        """Set the message shown when the stream is offline.

        :param message: The new offline message.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/offlinemessage", message, "offline message"
        )

    async def set_page_content(self, content: str) -> str:
        """Set the custom page content (HTML/markdown below the player).

        :param content: The page content.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/pagecontent", content, "page content"
        )

    async def set_admin_password(self, password: str) -> str:
        """Change the admin password.

        :param password: The new admin password.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/adminpass", password, "admin password"
        )

    async def set_server_url(self, url: str) -> str:
        """Set the public server URL.

        :param url: The new server URL.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/serverurl", url, "server URL"
        )

    async def set_custom_styles(self, css: str) -> str:
        """Set custom CSS styles for the web interface.

        :param css: The CSS string.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/customstyles", css, "custom styles"
        )

    async def set_custom_javascript(self, js: str) -> str:
        """Set custom JavaScript for the web interface.

        :param js: The JavaScript string.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/customjavascript", js, "custom JavaScript"
        )

    async def set_socket_host_override(self, host: str) -> str:
        """Set the WebSocket host override.

        :param host: The WebSocket host override value.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/sockethostoverride", host, "socket host override"
        )

    async def set_ffmpeg_path(self, path: str) -> str:
        """Set the path to the ffmpeg binary.

        :param path: The ffmpeg binary path.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/ffmpegpath", path, "ffmpeg path"
        )

    async def set_video_serving_endpoint(self, endpoint: str) -> str:
        """Set the video serving endpoint (e.g., for CDN).

        :param endpoint: The video serving endpoint URL.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/videoservingendpoint", endpoint, "video serving endpoint"
        )

    async def set_video_codec(self, codec: str) -> str:
        """Set the video codec.

        :param codec: The video codec name.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/video/codec", codec, "video codec"
        )

    async def set_chat_disabled(self, *, disabled: bool) -> str:
        """Enable or disable the chat.

        :param disabled: True to disable chat, False to enable it.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/disable", disabled, "chat disabled"
        )

    async def set_chat_join_messages_enabled(self, *, enabled: bool) -> str:
        """Enable or disable chat join messages.

        :param enabled: True to show join messages, False to hide them.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/joinmessagesenabled", enabled, "chat join messages"
        )

    async def set_chat_established_mode(self, *, enabled: bool) -> str:
        """Enable or disable established user mode for chat.

        :param enabled: True to enable established mode.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/establishedusermode",
            enabled,
            "chat established mode",
        )

    async def set_chat_spam_protection(self, *, enabled: bool) -> str:
        """Enable or disable chat spam protection.

        :param enabled: True to enable spam protection.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/spamprotectionenabled",
            enabled,
            "chat spam protection",
        )

    async def set_chat_slur_filter(self, *, enabled: bool) -> str:
        """Enable or disable the chat slur filter.

        :param enabled: True to enable the slur filter.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/slurfilterenabled", enabled, "chat slur filter"
        )

    async def set_nsfw(self, *, nsfw: bool) -> str:
        """Set the NSFW flag for the server.

        :param nsfw: True to mark the server as NSFW.
        :return: Success message from the server.
        """
        return await self._set_config_value("/api/admin/config/nsfw", nsfw, "NSFW flag")

    async def set_directory_enabled(self, *, enabled: bool) -> str:
        """Enable or disable listing in the Owncast directory.

        :param enabled: True to enable directory listing.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/directoryenabled", enabled, "directory enabled"
        )

    async def set_hide_viewer_count(self, *, hide: bool) -> str:
        """Show or hide the viewer count.

        :param hide: True to hide the viewer count.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/hideviewercount", hide, "hide viewer count"
        )

    async def set_disable_search_indexing(self, *, disabled: bool) -> str:
        """Enable or disable search engine indexing.

        :param disabled: True to disable search indexing.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/disablesearchindexing",
            disabled,
            "disable search indexing",
        )

    async def set_forbidden_usernames(self, names: list[str]) -> str:
        """Set the list of forbidden usernames.

        :param names: List of forbidden username strings.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/forbiddenusernames", names, "forbidden usernames"
        )

    async def set_suggested_usernames(self, names: list[str]) -> str:
        """Set the list of suggested usernames for new viewers.

        :param names: List of suggested username strings.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/chat/suggestedusernames", names, "suggested usernames"
        )

    async def set_tags(self, tags: list[str]) -> str:
        """Set the server tags.

        :param tags: List of tag strings.
        :return: Success message from the server.
        """
        return await self._set_config_value("/api/admin/config/tags", tags, "tags")

    async def set_federation_blocked_domains(self, domains: list[str]) -> str:
        """Set the list of blocked federation domains.

        :param domains: List of domain strings to block.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/federation/blockdomains",
            domains,
            "federation blocked domains",
        )

    async def set_stream_keys(self, keys: list[StreamKey]) -> str:
        """Set the stream keys.

        :param keys: List of :class:`StreamKey` objects.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/streamkeys",
            [k._to_dict() for k in keys],  # noqa: SLF001  # framework serialization; private to module authors
            "stream keys",
        )

    async def set_video_variants(self, variants: list[VideoVariant]) -> str:
        """Set the video output variants (quality levels).

        :param variants: List of :class:`VideoVariant` objects.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/video/streamoutputvariants",
            [v._to_dict() for v in variants],  # noqa: SLF001  # framework serialization; private to module authors
            "video variants",
        )

    async def set_social_handles(self, handles: list[SocialHandle]) -> str:
        """Set the social media handles displayed on the page.

        :param handles: List of :class:`SocialHandle` objects.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/socialhandles",
            [h._to_dict() for h in handles],  # noqa: SLF001  # framework serialization; private to module authors
            "social handles",
        )

    async def set_external_actions(self, actions: list[ExternalAction]) -> str:
        """Set the external actions (buttons/links in the player).

        :param actions: List of :class:`ExternalAction` objects.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/externalactions",
            [a._to_dict() for a in actions],  # noqa: SLF001  # framework serialization; private to module authors
            "external actions",
        )

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
        """Set the S3 storage configuration.

        :param enabled: Whether S3 storage is enabled.
        :param endpoint: The S3 endpoint URL.
        :param access_key: The S3 access key.
        :param secret: The S3 secret key.
        :param bucket: The S3 bucket name.
        :param region: The S3 region.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/s3",
            {
                "enabled": enabled,
                "endpoint": endpoint,
                "accessKey": access_key,
                "secret": secret,
                "bucket": bucket,
                "region": region,
            },
            "S3 config",
        )

    async def set_discord_notifications(
        self,
        *,
        enabled: bool,
        webhook: str,
        go_live_message: str,
    ) -> str:
        """Set the Discord notification configuration.

        :param enabled: Whether Discord notifications are enabled.
        :param webhook: The Discord webhook URL.
        :param go_live_message: The message sent when going live.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/notifications/discord",
            {
                "enabled": enabled,
                "webhook": webhook,
                "goLiveMessage": go_live_message,
            },
            "Discord notifications",
        )

    async def set_browser_notifications(
        self,
        *,
        enabled: bool,
        go_live_message: str,
    ) -> str:
        """Set the browser notification configuration.

        :param enabled: Whether browser notifications are enabled.
        :param go_live_message: The message shown when going live.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/notifications/browser",
            {
                "enabled": enabled,
                "goLiveMessage": go_live_message,
            },
            "browser notifications",
        )

    async def set_color_variables(self, variables: dict[str, Any]) -> str:
        """Set the custom color variables for the web interface.

        :param variables: Color variables dict.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/appearance", variables, "color variables"
        )

    async def set_stream_latency(self, level: int) -> str:
        """Set the stream latency level.

        :param level: Latency level value.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/video/streamlatencylevel", level, "stream latency"
        )

    async def set_rtmp_port(self, port: int) -> str:
        """Set the RTMP server port.

        :param port: The RTMP port number.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/rtmpserverport", port, "RTMP port"
        )

    async def set_web_server_port(self, port: int) -> str:
        """Set the web server port.

        :param port: The web server port number.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/webserverport", port, "web server port"
        )

    async def set_web_server_ip(self, ip: str) -> str:
        """Set the web server bind IP address.

        :param ip: The IP address to bind to.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/webserverip", ip, "web server IP"
        )

    async def set_federation_enabled(self, *, enabled: bool) -> str:
        """Enable or disable federation (ActivityPub).

        :param enabled: True to enable federation.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/federation/enable", enabled, "federation enabled"
        )

    async def set_federation_username(self, name: str) -> str:
        """Set the federation (ActivityPub) username.

        :param name: The federation username.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/federation/username", name, "federation username"
        )

    async def set_federation_go_live_message(self, message: str) -> str:
        """Set the message sent to followers when going live.

        :param message: The go-live notification message.
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/federation/livemessage",
            message,
            "federation go-live message",
        )

    async def set_logo(self, base64_data_url: str) -> str:
        """Set the server logo from a base64 data URL.

        :param base64_data_url: Data URL string
            (e.g., ``data:image/png;base64,iVBOR...``).
        :return: Success message from the server.
        """
        return await self._set_config_value(
            "/api/admin/config/logo", base64_data_url, "logo"
        )

    async def upload_emoji(self, name: str, data_base64: str) -> str:
        """Upload a custom emoji.

        :param name: The emoji name.
        :param data_base64: Base64-encoded image data for the emoji.
        :return: Success message from the server.
        """
        self._logger.info("Uploading emoji: %s", name)
        return await self._post(
            "/api/admin/emoji/upload", {"name": name, "data": data_base64}
        )

    async def delete_emoji(self, name: str) -> str:
        """Delete a custom emoji.

        :param name: The emoji name to delete.
        :return: Success message from the server.
        """
        self._logger.info("Deleting emoji: %s", name)
        return await self._post("/api/admin/emoji/delete", {"name": name})

    async def get_webhooks(self) -> list[dict[str, Any]]:
        """Get all registered webhooks.

        :return: Webhook list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/webhooks"))

    async def create_webhook(self, url: str, events: list[str]) -> str:
        """Create a new webhook.

        :param url: The URL to send webhook events to.
        :param events: List of event type strings to subscribe to.
        :return: Success message from the server.
        """
        self._logger.info("Creating webhook for %s with events: %s", url, events)
        return await self._post(
            "/api/admin/webhooks/create", {"url": url, "events": events}
        )

    async def delete_webhook(self, webhook_id: int) -> str:
        """Delete a webhook.

        :param webhook_id: The ID of the webhook to delete.
        :return: Success message from the server.
        """
        self._logger.info("Deleting webhook: %d", webhook_id)
        return await self._post("/api/admin/webhooks/delete", {"id": webhook_id})

    async def get_access_tokens(self) -> list[dict[str, Any]]:
        """Get all access tokens.

        :return: Token list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/accesstokens"))

    async def create_access_token(self, name: str, scopes: list[str]) -> str:
        """Create a new access token.

        :param name: Display name for the token.
        :param scopes: List of permission scope strings.
        :return: Success message from the server.
        """
        self._logger.info("Creating access token: %s", name)
        return await self._post(
            "/api/admin/accesstokens/create", {"name": name, "scopes": scopes}
        )

    async def delete_access_token(self, token: str) -> str:
        """Delete an access token.

        :param token: The token string to delete.
        :return: Success message from the server.
        """
        self._logger.info("Deleting access token ending in '...%s'.", token[-4:])

        return await self._post("/api/admin/accesstokens/delete", {"token": token})

    async def get_followers(self, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        """Get a paginated list of followers.

        :param offset: Number of followers to skip.
        :param limit: Maximum number of followers to return.
        :return: Paginated follower data as returned by the Owncast API.
        """
        return dict(
            await self._get(
                "/api/admin/followers", params={"offset": offset, "limit": limit}
            )
        )

    async def get_pending_follow_requests(self) -> list[dict[str, Any]]:
        """Get pending follow requests.

        :return: Follower request list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/followers/pending"))

    async def get_blocked_followers(self) -> list[dict[str, Any]]:
        """Get blocked and rejected followers.

        :return: Follower list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/followers/blocked"))

    async def approve_follower(self, actor_iri: str, *, approved: bool) -> str:
        """Approve or reject a follow request.

        :param actor_iri: The ActivityPub actor IRI of the follower.
        :param approved: True to approve, False to reject.
        :return: Success message from the server.
        """
        action = "Approving" if approved else "Rejecting"
        self._logger.info("%s follower %s.", action, actor_iri)
        return await self._post(
            "/api/admin/followers/approve",
            {"actorIRI": actor_iri, "approved": approved},
        )

    async def send_federated_message(self, message: str) -> str:
        """Send a message to all followers via federation.

        :param message: The message text to send.
        :return: Success message from the server.
        """
        self._logger.info("Sending federated message: %s", message)
        return await self._post("/api/admin/federation/send", {"value": message})

    async def get_logs(self) -> list[dict[str, Any]]:
        """Get server logs.

        :return: Log entry list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/logs"))

    async def get_warnings(self) -> list[dict[str, Any]]:
        """Get server warning and error logs.

        :return: Log entry list as returned by the Owncast API.
        """
        return list(await self._get("/api/admin/logs/warnings"))

    async def get_playback_metrics(self) -> dict[str, Any]:
        """Get playback quality metrics.

        :return: Playback metrics as returned by the Owncast API.
        """
        return dict(await self._get("/api/admin/metrics/video"))

    async def _set_config_value(
        self, endpoint: str, value: Any, description: str
    ) -> str:
        """Set a configuration value via POST with a ``{"value": ...}`` body.

        :param endpoint: The config API endpoint path.
        :param value: The value to set.
        :param description: Human-readable description for logging.
        :return: Success message from the server.
        :raises OwncastError: If the request fails.
        """
        self._logger.info("Setting config: %s", description)
        return await self._post(endpoint, {"value": value})
