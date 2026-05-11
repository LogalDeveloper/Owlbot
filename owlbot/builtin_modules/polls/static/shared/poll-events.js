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

function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseEventData(event) {
    try {
        return JSON.parse(event.data);
    } catch {
        return null;
    }
}

export function isKeepalivePayload(value) {
    return isRecord(value) && Number.isFinite(value.time_remaining);
}

export function isPollEndedPayload(value) {
    return (
        isRecord(value) &&
        typeof value.results_url === "string" &&
        value.results_url.length > 0
    );
}

export function isTallyPayload(value, optionCount) {
    return (
        isRecord(value) &&
        Array.isArray(value.counts) &&
        value.counts.length === optionCount &&
        value.counts.every((count) => Number.isInteger(count) && count >= 0)
    );
}

export class PollEventStream {
    constructor(url, { onClosed } = {}) {
        this.source = new EventSource(url);
        this.onClosed = onClosed;

        this.source.addEventListener("error", () => {
            if (this.source.readyState === EventSource.CLOSED) {
                this.close();
                if (this.onClosed) {
                    this.onClosed();
                }
            }
        });
    }

    on(eventName, handler, validator = () => true) {
        this.source.addEventListener(eventName, (event) => {
            const data = parseEventData(event);
            if (validator(data)) {
                handler(data);
            }
        });
    }

    close() {
        this.source.close();
    }
}
