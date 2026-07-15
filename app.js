/* ═══════════════════════════════════════════════════════════
   Petoi Bittle X — AI Controller App Logic
   Voice recognition, camera face tracking, and serial bridge
   ═══════════════════════════════════════════════════════════ */

const API_BASE = window.location.protocol.startsWith('http')
    ? `${window.location.origin}/api`
    : 'http://localhost:8080/api';

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
    sceneContextEnabled: $('scene-context-enabled'),
    statusBt: $('status-bt'),
    statusBtLabel: $('status-bt-label'),
    btnEmergency: $('btn-emergency'),
    logEntries: $('log-entries'),
    btnClearLog: $('btn-clear-log'),
    btnResetDemo: $('btn-reset-demo'),
};

// ─── Logging ──────────────────────────────────────────────
function addLog(msg, type = 'system') {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    const timeEl = document.createElement('span');
    timeEl.className = 'log-time';
    timeEl.textContent = time;
    const msgEl = document.createElement('span');
    msgEl.className = 'log-msg';
    msgEl.textContent = msg;
    entry.append(timeEl, msgEl);
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
        ? trace.steps.join(' → ')
        : trace.skill_id;
    const plannerLabel = trace.planner_source === 'llm' ? 'AI model' : 'keyword rules';
    const rationale = trace.rationale || 'The system matched the prompt to available safe skills.';

    return `
        <div class="why-box">
            <span class="why-label">🧠 Why I chose this</span>
            <p class="why-text">${escapeHtml(rationale)}</p>
        </div>
        <div class="cmd-tags" style="margin-top:10px">
            <span class="cmd-tag">Plan: ${escapeHtml(plan)}</span>
            <span class="cmd-tag">Decided by: ${plannerLabel}</span>
            <span class="cmd-tag">How sure? ${confidence}</span>
        </div>
    `;
}

const MOOD_LABELS = {
    excited: 'excited 😄',
    frustrated: 'frustrated 😖',
    uncertain: 'unsure 🤔',
    calm: 'calm 🙂',
};

function renderSentiment(sentiment) {
    if (!sentiment || (!sentiment.affect && !sentiment.label)) return '';

    const mood = sentiment.affect
        ? (MOOD_LABELS[sentiment.affect] || escapeHtml(sentiment.affect))
        : escapeHtml(sentiment.label);
    const supportCue = sentiment.label
        ? ` — Support cue: ${escapeHtml(sentiment.label)}${sentiment.score !== undefined ? ` (${escapeHtml(sentiment.score)})` : ''}`
        : '';

    return `
        <p class="mood-line">
            Mood detected: ${mood}${supportCue}
        </p>
    `;
}

function renderCommandTags(commands = []) {
    if (!commands.length) return '';
    const tags = commands
        .map(command => `<span class="cmd-tag">${escapeHtml(command)}</span>`)
        .join('');
    return `<div class="cmd-tags">${tags}</div>`;
}

function renderReasoning(reasoning) {
    if (!reasoning) return '';

    const candidates = Array.isArray(reasoning.candidates) ? reasoning.candidates : [];
    const fitOrder = { strong: 'Strong', medium: 'Medium', weak: 'Weak' };
    const candidateMarkup = candidates.map(candidate => `
        <div class="reasoning-candidate ${candidate.selected ? 'selected' : ''}">
            <div>
                <div class="reasoning-candidate-top">
                    <strong>${escapeHtml(candidate.label || candidate.skill_id || 'skill')}</strong>
                    <span class="fit-pill fit-${escapeHtml(candidate.fit || 'weak')}">${escapeHtml(fitOrder[candidate.fit] || candidate.fit || 'Weak')} fit</span>
                </div>
                ${Array.isArray(candidate.matched_cues) && candidate.matched_cues.length
                    ? `<p class="reasoning-cues">Cues: ${candidate.matched_cues.map(escapeHtml).join(', ')}</p>`
                    : '<p class="reasoning-cues">Cues: none matched directly</p>'}
                <p>${escapeHtml(candidate.reason || '')}</p>
                <p class="reasoning-command">${escapeHtml(candidate.command_text || (candidate.commands || []).join(' → ') || 'No command')}</p>
            </div>
            <span>${candidate.selected ? 'Chosen' : 'Considered'}</span>
        </div>
    `).join('');

    const commands = Array.isArray(reasoning.commands) && reasoning.commands.length > 0
        ? reasoning.commands.join(' → ')
        : 'No robot command sent';

    const sceneMarkup = renderSceneContext(reasoning.scene);

    return `
        <div class="reasoning-card">
            ${sceneMarkup}
            <div class="reasoning-row">
                <span>What I understood</span>
                <p>${escapeHtml(reasoning.intent || 'The prompt needs a clearer supported action.')}</p>
            </div>
            <div class="reasoning-row">
                <span>Actions considered</span>
                <div class="reasoning-candidates">${candidateMarkup}</div>
            </div>
            <div class="reasoning-row">
                <span>Safe command check</span>
                <p>${escapeHtml(reasoning.validation?.message || 'Commands were checked before execution.')}</p>
                <p class="reasoning-command">${escapeHtml(commands)}</p>
            </div>
            <div class="reasoning-row">
                <span>Try next</span>
                <p>${escapeHtml(reasoning.revision_tip || 'Revise the prompt if the robot did not do what you expected.')}</p>
            </div>
        </div>
    `;
}

function renderSceneContext(scene) {
    if (!scene) return '';

    const observations = Array.isArray(scene.observations) ? scene.observations : [];
    const uncertainties = Array.isArray(scene.uncertainties) ? scene.uncertainties : [];
    const observationMarkup = observations.length
        ? `<ul class="scene-list">${observations.map(item => `
            <li>
                <strong>${escapeHtml(item.label || 'Visible item')}</strong>
                <span>${escapeHtml(item.location || 'location uncertain')} · ${escapeHtml(item.confidence || 'confidence unknown')}</span>
                ${item.evidence ? `<small>${escapeHtml(item.evidence)}</small>` : ''}
            </li>
        `).join('')}</ul>`
        : '<p>No reliable objects were reported from the frame.</p>';
    const uncertaintyMarkup = uncertainties.length
        ? `<ul class="scene-uncertainties">${uncertainties.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
        : '<p>No additional uncertainty was reported.</p>';
    const robot = scene.robot || {};
    const grounding = scene.grounding || {};

    return `
        <div class="scene-reasoning-banner">
            <span>Camera-grounded preview</span>
            <strong>No robot movement was executed</strong>
        </div>
        <div class="reasoning-row">
            <span>What I observed</span>
            ${observationMarkup}
        </div>
        <div class="reasoning-row">
            <span>Robot location</span>
            <p>${escapeHtml(robot.summary || 'The robot location or heading could not be confirmed.')}</p>
        </div>
        <div class="reasoning-row">
            <span>How the command is grounded</span>
            <p>${escapeHtml(grounding.summary || 'The command could not yet be tied to a visible target.')}</p>
        </div>
        <div class="reasoning-row">
            <span>Visual uncertainty</span>
            ${uncertaintyMarkup}
        </div>
    `;
}

function captureCameraFrame() {
    if (!state.cameraActive || !els.cameraFeed.videoWidth || !els.cameraFeed.videoHeight) {
        throw new Error('Start the camera before using scene-grounded reasoning.');
    }

    const maxWidth = 960;
    const scale = Math.min(1, maxWidth / els.cameraFeed.videoWidth);
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(els.cameraFeed.videoWidth * scale);
    canvas.height = Math.round(els.cameraFeed.videoHeight * scale);
    canvas.getContext('2d').drawImage(els.cameraFeed, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.78);
}

async function requestNaturalLanguageCommand(text) {
    const useScene = Boolean(els.sceneContextEnabled?.checked);
    const payload = { text };
    let endpoint = 'command';

    if (useScene) {
        payload.image = captureCameraFrame();
        endpoint = 'scene-reasoning';
        addLog('📷 Captured current frame for a non-executing reasoning preview', 'system');
    }

    const res = await fetch(`${API_BASE}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
}

function renderCommandResponse(data) {
    let html = `<p>${escapeHtml(data.explanation || 'No explanation returned.')}</p>`;
    html += renderCommandTags(data.commands || []);
    if (Array.isArray(data.command_delays) && data.command_delays.some(delay => delay > 0) && data.commands?.length > 1) {
        html += '<p style="font-size:11px;color:var(--text-muted);margin-top:6px">⏱ Timed sequence with short pauses between actions</p>';
    } else if (data.delay > 0 && data.commands?.length > 1) {
        html += `<p style="font-size:11px;color:var(--text-muted);margin-top:6px">⏱ ${escapeHtml(data.delay)}s delay between actions</p>`;
    }
    html += renderSentiment(data.sentiment);
    html += renderTrace(data.trace);
    html += renderReasoning(data.reasoning);
    els.responseBubble.innerHTML = html;
}

function logCommandResponse(data) {
    data.commands?.forEach(cmd => addLog(`➤ ${cmd}`, 'cmd'));
    if (data.explanation) addLog(`🐾 ${data.explanation}`, 'robot');
    if (data.sentiment?.label) {
        addLog(`💬 sentiment=${data.sentiment.label} affect=${data.sentiment.affect} score=${data.sentiment.score}`, 'system');
    }
    if (data.reasoning?.intent) {
        addLog(`🧭 understood=${data.reasoning.intent}`, 'system');
    }
    if (data.trace?.skill_id || data.trace?.status) {
        const planLabel = Array.isArray(data.trace.steps) && data.trace.steps.length > 0
            ? data.trace.steps.join(' -> ')
            : (data.trace.status || 'needs revision');
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
        const data = await requestNaturalLanguageCommand(text);

        els.robotEmoji.textContent = EMOJIS[data.emotion] || '🐕';
        renderCommandResponse(data);
        setTranscriptState(`"${text}"`, 'Done', 'done');
        logCommandResponse(data);
    } catch (e) {
        els.responseBubble.innerHTML = `<p style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`;
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

        addLog(
            els.sceneContextEnabled?.checked
                ? '📷 Camera active — scene frame ready; movement paused'
                : '📷 Camera active — tracking faces via skin detection',
            'robot'
        );

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

        if (els.sceneContextEnabled?.checked) {
            ctx.clearRect(0, 0, W, H);
            els.trackingStatus.className = 'tracking-status active';
            els.trackingStatus.querySelector('span:last-child').textContent = 'Scene frame ready';
            state.trackingLoop = requestAnimationFrame(detect);
            return;
        }

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
            // Clamp to the server's joint limits (pan ±70, tilt −30..80) so
            // tracking commands are never rejected by the validator.
            const pan = Math.max(-70, Math.min(70,
                Math.round(state.lastPan + (targetPan - state.lastPan) * speed)));
            const tilt = Math.max(-30, Math.min(80,
                Math.round(state.lastTilt + (targetTilt - state.lastTilt) * speed)));

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
els.sceneContextEnabled?.addEventListener('change', () => {
    if (!state.cameraActive) return;
    const label = els.trackingStatus.querySelector('span:last-child');
    if (els.sceneContextEnabled.checked) {
        els.trackingStatus.className = 'tracking-status active';
        label.textContent = 'Scene frame ready';
        addLog('📷 Scene preview enabled — face-tracking movement paused', 'system');
    } else {
        els.trackingStatus.className = 'tracking-status';
        label.textContent = 'Looking for a face';
        addLog('📷 Face-tracking camera demo enabled', 'system');
    }
});

// ═══════════════════════════════════════════════════════════
//  MANUAL MODE
// ═══════════════════════════════════════════════════════════

async function sendManualCommand(cmd) {
    if (!cmd) return;
    if (state.processing) {
        addLog('Command already running. Wait for it to finish before sending another.', 'system');
        return;
    }

    state.processing = true;
    addLog(`⌨️ ${cmd}`, 'cmd');

    try {
        // Detect natural language vs serial command
        const isSerial = /^[a-zA-Z]\d/.test(cmd) || /^k[a-z]/.test(cmd) || cmd === 'd' || cmd === 'j' || cmd === 'G' || cmd === 'p';

        if (isSerial) {
            if (els.sceneContextEnabled?.checked) {
                throw new Error('Direct robot commands are disabled during scene-reasoning preview.');
            }
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
            const data = await requestNaturalLanguageCommand(cmd);
            els.robotEmoji.textContent = EMOJIS[data.emotion] || '🐕';
            renderCommandResponse(data);
            setTranscriptState(`"${cmd}"`, 'Done', 'done');
            logCommandResponse(data);
        }
    } catch (e) {
        setTranscriptState(`"${cmd}"`, 'Error', 'error');
        els.responseBubble.innerHTML = `<p style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`;
        addLog(`Error: ${e.message}`, 'error');
    } finally {
        state.processing = false;
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

document.querySelectorAll('.demo-prompt-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const prompt = btn.dataset.demoPrompt;
        els.manualInput.value = prompt;
        sendManualCommand(prompt);
    });
});

function resetDemoScreen() {
    els.manualInput.value = '';
    els.robotEmoji.textContent = '🐕';
    setTranscriptState('Ready for a voice or text command.', 'Ready', 'done');
    els.responseBubble.innerHTML = '<p>Ready. The next command will appear here with the selected plan and robot response.</p>';
    els.logEntries.innerHTML = '';
    addLog('Demo screen reset', 'system');
}

els.btnResetDemo.addEventListener('click', resetDemoScreen);

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
