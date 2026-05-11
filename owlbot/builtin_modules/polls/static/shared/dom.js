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

export function requireElement(root, selector, elementType = Element) {
    const element = root.querySelector(selector);
    if (element === null) {
        throw new Error(`Missing required element: ${selector}`);
    }
    if (!(element instanceof elementType)) {
        throw new Error(`Element ${selector} is not a ${elementType.name}`);
    }
    return element;
}

export function createTextElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) {
        element.className = className;
    }
    element.textContent = text;
    return element;
}

export function setHidden(element, hidden) {
    element.classList.toggle("d-none", hidden);
}

export function showAlert(container, message, variant = "danger") {
    const alert = document.createElement("div");
    alert.className = `alert alert-${variant} mb-0`;
    alert.setAttribute("role", "alert");
    alert.textContent = message;

    container.replaceChildren(alert);
}

export function disableControls(root) {
    for (const control of root.querySelectorAll("button, input, select, textarea")) {
        control.disabled = true;
    }
}
