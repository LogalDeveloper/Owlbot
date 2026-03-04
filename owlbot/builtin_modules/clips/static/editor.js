(function() {
    // Configuration
    const cfg = window.EDITOR_CONFIG;
    const DURATION = cfg.duration;
    const MIN_LENGTH = cfg.minLength;
    const MAX_LENGTH = cfg.maxLength;

    // DOM References
    const video = document.getElementById("video");
    const slider = document.getElementById("slider");
    const track = document.getElementById("track");
    const handleStart = document.getElementById("handle-start");
    const handleEnd = document.getElementById("handle-end");
    const startTimeEl = document.getElementById("start-time");
    const endTimeEl = document.getElementById("end-time");
    const clipLengthEl = document.getElementById("clip-length");
    const playhead = document.getElementById("playhead");
    const btnPreview = document.getElementById("btn-preview");
    const pctEl = document.getElementById("loading-pct");
    const loadingEl = document.getElementById("loading");

    // Utilities
    function formatTime(s) {
        s = Math.round(s);
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return m + ":" + (sec < 10 ? "0" : "") + sec;
    }

    function clamp(val, min, max) {
        return Math.max(min, Math.min(max, val));
    }

    function pctToTime(pct) {
        return Math.round(clamp(pct, 0, 1) * DURATION);
    }

    function getSliderPct(e) {
        const rect = slider.getBoundingClientRect();
        return (e.clientX - rect.left) / rect.width;
    }

    // Video Loading
    function showLoadError(message) {
        const alert = document.createElement("div");
        alert.className = "alert alert-danger";
        alert.setAttribute("role", "alert");
        alert.textContent = message;

        const link = document.createElement("a");
        link.href = cfg.listUrl;
        link.textContent = "Return to clips list";

        loadingEl.textContent = "";
        loadingEl.appendChild(alert);
        loadingEl.appendChild(link);
    }

    const xhr = new XMLHttpRequest();
    xhr.open("GET", cfg.previewUrl);
    xhr.responseType = "blob";
    xhr.onprogress = function(e) {
        if (e.lengthComputable) {
            pctEl.textContent = Math.round((e.loaded / e.total) * 100) + "%";
        }
    };
    xhr.onerror = function() {
        showLoadError("Failed to load the video preview. Please check your connection and try again.");
    };
    xhr.onload = function() {
        if (xhr.status !== 200) {
            showLoadError("This editor session has expired or is no longer available.");
            return;
        }
        video.src = URL.createObjectURL(xhr.response);
        video.addEventListener("loadedmetadata", function() {
            video.currentTime = startTime;
        }, { once: true });
        loadingEl.classList.add("d-none");
        document.getElementById("editor-controls").classList.remove("d-none");
    };
    xhr.send();

    // Slider State & UI
    const clipLength = Math.min(MAX_LENGTH, DURATION);
    let startTime = DURATION - clipLength;
    let endTime = DURATION;

    function updateUI() {
        const startPct = (startTime / DURATION) * 100;
        const endPct = (endTime / DURATION) * 100;
        handleStart.style.left = startPct + "%";
        handleEnd.style.left = endPct + "%";
        track.style.left = startPct + "%";
        track.style.width = (endPct - startPct) + "%";
        startTimeEl.textContent = formatTime(startTime);
        endTimeEl.textContent = formatTime(endTime);
        clipLengthEl.textContent = formatTime(endTime - startTime);
    }

    function updatePlayhead() {
        const pct = (video.currentTime / DURATION) * 100;
        playhead.style.left = pct + "%";
    }

    // Slider Interaction
    let dragging = null;
    let dragOffset = 0;

    function onPointerDown(e) {
        const target = e.target;
        if (target === handleStart) {
            dragging = "start";
        } else if (target === handleEnd) {
            dragging = "end";
        } else if (target === track) {
            dragging = "track";
            dragOffset = pctToTime(getSliderPct(e)) - startTime;
        }
        if (dragging) {
            e.preventDefault();
            if (previewActive) {
                video.pause();
                setPreviewActive(false);
            }
            document.addEventListener("pointermove", onPointerMove);
            document.addEventListener("pointerup", onPointerUp);
        }
    }

    function onPointerMove(e) {
        const t = pctToTime(getSliderPct(e));
        const length = endTime - startTime;

        if (dragging === "start") {
            startTime = clamp(t, 0, DURATION - MIN_LENGTH);
            if (endTime - startTime < MIN_LENGTH) endTime = startTime + MIN_LENGTH;
            if (endTime - startTime > MAX_LENGTH) endTime = startTime + MAX_LENGTH;
            endTime = Math.min(endTime, DURATION);
        } else if (dragging === "end") {
            endTime = clamp(t, MIN_LENGTH, DURATION);
            if (endTime - startTime < MIN_LENGTH) startTime = endTime - MIN_LENGTH;
            if (endTime - startTime > MAX_LENGTH) startTime = endTime - MAX_LENGTH;
            startTime = Math.max(startTime, 0);
        } else if (dragging === "track") {
            let newStart = t - dragOffset;
            newStart = clamp(newStart, 0, DURATION - length);
            startTime = Math.round(newStart);
            endTime = startTime + length;
        }

        video.currentTime = dragging === "end" ? endTime : startTime;
        updateUI();
    }

    function onPointerUp() {
        dragging = null;
        document.removeEventListener("pointermove", onPointerMove);
        document.removeEventListener("pointerup", onPointerUp);
    }

    // Preview Playback
    let previewActive = false;

    function setPreviewActive(active) {
        previewActive = active;
        if (active) {
            btnPreview.textContent = "Stop Preview";
            btnPreview.classList.remove("btn-primary");
            btnPreview.classList.add("btn-danger");
        } else {
            btnPreview.textContent = "Preview Clip";
            btnPreview.classList.remove("btn-danger");
            btnPreview.classList.add("btn-primary");
        }
    }

    // Form Submission
    function onCreateClick() {
        document.getElementById("editor-controls").classList.add("d-none");
        document.getElementById("processing").classList.remove("d-none");
        document.title = "Creating Clip...";
        document.querySelector(".breadcrumb-item.active").textContent = "Creating Clip";
        document.querySelector("h1").textContent = "Creating Clip";
        document.getElementById("form-start").value = startTime;
        document.getElementById("form-end").value = endTime;
        document.getElementById("form-title").value = document.getElementById("title").value;
        document.getElementById("submit-form").submit();
    }

    // Event Bindings
    slider.addEventListener("pointerdown", onPointerDown);

    btnPreview.addEventListener("click", function() {
        if (previewActive) {
            video.pause();
            setPreviewActive(false);
        } else {
            video.currentTime = startTime;
            video.play();
            setPreviewActive(true);
        }
    });

    video.addEventListener("timeupdate", function() {
        updatePlayhead();
        if (previewActive && video.currentTime >= endTime) {
            video.pause();
            setPreviewActive(false);
        }
    });

    document.getElementById("btn-create").addEventListener("click", onCreateClick);

    // Initialization
    updateUI();
})();
