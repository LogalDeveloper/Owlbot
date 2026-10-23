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

"""Owlbot - A modular chat bot for Owncast.

The public API for building modules is in the `owlbot.api` subpackage:

    from owlbot.api import on_event, on_command, EventType, EventContext
"""

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

OWNCAST_TARGET_VERSION = "0.3.0"

from .bot import Owlbot, StartupError

__all__ = [
    "OWNCAST_TARGET_VERSION",
    "Owlbot",
    "StartupError",
    "__version__",
]
