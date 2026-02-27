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

"""Jinja2 template rendering for Owlbot modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jinja2

from owlbot import __version__

if TYPE_CHECKING:
    from pathlib import Path


class ModuleTemplates:
    """Module-scoped Jinja2 template rendering.

    Uses a ChoiceLoader that checks the module's ``templates/`` directory
    first (if it exists), then falls back to Owlbot's core template
    directory.  This lets modules override any core template while
    inheriting shared layouts like ``base.html`` by default.
    """

    def __init__(self, module_dir: Path, core_template_dir: Path) -> None:
        """Initialize the template environment for a module.

        :param module_dir: Root directory of the module (contains ``templates/``
            subdirectory if the module ships its own templates).
        :param core_template_dir: Owlbot's shared template directory
            (``owlbot/templates/``).
        """
        loaders: list[jinja2.BaseLoader] = []

        module_template_dir = module_dir / "templates"
        if module_template_dir.is_dir():
            loaders.append(jinja2.FileSystemLoader(module_template_dir))

        loaders.append(jinja2.FileSystemLoader(core_template_dir))

        self._env = jinja2.Environment(
            loader=jinja2.ChoiceLoader(loaders),
            autoescape=True,
        )
        self._env.globals["owlbot_version"] = __version__

    def render(self, template_name: str, **context: Any) -> str:
        """Load and render a template by name.

        :param template_name: Name of the template file (e.g. ``"list.html"``).
        :param context: Variables to pass to the template.
        :return: The rendered template string.
        """
        template = self._env.get_template(template_name)
        return template.render(**context)

    @property
    def env(self) -> jinja2.Environment:
        """The underlying Jinja2 Environment for advanced use.

        Use this to register custom filters, tests, or globals.
        """
        return self._env
