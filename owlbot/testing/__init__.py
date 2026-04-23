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

"""Testing utilities for Owlbot modules."""

from owlbot.testing.helpers import (
    make_chat_event,
    make_name_changed_event,
    make_stream_started_event,
    make_stream_status,
    make_stream_stopped_event,
    make_stream_title_updated_event,
    make_user,
    make_user_joined_event,
    make_user_parted_event,
    make_visibility_update_event,
)
from owlbot.testing.stubs import (
    RecordedCall,
    RecordingOwncastAdminClient,
    RecordingOwncastClient,
)

__all__ = [
    "RecordedCall",
    "RecordingOwncastAdminClient",
    "RecordingOwncastClient",
    "make_chat_event",
    "make_name_changed_event",
    "make_stream_started_event",
    "make_stream_status",
    "make_stream_stopped_event",
    "make_stream_title_updated_event",
    "make_user",
    "make_user_joined_event",
    "make_user_parted_event",
    "make_visibility_update_event",
]
