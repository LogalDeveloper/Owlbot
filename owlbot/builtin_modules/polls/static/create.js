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

import { readJsonConfig } from "./shared/config.js";
import {
    createTextElement,
    requireElement,
    setHidden,
} from "./shared/dom.js";

class CreatePollForm {
    constructor(root, config) {
        this.root = root;
        this.config = config;
        this.form = requireElement(root, "#poll-form", HTMLFormElement);
        this.container = requireElement(root, "#options-container", HTMLElement);
        this.addButton = requireElement(root, "#add-option", HTMLButtonElement);
        this.multiCheckbox = requireElement(
            root,
            "#allow_multiple",
            HTMLInputElement
        );
        this.multiOptions = requireElement(
            root,
            "#multi-select-options",
            HTMLElement
        );
        this.minSelectionsInput = requireElement(
            root,
            "#min_selections",
            HTMLInputElement
        );
        this.maxSelectionsInput = requireElement(
            root,
            "#max_selections",
            HTMLInputElement
        );
    }

    start() {
        this.ensureMinimumOptions();
        this.renumberOptions();
        this.setMultiSelectEnabled(this.multiCheckbox.checked);
        this.bindEvents();
    }

    bindEvents() {
        this.addButton.addEventListener("click", () => this.addOption());
        this.container.addEventListener("click", (event) =>
            this.removeOption(event)
        );
        this.multiCheckbox.addEventListener("change", () =>
            this.setMultiSelectEnabled(this.multiCheckbox.checked)
        );
        this.minSelectionsInput.addEventListener("input", () =>
            this.updateSelectionConstraints()
        );
        this.maxSelectionsInput.addEventListener("input", () =>
            this.updateSelectionConstraints()
        );
        this.form.addEventListener("submit", () =>
            this.updateSelectionConstraints()
        );
    }

    optionRows() {
        return Array.from(this.container.querySelectorAll(".option-row"));
    }

    ensureMinimumOptions() {
        while (this.optionRows().length < this.config.minOptions) {
            this.container.appendChild(this.buildOptionRow());
        }
    }

    addOption() {
        if (this.optionRows().length >= this.config.maxOptions) {
            return;
        }
        this.container.appendChild(this.buildOptionRow());
        this.renumberOptions();
    }

    removeOption(event) {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }

        const button = target.closest(".remove-option");
        if (button === null || !this.container.contains(button)) {
            return;
        }

        const row = button.closest(".option-row");
        if (row === null || this.optionRows().length <= this.config.minOptions) {
            return;
        }

        row.remove();
        this.renumberOptions();
    }

    buildOptionRow() {
        const row = document.createElement("div");
        row.className = "input-group mb-2 option-row";

        const number = this.optionRows().length + 1;
        const numberLabel = createTextElement(
            "span",
            "input-group-text option-number",
            String(number)
        );
        const input = document.createElement("input");
        input.type = "text";
        input.className = "form-control option-input";
        input.name = "options";
        input.required = true;
        input.maxLength = this.config.maxOptionLength;
        input.placeholder = `Option ${number}`;

        row.append(numberLabel, input);

        if (number > this.config.minOptions) {
            const removeButton = createTextElement(
                "button",
                "btn btn-outline-danger remove-option",
                "Remove"
            );
            removeButton.type = "button";
            row.appendChild(removeButton);
        }

        return row;
    }

    renumberOptions() {
        const rows = this.optionRows();
        for (const [index, row] of rows.entries()) {
            const number = index + 1;
            requireElement(row, ".option-number", HTMLElement).textContent =
                String(number);
            requireElement(row, ".option-input", HTMLInputElement).placeholder =
                `Option ${number}`;
        }

        this.addButton.disabled = rows.length >= this.config.maxOptions;
        this.updateSelectionConstraints();
    }

    setMultiSelectEnabled(enabled) {
        this.minSelectionsInput.disabled = !enabled;
        this.maxSelectionsInput.disabled = !enabled;
        setHidden(this.multiOptions, !enabled);

        if (!enabled) {
            this.minSelectionsInput.value = "";
            this.maxSelectionsInput.value = "";
            this.minSelectionsInput.setCustomValidity("");
            this.maxSelectionsInput.setCustomValidity("");
            return;
        }

        this.updateSelectionConstraints();
    }

    updateSelectionConstraints() {
        const optionCount = this.optionRows().length;
        const minValue = this.parseSelectionValue(this.minSelectionsInput);
        const maxValue = this.parseSelectionValue(this.maxSelectionsInput);

        this.minSelectionsInput.max = String(optionCount);
        this.maxSelectionsInput.max = String(optionCount);
        this.maxSelectionsInput.placeholder = String(optionCount);
        this.maxSelectionsInput.min =
            Number.isInteger(minValue) && minValue >= 1 ? String(minValue) : "1";

        this.minSelectionsInput.setCustomValidity("");
        this.maxSelectionsInput.setCustomValidity("");

        if (
            !this.multiCheckbox.checked ||
            !Number.isInteger(minValue) ||
            !Number.isInteger(maxValue)
        ) {
            return;
        }

        if (minValue > maxValue) {
            this.maxSelectionsInput.setCustomValidity(
                "Maximum selections must be at least the minimum."
            );
        }
    }

    parseSelectionValue(input) {
        if (input.value.trim() === "") {
            return null;
        }
        return Number(input.value);
    }
}

const config = readJsonConfig("create-config");
new CreatePollForm(document, config).start();
