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

    const config = window.POLL_CONFIG;
    const viewerInfo = window.VIEWER_INFO;
    if (!config || !viewerInfo) return;

    const timerEl = document.getElementById("timer");
    let remaining = config.timeRemaining;

    // --- Countdown timer ---

    const formatTime = (seconds) => {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${String(s).padStart(2, "0")}`;
    };

    const updateTimer = () => {
        if (remaining <= 0) {
            timerEl.textContent = "0:00";
            return;
        }
        timerEl.textContent = formatTime(remaining);
        remaining--;
    };

    updateTimer();
    const timerInterval = setInterval(() => {
        updateTimer();
        if (remaining < 0) {
            clearInterval(timerInterval);
        }
    }, 1000);

    // --- Update vote bars ---

    const updateBars = (counts) => {
        const total = counts.reduce((a, b) => a + b, 0);
        for (const bar of document.querySelectorAll(".option-bar")) {
            const idx = Number(bar.dataset.index);
            const fill = bar.querySelector(".bar-fill");
            const countSpan = bar.querySelector(".vote-count");
            const pct = total > 0 ? Math.round((counts[idx] / total) * 100) : 0;
            if (fill && counts[idx] !== undefined) {
                fill.style.width = `${pct}%`;
            }
            if (countSpan) {
                const countEl = countSpan.querySelector(".count");
                const pctEl = countSpan.querySelector(".pct");
                if (countEl) countEl.textContent = counts[idx];
                if (pctEl) pctEl.textContent = pct;
                countSpan.classList.remove("d-none");
            }
        }
    };

    // Show vote counts immediately for non-hidden polls so the UI
    // doesn't look identical to a hidden poll before SSE delivers data.
    if (!config.hidden || viewerInfo.isModerator) {
        for (const el of document.querySelectorAll(".vote-count")) {
            el.classList.remove("d-none");
        }
    }

    // --- SSE connection ---

    const evtSource = new EventSource(viewerInfo.eventsUrl);

    const parse = (e) => {
        try {
            return JSON.parse(e.data);
        } catch {
            return null;
        }
    };

    evtSource.addEventListener("error", () => {
        if (evtSource.readyState === EventSource.CLOSED) {
            clearInterval(timerInterval);
            const status = document.getElementById("poll-status");
            if (status) {
                status.innerHTML =
                    '<div class="alert alert-danger mb-0" role="alert">' +
                    "Connection lost. Results are not live. Refresh to reconnect.</div>";
            }
        }
    });

    evtSource.addEventListener("keepalive", (e) => {
        const data = parse(e);
        if (data && typeof data.time_remaining === "number") {
            remaining = data.time_remaining;
        }
    });

    evtSource.addEventListener("tally", (e) => {
        const data = parse(e);
        if (data) {
            updateBars(data.counts);
        }
    });

    evtSource.addEventListener("poll_ended", (e) => {
        const data = parse(e);
        clearInterval(timerInterval);
        evtSource.close();
        if (data?.results_url) {
            window.location.href = data.results_url;
        }
    });

    evtSource.addEventListener("poll_cancelled", () => {
        clearInterval(timerInterval);
        evtSource.close();
        const status = document.getElementById("poll-status");
        if (status) {
            status.innerHTML =
                '<div class="alert alert-danger mb-0" role="alert">' +
                "This poll has been cancelled.</div>";
        }
        const form = document.getElementById("vote-form");
        if (form) {
            for (const input of form.querySelectorAll(
                "input, button[type='submit']"
            )) {
                input.disabled = true;
            }
        }
    });

    // --- Multi-select validation ---

    const voteForm = document.getElementById("vote-form");
    if (voteForm && config.allowMultiple) {
        voteForm.addEventListener("submit", (e) => {
            const checked = voteForm.querySelectorAll(
                'input[name="option"]:checked'
            );
            const count = checked.length;

            if (count === 0) {
                e.preventDefault();
                alert("Please select at least one option.");
                return;
            }

            if (
                config.minSelections !== null &&
                count < config.minSelections
            ) {
                e.preventDefault();
                alert(
                    `Please select at least ${config.minSelections} option(s).`
                );
                return;
            }

            if (
                config.maxSelections !== null &&
                count > config.maxSelections
            ) {
                e.preventDefault();
                alert(
                    `Please select at most ${config.maxSelections} option(s).`
                );
                return;
            }
        });
    }
})();
