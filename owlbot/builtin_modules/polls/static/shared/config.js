// Copyright 2026 Logan Fick
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

export function readJsonConfig(id) {
    const script = document.getElementById(id);
    if (!(script instanceof HTMLScriptElement)) {
        throw new Error(`Missing JSON config script: #${id}`);
    }

    try {
        return JSON.parse(script.textContent || "{}");
    } catch (error) {
        throw new Error(`Invalid JSON config script: #${id}`, { cause: error });
    }
}
