(function () {
    "use strict";

    const easings = ["ease", "ease-in", "ease-out", "ease-in-out", "linear"];
    const activeEmojis = [];
    const DEG_TO_RAD = Math.PI / 180;

    function randomBetween(min, max) {
        return Math.random() * (max - min) + min;
    }

    function computeTrajectory(angleDeg) {
        // 0 = up, 90 = right, 180 = down, 270 = left (clockwise).
        const rad = angleDeg * DEG_TO_RAD;
        const dx = Math.sin(rad);
        const dy = -Math.cos(rad);

        // Starting position: opposite edge, randomized along that edge.
        // Ending position: off-screen in travel direction.
        // Units are in vw/vh for viewport-relative positioning.
        const travel = 120;
        let startX, startY;
        const absDx = Math.abs(dx);
        const absDy = Math.abs(dy);

        if (absDy >= absDx) {
            // Primarily vertical movement.
            startX = Math.random() * 100;
            startY = dy < 0 ? 100 : -10;
        } else {
            // Primarily horizontal movement.
            startX = dx > 0 ? -10 : 100;
            startY = Math.random() * 100;
        }

        // Drift perpendicular to travel direction.
        const drift = (Math.random() - 0.5) * 15;
        const perpDx = -dy;
        const perpDy = dx;

        return {
            startX,
            startY,
            endDx: dx * travel + perpDx * drift,
            endDy: dy * travel + perpDy * drift
        };
    }

    function spawnEmoji(element) {
        const size = randomBetween(SETTINGS.min_size, SETTINGS.max_size);
        const duration = randomBetween(SETTINGS.min_duration, SETTINGS.max_duration);
        const delay = Math.random() * 1.5;
        const easing = easings[Math.floor(Math.random() * easings.length)];

        const angle = SETTINGS.direction === "random"
            ? Math.floor(Math.random() * 360)
            : SETTINGS.direction;
        const traj = computeTrajectory(angle);

        let rotStart = 0;
        let rotEnd = 0;
        if (SETTINGS.max_rotation > 0) {
            rotStart = randomBetween(-SETTINGS.max_rotation, SETTINGS.max_rotation);
            rotEnd = randomBetween(-SETTINGS.max_rotation, SETTINGS.max_rotation);
        }

        element.classList.add("emoji_item");
        element.style.left = `${traj.startX}vw`;
        element.style.top = `${traj.startY}vh`;

        if (element.tagName === "IMG") {
            element.style.width = `${size}px`;
            element.style.height = `${size}px`;
        } else {
            element.style.fontSize = `${size}px`;
        }

        // Enforce max count by removing oldest.
        while (activeEmojis.length >= SETTINGS.max_count) {
            const oldest = activeEmojis.shift();
            if (oldest) {
                oldest.remove();
            }
        }

        activeEmojis.push(element);
        document.body.append(element);

        const keyframes = [
            {
                transform: `translate(0, 0) rotate(${rotStart}deg)`,
                opacity: 1
            },
            {
                transform: `translate(${traj.endDx}vw, ${traj.endDy}vh) rotate(${rotEnd}deg)`,
                opacity: 0
            }
        ];
        const timing = {
            duration: duration * 1000,
            delay: delay * 1000,
            easing,
            fill: "forwards"
        };

        const animation = element.animate(keyframes, timing);
        animation.onfinish = () => {
            element.remove();
            const index = activeEmojis.indexOf(element);
            if (index !== -1) {
                activeEmojis.splice(index, 1);
            }
        };
    }

    function handleEvent(data) {
        let element;
        if (data.type === "unicode") {
            element = document.createElement("span");
            element.textContent = data.emoji;
        } else if (data.type === "emote") {
            element = document.createElement("img");
            element.src = data.url;
            element.alt = "emote";
        } else {
            return;
        }
        spawnEmoji(element);
    }

    const RECONNECT_DELAY = 5000;

    function connect() {
        const source = new EventSource(EVENTS_URL);
        source.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleEvent(data);
            } catch (e) {
                // Ignore malformed events.
            }
        };
        source.onerror = () => {
            // EventSource auto-reconnects on transient network errors
            // (readyState stays CONNECTING). However, HTTP-level errors
            // like 502/503 from a reverse proxy set readyState to CLOSED
            // and the browser gives up. Reconnect manually so the overlay
            // survives bot restarts behind a proxy.
            if (source.readyState === EventSource.CLOSED) {
                source.close();
                setTimeout(connect, RECONNECT_DELAY);
            }
        };
    }

    connect();
})();
