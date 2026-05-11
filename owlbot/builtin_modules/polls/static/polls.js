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
    disableControls,
    requireElement,
    showAlert,
} from "./shared/dom.js";
import {
    isKeepalivePayload,
    isPollEndedPayload,
    isTallyPayload,
    PollEventStream,
} from "./shared/poll-events.js";

function normalizeSeconds(value) {
    return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

function formatTime(seconds) {
    const safeSeconds = normalizeSeconds(seconds);
    const minutes = Math.floor(safeSeconds / 60);
    const remainingSeconds = safeSeconds % 60;
    return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

class CountdownTimer {
    constructor(element, initialSeconds) {
        this.element = element;
        this.remaining = normalizeSeconds(initialSeconds);
        this.intervalId = null;
    }

    start() {
        this.render();
        this.ensureInterval();
    }

    stop() {
        if (this.intervalId === null) {
            return;
        }
        window.clearInterval(this.intervalId);
        this.intervalId = null;
    }

    setRemaining(seconds) {
        this.remaining = normalizeSeconds(seconds);
        this.render();
        if (this.remaining > 0) {
            this.ensureInterval();
        } else {
            this.stop();
        }
    }

    ensureInterval() {
        if (this.intervalId !== null || this.remaining <= 0) {
            return;
        }
        this.intervalId = window.setInterval(() => this.tick(), 1000);
    }

    tick() {
        if (this.remaining <= 0) {
            this.stop();
            this.render();
            return;
        }

        this.remaining -= 1;
        this.render();

        if (this.remaining <= 0) {
            this.stop();
        }
    }

    render() {
        this.element.textContent = formatTime(this.remaining);
    }
}

class ResultsBars {
    constructor(root) {
        this.rows = Array.from(root.querySelectorAll(".option-bar")).map(
            (row) => ({
                index: Number(row.dataset.index),
                fill: row.querySelector(".bar-fill"),
                countWrapper: row.querySelector(".vote-count"),
                count: row.querySelector(".count"),
                percent: row.querySelector(".pct"),
            })
        );
    }

    showCounts() {
        for (const row of this.rows) {
            row.countWrapper?.classList.remove("d-none");
        }
    }

    update(counts) {
        const total = counts.reduce((sum, count) => sum + count, 0);

        for (const row of this.rows) {
            const count = counts[row.index] ?? 0;
            const percent = total > 0 ? Math.round((count / total) * 100) : 0;

            if (row.fill !== null) {
                row.fill.style.width = `${percent}%`;
            }
            if (row.count !== null) {
                row.count.textContent = String(count);
            }
            if (row.percent !== null) {
                row.percent.textContent = String(percent);
            }
            row.countWrapper?.classList.remove("d-none");
        }
    }
}

class VotePage {
    constructor(root, config) {
        this.root = root;
        this.config = config;
        this.status = requireElement(root, "#poll-status", HTMLElement);
        this.timer = new CountdownTimer(
            requireElement(root, "#timer", HTMLElement),
            config.timeRemaining
        );
        this.bars = new ResultsBars(root);
        this.stream = null;
    }

    start() {
        this.timer.start();

        if (!this.config.hidden || this.config.isModerator) {
            this.bars.showCounts();
        }

        this.stream = new PollEventStream(this.config.eventsUrl, {
            onClosed: () => this.handleConnectionClosed(),
        });
        this.stream.on(
            "keepalive",
            (data) => this.timer.setRemaining(data.time_remaining),
            isKeepalivePayload
        );
        this.stream.on(
            "tally",
            (data) => this.bars.update(data.counts),
            (data) => isTallyPayload(data, this.config.optionCount)
        );
        this.stream.on(
            "poll_ended",
            (data) => this.handlePollEnded(data),
            isPollEndedPayload
        );
        this.stream.on("poll_cancelled", () => this.handlePollCancelled());
    }

    stop() {
        this.timer.stop();
        if (this.stream !== null) {
            this.stream.close();
            this.stream = null;
        }
    }

    handleConnectionClosed() {
        this.timer.stop();
        showAlert(
            this.status,
            "Connection lost. Results are not live. Refresh to reconnect."
        );
    }

    handlePollEnded(data) {
        this.stop();
        window.location.href = data.results_url;
    }

    handlePollCancelled() {
        this.stop();
        showAlert(this.status, "This poll has been cancelled.");
        disableControls(this.root);
    }
}

const config = readJsonConfig("poll-config");
new VotePage(document, config).start();
