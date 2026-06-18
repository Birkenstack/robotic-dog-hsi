/* ═══════════════════════════════════════════════════════════
   Petoi Bittle X — AI Controller App Logic
   Voice recognition, camera face tracking, and serial bridge
   ═══════════════════════════════════════════════════════════ */

const API_BASE = 'http://localhost:8080/api';

// ─── State ────────────────────────────────────────────────
const state = {
    connected: false,
    listening: false,
    cameraActive: false,
    recognition: null,
    cameraStream: null,
    trackingLoop: null,
    lastPan: 0,
    lastTilt: 0,
    lastTrackSend: 0,
    processing: false,  // Prevent overlapping voice commands
    prevFrame: null,     // For motion detection fallback
};

// ─── DOM Elements ─────────────────────────────────────────
const $ = id => document.getElementById(id);
const els = {
    micContainer: $('mic-container'),
    btnMic: $('btn-mic'),
    micStatus: $('mic-status'),
    liveTranscript: $('live-transcript'),
    robotEmoji: $('robot-emoji'),
    responseBubble: $('response-bubble'),
    cameraFeed: $('camera-feed'),
    cameraOverlay: $('camera-overlay'),
    btnStartCamera: $('btn-start-camera'),
    trackingStatus: $('tracking-status'),
    trackingSpeed: $('tracking-speed'),
    panRange: $('pan-range'),
    tiltRange: $('tilt-range'),
    gaugePan: $('gauge-pan'),
    gaugePanMarker: $('gauge-pan-marker'),
    gaugeTilt: $('gauge-tilt'),
    gaugeTiltMarker: $('gauge-tilt-marker'),
    servoPanValue: $('servo-pan-value'),
    servoTiltValue: $('servo-tilt-value'),
    manualInput: $('manual-input'),
    btnSendManual: $('btn-send-manual'),
    statusBt: $('status-bt'),
    statusBtLabel: $('status-bt-label'),
    btnEmergency: $('btn-emergency'),
    logEntries: $('log-entries'),
    btnClearLog: $('btn-clear-log'),
};

// ─── Logging ──────────────────────────────────────────────
function addLog(msg, type = 'system') {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-msg">${msg}</span>`;
    els.logEntries.appendChild(entry);
    els.logEntries.scrollTop = els.logEntries.scrollHeight;

    // Keep log manageable
    while (els.logEntries.children.length > 200) {
        els.logEntries.removeChild(els.logEntries.firstChild);
    }
}

// ─── Status Check ─────────────────────────────────────────
async function checkStatus() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();
        state.connected = data.connected;
        els.statusBt.className = `status-dot ${data.connected ? 'connected' : 'error'}`;
        els.statusBtLabel.textContent = data.connected ? `${data.port} ✓` : 'Disconnected';
    } catch (e) {
        els.statusBt.className = 'status-dot error';
        els.statusBtLabel.textContent = 'Server offline';
    }
}

// ─── Emergency Stop ───────────────────────────────────────
async function emergencyStop() {
    addLog('⛔ EMERGENCY STOP', 'error');
    try {
        await fetch(`${API_BASE}/stop`, { method: 'POST' });
        addLog('Servos disabled', 'system');
    } catch (e) {
        addLog('Failed to send stop!', 'error');
    }
}

els.btnEmergency.addEventListener('click', emergencyStop);
document.addEventListener('keydown', e => { if (e.key === 'Escape') emergencyStop(); });

// ─── Emotion Map ──────────────────────────────────────────
const EMOJIS = {
    happy: '🐕', curious: '🐶', alert: '🐕‍🦺',
    playful: '🐩', sleepy: '😴', confused: '🤔'
};

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function setTranscriptState(text = '', status = '', tone = '') {
    const safeText = escapeHtml(text);
    const safeStatus = escapeHtml(status);
    const statusMarkup = safeStatus
        ? `<span class="transcript-status ${tone}">${safeStatus}</span>`
        : '';

    els.liveTranscript.innerHTML = safeText
        ? `<span class="transcript-text">${safeText}</span>${statusMarkup}`
        : '';
    els.liveTranscript.classList.toggle('has-content', Boolean(safeText));
}

function renderTrace(trace) {
    if (!trace || !trace.skill_id) return '';

    const confidence = typeof trace.confidence === 'number'
        ? `${Math.round(trace.confidence * 100)}%`
        : 'n/a';
    const plan = Array.isArray(trace.steps) && trace.steps.length > 0
        ? trace.steps.join(' -> ')
        : trace.skill_id;

    return `
        <div class="cmd-tags" style="margin-top:10px">
            <span class="cmd-tag">Plan: ${plan}</span>
            <span class="cmd-tag">Planner: ${trace.planner_source || 'rule'}</span>
            <span class="cmd-tag">Confidence: ${confidence}</span>
        </div>
        <p style="font-size:11px;color:var(--text-muted);margin-top:6px">
            ${trace.rationale || 'The system matched the prompt to available safe skills.'}
        </p>
    `;
}

function renderSentiment(sentiment) {
    if (!sentiment || !sentiment.label) return '';

    return `
        <p style="font-size:11px;color:var(--text-muted);margin-top:6px">
            Support cue: ${sentiment.label} (${sentiment.score})
        </p>
    `;
}

// ═══════════════════════════════════════════════════════════
//  VOICE MODE
// ═══════════════════════════════════════════════════════════

function initVoiceRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
        els.micStatus.textContent = 'Not supported — use Chrome';
        addLog('Web Speech API unavailable. Use Chrome.', 'error');
        return;
    }

    const recog = new SR();
    recog.continuous = true;
    recog.interimResults = true;
    recog.lang = 'en-US';

    recog.onstart = () => {
        state.listening = true;
        els.micContainer.classList.add('listening');
        els.micStatus.textContent = 'Listening... speak a command';
    };

    recog.onend = () => {
        if (state.listening) {
            // Auto-restart
            setTimeout(() => {
                try { recog.start(); } catch (e) { /* ignore */ }
            }, 100);
        } else {
            els.micContainer.classList.remove('listening');
            els.micStatus.textContent = 'Click to start listening';
        }
    };

    recog.onerror = (e) => {
        if (e.error === 'no-speech' || e.error === 'aborted') return;
        addLog(`Mic error: ${e.error}`, 'error');
    };

    recog.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const transcript = event.results[i][0].transcript.trim();
            if (event.results[i].isFinal && transcript.length > 0) {
                if (!state.processing) {
                    processVoiceCommand(transcript);
                }
            } else {
                els.liveTranscript.textContent = transcript;
                els.liveTranscript.style.opacity = '0.5';
            }
        }
    };

    state.recognition = recog;
}

async function processVoiceCommand(text) {
    state.processing = true;
    setTranscriptState(`"${text}"`, 'Thinking...', 'pending');
    addLog(`🎤 "${text}"`, 'voice');

    try {
        const res = await fetch(`${API_BASE}/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });

        const data = await res.json();

        // Update UI
        els.robotEmoji.textContent = EMOJIS[data.emotion] || '🐕';

        let html = `<p>${data.explanation}</p>`;
        if (data.commands?.length > 0) {
            html += '<div class="cmd-tags">';
            data.commands.forEach(cmd => { html += `<span class="cmd-tag">${cmd}</span>`; });
            html += '</div>';
            if (data.delay > 0 && data.commands.length > 1) {
                html += `<p style="font-size:11px;color:var(--text-muted);margin-top:6px">⏱ ${data.delay}s delay between actions</p>`;
            }
        }
        html += renderSentiment(data.sentiment);
        html += renderTrace(data.trace);
        els.responseBubble.innerHTML = html;
        setTranscriptState(`"${text}"`, 'Done', 'done');

        // Log everything
        data.commands?.forEach(cmd => addLog(`➤ ${cmd}`, 'cmd'));
        if (data.explanation) addLog(`🐾 ${data.explanation}`, 'robot');
        if (data.sentiment?.label) {
            addLog(`💬 sentiment=${data.sentiment.label} affect=${data.sentiment.affect} score=${data.sentiment.score}`, 'system');
        }
        if (data.trace?.skill_id) {
            const planLabel = Array.isArray(data.trace.steps) && data.trace.steps.length > 0
                ? data.trace.steps.join(' -> ')
                : data.trace.skill_id;
            addLog(`🧠 plan=${planLabel} via ${data.trace.planner_source || 'rule'} (${Math.round((data.trace.confidence || 0) * 100)}%)`, 'system');
        }

        data.serial_responses?.forEach(sr => {
            if (sr.blocked) {
                addLog(`🚫 ${sr.cmd} — ${sr.reason}`, 'error');
            } else if (sr.success) {
                addLog(`✅ ${sr.cmd} acknowledged`, 'system');
            } else {
                addLog(`❌ ${sr.cmd}: ${sr.error}`, 'error');
            }
        });
    } catch (e) {
        els.responseBubble.innerHTML = `<p style="color:var(--red)">Error: ${e.message}</p>`;
        setTranscriptState(`"${text}"`, 'Error', 'error');
        addLog(`Error: ${e.message}`, 'error');
    }

    state.processing = false;
}

function toggleMic() {
    if (!state.recognition) initVoiceRecognition();
    if (!state.recognition) return;

    if (state.listening) {
        state.listening = false;
        state.recognition.stop();
        els.micContainer.classList.remove('listening');
        els.micStatus.textContent = 'Click to start listening';
        addLog('🎤 Mic stopped', 'system');
    } else {
        state.listening = true;
        try { state.recognition.start(); } catch (e) {}
        addLog('🎤 Mic started', 'system');
    }
}

els.btnMic.addEventListener('click', toggleMic);

// ═══════════════════════════════════════════════════════════
//  CAMERA TRACKING MODE — Skin-color face detection
// ═══════════════════════════════════════════════════════════

async function startCamera() {
    if (state.cameraActive) { stopCamera(); return; }

    try {
        addLog('📷 Requesting camera...', 'system');
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
        });

        els.cameraFeed.srcObject = stream;
        state.cameraStream = stream;
        await els.cameraFeed.play();

        // Setup canvas
        const canvas = els.cameraOverlay;
        canvas.width = els.cameraFeed.videoWidth || 640;
        canvas.height = els.cameraFeed.videoHeight || 480;

        state.cameraActive = true;
        els.btnStartCamera.innerHTML = '<span>⏹️</span> Stop Camera';
        els.btnStartCamera.classList.add('active');

        addLog('📷 Camera active — tracking faces via skin detection', 'robot');

        // Start tracking loop
        runTrackingLoop(canvas);

    } catch (e) {
        addLog(`📷 Camera error: ${e.message}`, 'error');
    }
}

function stopCamera() {
    if (state.trackingLoop) { cancelAnimationFrame(state.trackingLoop); state.trackingLoop = null; }
    if (state.cameraStream) { state.cameraStream.getTracks().forEach(t => t.stop()); state.cameraStream = null; }
    state.cameraActive = false;
    els.btnStartCamera.innerHTML = '<span>📷</span> Start Camera';
    els.btnStartCamera.classList.remove('active');
    els.trackingStatus.className = 'tracking-status';
    els.trackingStatus.querySelector('span:last-child').textContent = 'Camera stopped';
    addLog('📷 Camera stopped', 'system');
}

function runTrackingLoop(canvas) {
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const video = els.cameraFeed;
    const W = canvas.width;
    const H = canvas.height;

    // Downscale for performance
    const SCALE = 4;
    const sw = Math.floor(W / SCALE);
    const sh = Math.floor(H / SCALE);
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = sw;
    tempCanvas.height = sh;
    const tempCtx = tempCanvas.getContext('2d', { willReadFrequently: true });

    function detect() {
        if (!state.cameraActive) return;

        // Draw downscaled video frame
        tempCtx.drawImage(video, 0, 0, sw, sh);
        const imgData = tempCtx.getImageData(0, 0, sw, sh);
        const pixels = imgData.data;

        // Skin-color detection (HSV-based via RGB approximation)
        let sumX = 0, sumY = 0, count = 0;
        let minX = sw, minY = sh, maxX = 0, maxY = 0;

        for (let y = 0; y < sh; y++) {
            for (let x = 0; x < sw; x++) {
                const i = (y * sw + x) * 4;
                const r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];

                // Skin detection heuristic (works for diverse skin tones)
                if (r > 60 && g > 40 && b > 20 &&
                    r > g && r > b &&
                    (r - g) > 15 &&
                    Math.abs(r - g) < 130 &&
                    r - b > 20) {
                    sumX += x;
                    sumY += y;
                    count++;
                    if (x < minX) minX = x;
                    if (y < minY) minY = y;
                    if (x > maxX) maxX = x;
                    if (y > maxY) maxY = y;
                }
            }
        }

        // Clear overlay
        ctx.clearRect(0, 0, W, H);

        // Need minimum cluster of skin pixels to count as a face
        const faceThreshold = (sw * sh) * 0.02; // At least 2% of frame

        if (count > faceThreshold) {
            const cx = (sumX / count) * SCALE;
            const cy = (sumY / count) * SCALE;
            const bx = minX * SCALE;
            const by = minY * SCALE;
            const bw = (maxX - minX) * SCALE;
            const bh = (maxY - minY) * SCALE;

            // Draw detection box
            ctx.strokeStyle = '#6366f1';
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 4]);
            ctx.strokeRect(bx, by, bw, bh);
            ctx.setLineDash([]);

            // Draw center dot
            ctx.fillStyle = '#22c55e';
            ctx.beginPath();
            ctx.arc(cx, cy, 6, 0, Math.PI * 2);
            ctx.fill();

            // Draw crosshair lines
            ctx.strokeStyle = 'rgba(34, 197, 94, 0.3)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(cx, 0); ctx.lineTo(cx, H);
            ctx.moveTo(0, cy); ctx.lineTo(W, cy);
            ctx.stroke();

            // Status
            els.trackingStatus.className = 'tracking-status active';
            els.trackingStatus.querySelector('span:last-child').textContent = 'Face tracked';

            // Map to servo angles (mirror X for natural movement)
            const panMax = parseInt(els.panRange.value);
            const tiltMax = parseInt(els.tiltRange.value);
            const normX = -((cx / W) * 2 - 1);  // Inverted for mirror
            const normY = -((cy / H) * 2 - 1);

            const targetPan = Math.round(normX * panMax);
            const targetTilt = Math.round(normY * tiltMax);

            // Smooth with tracking speed (higher = more responsive)
            const speed = Math.min(parseInt(els.trackingSpeed.value) / 8, 1.0);
            const pan = Math.round(state.lastPan + (targetPan - state.lastPan) * speed);
            const tilt = Math.round(state.lastTilt + (targetTilt - state.lastTilt) * speed);

            // Always update smoothing state (even if we don't send)
            // This prevents smoothing from fighting stale values
            state.lastPan = pan;
            state.lastTilt = tilt;

            // Update gauges
            updateGauges(pan, tilt, panMax, tiltMax);

            // Send to robot (throttled: 100ms min, 1° deadzone)
            const now = Date.now();
            if (now - state.lastTrackSend > 100 &&
                (Math.abs(pan - (state._lastSentPan || 0)) > 1 || Math.abs(tilt - (state._lastSentTilt || 0)) > 1)) {
                sendTrackingCommand(pan, tilt);
                state._lastSentPan = pan;
                state._lastSentTilt = tilt;
                state.lastTrackSend = now;
            }
        } else {
            els.trackingStatus.className = 'tracking-status';
            els.trackingStatus.querySelector('span:last-child').textContent = 'No face detected';
        }

        state.trackingLoop = requestAnimationFrame(detect);
    }

    detect();
}

function updateGauges(pan, tilt, panMax, tiltMax) {
    const panPct = (pan / panMax) * 50;
    els.gaugePan.style.width = Math.abs(panPct) + '%';
    els.gaugePan.style.left = panPct >= 0 ? '50%' : (50 + panPct) + '%';
    els.gaugePanMarker.style.left = (50 + panPct) + '%';
    els.servoPanValue.textContent = pan + '°';

    const tiltPct = (tilt / tiltMax) * 50;
    els.gaugeTilt.style.width = Math.abs(tiltPct) + '%';
    els.gaugeTilt.style.left = tiltPct >= 0 ? '50%' : (50 + tiltPct) + '%';
    els.gaugeTiltMarker.style.left = (50 + tiltPct) + '%';
    els.servoTiltValue.textContent = tilt + '°';
}

async function sendTrackingCommand(pan, tilt) {
    try {
        fetch(`${API_BASE}/direct`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cmd: `i0 ${pan} 1 ${tilt}` })
        });
        // Fire-and-forget for tracking, don't await
    } catch (e) { /* silent */ }
}

els.btnStartCamera.addEventListener('click', startCamera);

// ═══════════════════════════════════════════════════════════
//  MANUAL MODE
// ═══════════════════════════════════════════════════════════

async function sendManualCommand(cmd) {
    if (!cmd) return;
    addLog(`⌨️ ${cmd}`, 'cmd');

    try {
        // Detect natural language vs serial command
        const isSerial = /^[a-zA-Z]\d/.test(cmd) || /^k[a-z]/.test(cmd) || cmd === 'd' || cmd === 'j' || cmd === 'G' || cmd === 'p';

        if (isSerial) {
            setTranscriptState(`"${cmd}"`, 'Sent directly', 'done');
            const res = await fetch(`${API_BASE}/direct`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cmd })
            });
            const data = await res.json();
            addLog(data.success ? `✅ ${data.response?.substring(0, 80) || 'ok'}` : `❌ ${data.error}`, data.success ? 'system' : 'error');
        } else {
            setTranscriptState(`"${cmd}"`, 'Thinking...', 'pending');
            // Route through LLM
            const res = await fetch(`${API_BASE}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: cmd })
            });
            const data = await res.json();
            let html = `<p>${data.explanation}</p>`;
            if (data.commands?.length > 0) {
                html += '<div class="cmd-tags">';
                data.commands.forEach(command => { html += `<span class="cmd-tag">${command}</span>`; });
                html += '</div>';
                if (data.delay > 0 && data.commands.length > 1) {
                    html += `<p style="font-size:11px;color:var(--text-muted);margin-top:6px">⏱ ${data.delay}s delay between actions</p>`;
                }
            }
            html += renderSentiment(data.sentiment);
            html += renderTrace(data.trace);
            els.responseBubble.innerHTML = html;
            setTranscriptState(`"${cmd}"`, 'Done', 'done');
            data.commands?.forEach(c => addLog(`➤ ${c}`, 'cmd'));
            if (data.explanation) addLog(`🐾 ${data.explanation}`, 'robot');
            if (data.sentiment?.label) {
                addLog(`💬 sentiment=${data.sentiment.label} affect=${data.sentiment.affect}`, 'system');
            }
            if (data.trace?.skill_id) {
                const planLabel = Array.isArray(data.trace.steps) && data.trace.steps.length > 0
                    ? data.trace.steps.join(' -> ')
                    : data.trace.skill_id;
                addLog(`🧠 plan=${planLabel} via ${data.trace.planner_source || 'rule'}`, 'system');
            }
        }
    } catch (e) {
        setTranscriptState(`"${cmd}"`, 'Error', 'error');
        els.responseBubble.innerHTML = `<p style="color:var(--red)">Error: ${e.message}</p>`;
        addLog(`Error: ${e.message}`, 'error');
    }
}

els.btnSendManual.addEventListener('click', () => {
    const cmd = els.manualInput.value.trim();
    if (cmd) { sendManualCommand(cmd); els.manualInput.value = ''; }
});

els.manualInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
        const cmd = els.manualInput.value.trim();
        if (cmd) { sendManualCommand(cmd); els.manualInput.value = ''; }
    }
});

// Quick command buttons
document.querySelectorAll('.cmd-btn').forEach(btn => {
    btn.addEventListener('click', () => sendManualCommand(btn.dataset.cmd));
});

els.btnClearLog.addEventListener('click', () => {
    els.logEntries.innerHTML = '';
    addLog('Log cleared', 'system');
});

// ═══════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════

async function init() {
    addLog('🐾 Bittle X learning interface ready', 'system');
    await checkStatus();
    setInterval(checkStatus, 5000);
    initVoiceRecognition();
    setTranscriptState('Ready for a voice or text command.', 'Ready', 'done');
    addLog('Use the mic, type a command, or start the camera demo.', 'system');
}

window.addEventListener('DOMContentLoaded', init);
