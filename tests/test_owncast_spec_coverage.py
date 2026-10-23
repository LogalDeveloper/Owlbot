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

"""Spec-coverage tests for the Owncast API clients.

Enforces a three-way invariant across:

- the pinned Owncast OpenAPI spec (``openapi-v<version>.yaml``),
- the hand-curated mapping dicts (``INTEGRATION_OPERATIONS`` and
  ``ADMIN_OPERATIONS``),
- the public async methods on the client classes (``OwncastClient`` and
  ``OwncastAdminClient``).

Four tests, each run for both the integration and admin surfaces, form a
closed loop around the mapping::

    spec --(1)--> mapping --(2)--> method
         <--(3)--         <--(4)--

(1) ``test_*_spec_op_is_mapped``
    Every spec operationId has a mapping entry.
    Fails when Owncast added an endpoint.
    Fix: wrap it as a method on the client, add an entry to the mapping.

(2) ``test_*_mapping_targets_real_method``
    Every mapping value resolves to an async method on the client.
    Fails when the client is missing a method the mapping claims exists.
    Fix: add or rename the method on the client class to match the mapping.

(3) ``test_*_mapping_is_not_orphaned``
    Every mapping key still exists in the spec.
    Fails when upstream removed or renamed the operation.
    Fix: drop the entry (removal) or update its key (rename).

(4) ``test_*_method_is_mapped``
    Every client method is referenced by the mapping.
    Fails when a method was added without being registered.
    Fix: add it to the mapping, or delete the method.

A fifth test, ``test_method_declaration_order_matches_spec``, pins the
declaration order of mapped methods on the real clients and their
recording stubs to match the spec document's operationId order.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Final

import pytest
from ruamel.yaml import YAML

from owlbot import OWNCAST_TARGET_VERSION
from owlbot.api.owncast_admin_client import OwncastAdminClient
from owlbot.api.owncast_client import OwncastClient
from owlbot.testing.stubs import RecordingOwncastAdminClient, RecordingOwncastClient

SPEC_PATH: Final = (
    Path(__file__).parent / "fixtures" / f"openapi-v{OWNCAST_TARGET_VERSION}.yaml"
)

INTEGRATION_OPERATIONS: Final[dict[str, str]] = {
    "ExternalGetChatMessages": "get_chat_history",
    "ExternalGetConnectedChatClients": "get_connected_clients",
    "ExternalGetStatus": "get_status",
    "ExternalGetUserDetails": "get_user_details",
    "ExternalSetStreamTitle": "set_stream_title",
    "ExternalUpdateMessageVisibility": "set_message_visibility",
    "SendChatAction": "send_action",
    "SendIntegrationChatMessage": "send_message",
    "SendSystemMessage": "send_system_message",
    "SendSystemMessageToConnectedClient": "send_system_message_to_client",
    "SendUserMessage": "send_user_message",
}

ADMIN_OPERATIONS: Final[dict[str, str]] = {
    "AddFederatedServer": "add_federated_server",
    "ApproveFollower": "approve_follower",
    "BanIPAddress": "ban_ip_address",
    "CreateExternalAPIUser": "create_access_token",
    "CreateWebhook": "create_webhook",
    "DeleteCustomEmoji": "delete_emoji",
    "DeleteExternalAPIUser": "delete_access_token",
    "DeletePrometheusAPI": "delete_prometheus",
    "DeleteUser": "delete_user",
    "DeleteWebhook": "delete_webhook",
    "DisconnectInboundConnection": "disconnect_stream",
    "GetActiveViewers": "get_active_viewers",
    "GetBlockedAndRejectedFollowers": "get_blocked_followers",
    "GetChatMessagesAdmin": "get_chat_messages",
    "GetConnectedChatClients": "get_connected_chat_clients",
    "GetDisabledUsers": "get_disabled_users",
    "GetDirectoryFollowers": "get_directory_followers",
    "GetExternalAPIUsers": "get_access_tokens",
    "GetFederatedActions": "get_federated_actions",
    "GetAdminFederatedServers": "get_federated_servers",
    "GetFeatureRequests": "get_feature_requests",
    "GetFollowersAdmin": "get_followers",
    "GetHardwareStats": "get_hardware_stats",
    "GetIPAddressBans": "get_ip_address_bans",
    "GetLogs": "get_logs",
    "GetModerators": "get_moderators",
    "GetPendingFollowRequests": "get_pending_follow_requests",
    "GetPlaybackClients": "get_playback_clients",
    "GetPrometheusAPI": "get_prometheus_metrics",
    "GetServerConfig": "get_server_config",
    "GetUsers": "get_users",
    "GetVideoPlaybackMetrics": "get_playback_metrics",
    "GetViewersOverTime": "get_viewers_over_time",
    "GetWarnings": "get_warnings",
    "GetWebhooks": "get_webhooks",
    "PostPrometheusAPI": "post_prometheus",
    "PutPrometheusAPI": "put_prometheus",
    "RemoveFederatedServer": "remove_federated_server",
    "RemoveFollower": "remove_follower",
    "ResetFavicon": "reset_favicon",
    "ResetYPRegistration": "reset_yp_registration",
    "SendFederatedMessage": "send_federated_message",
    "SetAdminPassword": "set_admin_password",
    "SetAutoplay": "set_autoplay",
    "SetBrowserNotificationConfiguration": "set_browser_notifications",
    "SetChatDisabled": "set_chat_disabled",
    "SetChatJoinMessagesEnabled": "set_chat_join_messages_enabled",
    "SetChatRequireAuthentication": "set_chat_require_authentication",
    "SetChatSlurFilterEnabled": "set_chat_slur_filter",
    "SetChatSpamProtectionEnabled": "set_chat_spam_protection",
    "SetCustomColorVariableValues": "set_color_variables",
    "SetCustomJavascript": "set_custom_javascript",
    "SetCustomOfflineMessage": "set_offline_message",
    "SetCustomStyles": "set_custom_styles",
    "SetDirectoryEnabled": "set_directory_enabled",
    "SetDisableSearchIndexing": "set_disable_search_indexing",
    "SetDiscordNotificationConfiguration": "set_discord_notifications",
    "SetEnableEstablishedChatUserMode": "set_chat_established_mode",
    "SetExternalActions": "set_external_actions",
    "SetExtraPageContent": "set_page_content",
    "SetFavicon": "set_favicon",
    "SetFederationActivityPrivate": "set_federation_activity_private",
    "SetFederationBlockDomains": "set_federation_blocked_domains",
    "SetFederationEnabled": "set_federation_enabled",
    "SetFederationEnableQuotes": "set_federation_enable_quotes",
    "SetFederationGoLiveMessage": "set_federation_go_live_message",
    "SetFederationHideFollowersTab": "set_federation_hide_followers",
    "SetFederationShowEngagement": "set_federation_show_engagement",
    "SetFederationUsername": "set_federation_username",
    "SetFfmpegPath": "set_ffmpeg_path",
    "SetForbiddenUsernameList": "set_forbidden_usernames",
    "SetHideViewerCount": "set_hide_viewer_count",
    "SetLogo": "set_logo",
    "SetNSFW": "set_nsfw",
    "SetRTMPServerPort": "set_rtmp_port",
    "SetRTMPServerBindAddress": "set_rtmp_bind_address",
    "SetS3Configuration": "set_s3_config",
    "SetServerName": "set_server_name",
    "SetServerSummary": "set_server_summary",
    "SetServerURL": "set_server_url",
    "SetServerWelcomeMessage": "set_welcome_message",
    "SetSocialHandles": "set_social_handles",
    "SetSocketHostOverride": "set_socket_host_override",
    "SetStreamKeys": "set_stream_keys",
    "SetStreamLatencyLevel": "set_stream_latency",
    "SetStreamOutputVariants": "set_video_variants",
    "SetStreamTitle": "set_stream_title",
    "SetSuggestedUsernameList": "set_suggested_usernames",
    "SetTags": "set_tags",
    "SetVideoCodec": "set_video_codec",
    "SetVideoServingEndpoint": "set_video_serving_endpoint",
    "SetWebServerIP": "set_web_server_ip",
    "SetWebServerPort": "set_web_server_port",
    "StatusAdmin": "get_status",
    "UnbanIPAddress": "unban_ip_address",
    "UpdateMessageVisibilityAdmin": "set_message_visibility",
    "UpdateUserEnabledAdmin": "set_user_enabled",
    "UpdateUserModerator": "set_user_moderator",
    "UploadCustomEmoji": "upload_emoji",
}


def load_spec() -> dict[str, Any]:
    """Parse the pinned Owncast OpenAPI spec as a mapping."""
    yaml = YAML(typ="safe")
    with SPEC_PATH.open() as f:
        loaded = yaml.load(f)
    if not isinstance(loaded, dict):
        raise TypeError(f"Expected mapping at spec root, got {type(loaded).__name__}.")
    return loaded


def spec_operation_ids(spec: dict[str, Any], path_prefix: str) -> list[str]:
    """Return operationIds for non-OPTIONS, non-internal ops under ``path_prefix``.

    Order follows the spec document: paths in declaration order, methods
    within each path in declaration order.

    :raises RuntimeError: If any covered operation is missing a string
        ``operationId``.
    """
    oids: list[str] = []
    missing: list[str] = []
    for path, methods in spec["paths"].items():
        if not path.startswith(path_prefix):
            continue
        for method, op in methods.items():
            if method == "options" or not isinstance(op, dict):
                continue
            if op.get("x-internal"):
                continue
            oid = op.get("operationId")
            if isinstance(oid, str):
                oids.append(oid)
            else:
                missing.append(f"{method.upper()} {path}")
    if missing:
        raise RuntimeError(
            f"Spec operations under {path_prefix!r} missing operationId: "
            f"{', '.join(missing)}"
        )
    return oids


def public_async_methods(cls: type) -> list[str]:
    """Return public async method names in class declaration order."""
    return [
        name
        for name, obj in cls.__dict__.items()
        if not name.startswith("_") and inspect.iscoroutinefunction(obj)
    ]


SPEC = load_spec()
INTEGRATION_SPEC_OPS = spec_operation_ids(SPEC, "/integrations/")
ADMIN_SPEC_OPS = spec_operation_ids(SPEC, "/admin/")
INTEGRATION_METHODS = public_async_methods(OwncastClient)
ADMIN_METHODS = public_async_methods(OwncastAdminClient)


@pytest.mark.parametrize("operation_id", sorted(INTEGRATION_SPEC_OPS))
def test_integration_spec_op_is_mapped(operation_id: str) -> None:
    """Every /integrations/ operationId has a mapping in INTEGRATION_OPERATIONS."""
    assert operation_id in INTEGRATION_OPERATIONS, (
        f"Spec operation {operation_id!r} under /integrations/ has no mapping "
        f"in INTEGRATION_OPERATIONS. Wrap it on OwncastClient and add the mapping."
    )


@pytest.mark.parametrize("operation_id", sorted(INTEGRATION_OPERATIONS))
def test_integration_mapping_targets_real_method(operation_id: str) -> None:
    """Every INTEGRATION_OPERATIONS entry targets an OwncastClient method."""
    method_name = INTEGRATION_OPERATIONS[operation_id]
    assert method_name in INTEGRATION_METHODS, (
        f"INTEGRATION_OPERATIONS[{operation_id!r}] points to {method_name!r}, "
        f"but OwncastClient has no public async method with that name."
    )


@pytest.mark.parametrize("operation_id", sorted(INTEGRATION_OPERATIONS))
def test_integration_mapping_is_not_orphaned(operation_id: str) -> None:
    """Every INTEGRATION_OPERATIONS key matches a /integrations/ operationId."""
    assert operation_id in INTEGRATION_SPEC_OPS, (
        f"INTEGRATION_OPERATIONS has {operation_id!r}, but no such operation "
        f"exists under /integrations/ in the pinned spec. "
        f"Either the spec removed it or the mapping key is misspelled."
    )


@pytest.mark.parametrize("method_name", sorted(INTEGRATION_METHODS))
def test_integration_method_is_mapped(method_name: str) -> None:
    """Every async OwncastClient method is referenced by INTEGRATION_OPERATIONS."""
    mapped = set(INTEGRATION_OPERATIONS.values())
    assert method_name in mapped, (
        f"OwncastClient.{method_name} is not referenced by INTEGRATION_OPERATIONS. "
        f"Either map it to a spec operationId or remove the method."
    )


@pytest.mark.parametrize("operation_id", sorted(ADMIN_SPEC_OPS))
def test_admin_spec_op_is_mapped(operation_id: str) -> None:
    """Every /admin/ operationId has a mapping in ADMIN_OPERATIONS."""
    assert operation_id in ADMIN_OPERATIONS, (
        f"Spec operation {operation_id!r} under /admin/ has no mapping "
        f"in ADMIN_OPERATIONS. Wrap it on OwncastAdminClient and add the mapping."
    )


@pytest.mark.parametrize("operation_id", sorted(ADMIN_OPERATIONS))
def test_admin_mapping_targets_real_method(operation_id: str) -> None:
    """Every ADMIN_OPERATIONS entry targets an OwncastAdminClient method."""
    method_name = ADMIN_OPERATIONS[operation_id]
    assert method_name in ADMIN_METHODS, (
        f"ADMIN_OPERATIONS[{operation_id!r}] points to {method_name!r}, "
        f"but OwncastAdminClient has no public async method with that name."
    )


@pytest.mark.parametrize("operation_id", sorted(ADMIN_OPERATIONS))
def test_admin_mapping_is_not_orphaned(operation_id: str) -> None:
    """Every ADMIN_OPERATIONS key matches an /admin/ operationId."""
    assert operation_id in ADMIN_SPEC_OPS, (
        f"ADMIN_OPERATIONS has {operation_id!r}, but no such operation "
        f"exists under /admin/ in the pinned spec. "
        f"Either the spec removed it or the mapping key is misspelled."
    )


@pytest.mark.parametrize("method_name", sorted(ADMIN_METHODS))
def test_admin_method_is_mapped(method_name: str) -> None:
    """Every async OwncastAdminClient method is referenced by ADMIN_OPERATIONS."""
    mapped = set(ADMIN_OPERATIONS.values())
    assert method_name in mapped, (
        f"OwncastAdminClient.{method_name} is not referenced by ADMIN_OPERATIONS. "
        f"Either map it to a spec operationId or remove the method."
    )


@pytest.mark.parametrize(
    ("cls", "mapping", "path_prefix"),
    [
        pytest.param(
            OwncastClient, INTEGRATION_OPERATIONS, "/integrations/", id="OwncastClient"
        ),
        pytest.param(
            RecordingOwncastClient,
            INTEGRATION_OPERATIONS,
            "/integrations/",
            id="RecordingOwncastClient",
        ),
        pytest.param(
            OwncastAdminClient, ADMIN_OPERATIONS, "/admin/", id="OwncastAdminClient"
        ),
        pytest.param(
            RecordingOwncastAdminClient,
            ADMIN_OPERATIONS,
            "/admin/",
            id="RecordingOwncastAdminClient",
        ),
    ],
)
def test_method_declaration_order_matches_spec(
    cls: type, mapping: dict[str, str], path_prefix: str
) -> None:
    """Public async methods are declared in the same order as the spec's operationIds.

    Only methods present in ``mapping`` participate; unmapped ops are ignored
    here because ``test_*_spec_op_is_mapped`` already covers coverage. This
    test pins the *order* of the mapped methods against the spec document.
    """
    spec_order = spec_operation_ids(SPEC, path_prefix)
    expected = [mapping[oid] for oid in spec_order if oid in mapping]
    expected_set = set(expected)
    actual = [m for m in public_async_methods(cls) if m in expected_set]
    assert actual == expected, (
        f"{cls.__name__} method declaration order does not match spec order.\n"
        f"Expected (spec order):\n  "
        + "\n  ".join(expected)
        + "\nActual (declaration order):\n  "
        + "\n  ".join(actual)
    )
