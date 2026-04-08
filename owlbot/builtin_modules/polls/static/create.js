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

(() => {
    "use strict";

    const config = window.CREATE_CONFIG;
    if (!config) return;

    const container = document.getElementById('options-container');
    const addBtn = document.getElementById('add-option');
    const multiCheck = document.getElementById('allow_multiple');
    const multiOpts = document.getElementById('multi-select-options');
    const form = document.getElementById('poll-form');
    const maxOptions = config.maxOptions;
    const maxOptionLength = config.maxOptionLength;
    const minSel = document.getElementById('min_selections');
    const maxSel = document.getElementById('max_selections');

    const syncSelectionLimits = () => {
        const minVal = parseInt(minSel.value, 10);
        const maxVal = parseInt(maxSel.value, 10);
        maxSel.min = minVal >= 1 ? minVal : 1;
        minSel.max = maxVal >= 1 ? maxVal : minSel.max;
    };

    const updateSelectionDefaults = () => {
        const count = container.querySelectorAll('.option-row').length;
        minSel.max = count;
        maxSel.max = count;
        maxSel.placeholder = count;
        if (minSel.value && parseInt(minSel.value, 10) > count) {
            minSel.value = count;
        }
        if (maxSel.value && parseInt(maxSel.value, 10) > count) {
            maxSel.value = count;
        }
        syncSelectionLimits();
    };

    minSel.addEventListener('input', syncSelectionLimits);
    maxSel.addEventListener('input', syncSelectionLimits);

    const renumber = () => {
        const rows = container.querySelectorAll('.option-row');
        for (const [i, row] of [...rows].entries()) {
            const num = i + 1;
            row.querySelector('.option-number').textContent = num;
            const input = row.querySelector('.option-input');
            input.name = `option_${num}`;
            input.placeholder = `Option ${num}`;
        }
        addBtn.disabled = rows.length >= maxOptions;
        updateSelectionDefaults();
    };

    addBtn.addEventListener('click', () => {
        const rows = container.querySelectorAll('.option-row');
        if (rows.length >= maxOptions) return;
        const num = rows.length + 1;
        const div = document.createElement('div');
        div.className = 'input-group mb-2 option-row';
        div.innerHTML =
            `<span class="input-group-text option-number">${num}</span>` +
            `<input type="text" class="form-control option-input" name="option_${num}" required maxlength="${maxOptionLength}" placeholder="Option ${num}">` +
            '<button type="button" class="btn btn-outline-danger remove-option">Remove</button>';
        container.appendChild(div);
        renumber();
    });

    container.addEventListener('click', (e) => {
        if (e.target.classList.contains('remove-option')) {
            e.target.closest('.option-row').remove();
            renumber();
        }
    });

    multiCheck.addEventListener('change', () => {
        if (multiCheck.checked) {
            updateSelectionDefaults();
            multiOpts.classList.remove('d-none');
        } else {
            multiOpts.classList.add('d-none');
            minSel.value = '';
            maxSel.value = '';
        }
    });

    form.addEventListener('submit', () => {
        const lines = [...container.querySelectorAll('.option-input')]
            .map(input => input.value.trim())
            .filter(Boolean);
        document.getElementById('options-hidden').value = lines.join('\n');
    });
})();
