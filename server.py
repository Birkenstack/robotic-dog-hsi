"""
Petoi Bittle X — LLM Voice & Camera Bridge Server
Receives commands from the browser UI, routes through OpenRouter LLM,
and sends serial commands to the robot over Bluetooth.

SAFEGUARDS:
  - atexit + signal handlers guarantee port cleanup on ANY exit
  - Lock timeout prevents deadlocks (never hangs forever)
  - Auto-reconnect on dead/stuck port
  - Watchdog detects stale connections
  - All serial ops wrapped in try/except with recovery
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timezone
import atexit
import signal
import threading
import serial
import serial.tools.list_ports
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ─── Config ────────────────────────────────────────────────
API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
COM_PORT = os.getenv("COM_PORT", "COM5")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))
LOCK_TIMEOUT = 5        # seconds — never wait longer than this for the lock
SERIAL_TIMEOUT = 2      # seconds — read timeout on the serial port
ACK_TIMEOUT = 3         # seconds — max wait for robot acknowledgment
CMD_SPACING = 0.3       # seconds — minimum gap between serial writes
MAX_PLAN_COMMANDS = 8
MAX_PLAN_STEPS = 3
INTERACTION_LOG_PATH = os.getenv("INTERACTION_LOG_PATH", "interaction_logs.jsonl")
# Bind to localhost by default so a shared classroom Wi-Fi network can't reach
# the robot-control endpoints. Override with FLASK_HOST=0.0.0.0 only if you
# deliberately need to drive the robot from another device.
HOST = os.getenv("FLASK_HOST", "127.0.0.1")
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    # "null" keeps the local file:// demo workflow working while blocking
    # ordinary external websites from calling the robot API through CORS.
    "http://localhost:8080,http://127.0.0.1:8080,null"
).split(",")

JOINT_LIMITS = {
    0: (-70, 70),
    1: (-30, 80),
    8: (-50, 50),
    9: (-50, 50),
    10: (-50, 50),
    11: (-50, 50),
    12: (-70, 70),
    13: (-70, 70),
    14: (-70, 70),
    15: (-70, 70),
}

SKILL_LIBRARY = {
    "stand": {
        "commands": ["kbalance"],
        "delay": 0,
        "emotion": "alert",
        "explanation": "I'll stand up and get ready.",
        "keywords": ["stand", "balance", "ready", "wake up"],
    },
    "sit": {
        "commands": ["ksit"],
        "delay": 0,
        "emotion": "curious",
        "explanation": "I'll sit down.",
        "keywords": ["sit", "sit down", "have a seat"],
    },
    "rest": {
        "commands": ["krest"],
        "delay": 0,
        "emotion": "sleepy",
        "explanation": "I'll rest now.",
        "keywords": ["rest", "relax", "sleep", "lie down", "settle"],
    },
    "stretch": {
        "commands": ["kstr"],
        "delay": 1,
        "emotion": "playful",
        "explanation": "I'll stretch out first.",
        "keywords": ["stretch", "warm up"],
    },
    "wave": {
        "commands": ["khi"],
        "delay": 2,
        "emotion": "happy",
        "explanation": "I'll wave hello.",
        "keywords": ["wave", "hello", "hi", "greet", "say hi"],
    },
    "meme_67": {
        # Built from movements this Bittle X firmware has acknowledged
        # reliably. It approximates a playful "6-7" up/down gesture without
        # depending on unsupported custom firmware skills.
        "commands": ["kbalance", "khi", "ksit", "kbalance", "kstr"],
        "delay": 1,
        "command_delays": [0.6, 1.6, 0.8, 0.8, 0],
        "emotion": "playful",
        "explanation": "I'll do a playful 6-7 routine.",
        "keywords": [
            "67", "6-7", "six seven", "doot doot", "67 meme",
            "six-seven", "meme", "trend", "trendy"
        ],
    },
    "celebrate": {
        # This firmware does not expose a "jy" skill. Compose the classroom
        # celebration from movements the connected Bittle X acknowledges.
        "commands": ["khi", "kstr"],
        "delay": 2,
        "emotion": "happy",
        "explanation": "I'll celebrate with a happy move.",
        "keywords": [
            "celebrate", "joy", "happy dance", "yay", "good job", "aced",
            "passed", "proud", "won", "test"
        ],
    },
    "check_around": {
        "commands": ["kck"],
        "delay": 1,
        "emotion": "curious",
        "explanation": "I'll look around.",
        "keywords": ["look around", "check", "search", "scan"],
    },
    "walk_forward": {
        "commands": ["kwkF"],
        "delay": 1,
        "emotion": "alert",
        "explanation": "I'll walk forward.",
        "keywords": ["walk forward", "come here", "move forward", "forward"],
    },
    "walk_backward": {
        "commands": ["kbk"],
        "delay": 1,
        "emotion": "alert",
        "explanation": "I'll back up.",
        "keywords": ["back", "backward", "go back", "move back", "go away"],
    },
    "walk_left": {
        "commands": ["kwkL"],
        "delay": 1,
        "emotion": "alert",
        "explanation": "I'll step left.",
        "keywords": ["left", "move left", "walk left"],
    },
    "walk_right": {
        "commands": ["kwkR"],
        "delay": 1,
        "emotion": "alert",
        "explanation": "I'll step right.",
        "keywords": ["right", "move right", "walk right"],
    },
    "crawl_forward": {
        "commands": ["kcrF"],
        "delay": 1,
        "emotion": "curious",
        "explanation": "I'll crawl forward slowly.",
        "keywords": ["crawl", "slowly forward", "move slowly"],
    },
    "trot_forward": {
        "commands": ["ktrF"],
        "delay": 1,
        "emotion": "playful",
        "explanation": "I'll trot forward quickly.",
        "keywords": ["trot", "run", "fast forward", "move fast"],
    },
    "push_up": {
        "commands": ["kpu1"],
        "delay": 3,
        "emotion": "playful",
        "explanation": "I'll do a push-up.",
        "keywords": ["push up", "push-up", "exercise"],
    },
    "play_dead": {
        "commands": ["kpd"],
        "delay": 2,
        "emotion": "sleepy",
        "explanation": "I'll play dead.",
        "keywords": ["play dead", "dead", "dramatic"],
    },
    "rotate": {
        "commands": ["krt"],
        "delay": 2,
        "emotion": "playful",
        "explanation": "I'll spin around.",
        "keywords": ["turn around", "rotate", "spin"],
    },
    "stop": {
        "commands": ["d"],
        "delay": 0,
        "emotion": "alert",
        "explanation": "I'll stop immediately.",
        "keywords": ["stop", "freeze", "halt", "emergency stop"],
    },
    "status": {
        "commands": ["j"],
        "delay": 0,
        "emotion": "curious",
        "explanation": "I'll report my joint status.",
        "keywords": ["status", "joint status", "what are your joints", "angles"],
    },
}

ALLOWED_SKILL_IDS = set(SKILL_LIBRARY.keys())
ALLOWED_DIRECT_COMMANDS = {
    "kbalance", "ksit", "krest", "kstr", "khi", "kck", "kwkF", "kbk",
    "kwkL", "kwkR", "kcrF", "ktrF", "kpu1", "kpd", "krt", "d", "j", "p", "G"
}
interaction_log_lock = threading.Lock()

POSITIVE_SENTIMENT_TERMS = {
    "good": 1.0, "great": 1.4, "awesome": 1.6, "cool": 1.0, "fun": 1.2,
    "nice": 0.9, "love": 1.5, "yay": 1.4, "happy": 1.3, "excited": 1.4,
    "thanks": 0.8, "thank you": 1.0, "amazing": 1.6, "aced": 1.6,
    "passed": 1.2, "proud": 1.3,
}

NEGATIVE_SENTIMENT_TERMS = {
    "bad": -1.0, "wrong": -0.8, "broken": -1.5, "confusing": -1.3,
    "frustrated": -1.6, "annoying": -1.4, "stupid": -1.6, "hate": -1.8,
    "not working": -1.8, "doesn't work": -1.8, "doesnt work": -1.8,
    "error": -1.2, "stuck": -1.2, "why": -0.4, "no": -0.3,
}

UNCERTAINTY_TERMS = {
    "maybe", "i think", "i guess", "not sure", "unsure", "confused", "how do i",
    "what do i", "can i", "should i",
}

# ─── Serial Manager (bulletproof) ──────────────────────────
class SerialManager:
    """Thread-safe serial port manager with auto-reconnect and cleanup."""

    def __init__(self, port, baud, lock_timeout=5):
        self.port = port
        self.baud = baud
        self.lock_timeout = lock_timeout
        self._ser = None
        self._lock = threading.Lock()
        self._last_success = 0  # timestamp of last successful send
        self._connected = False

        # Register cleanup on ANY exit path
        atexit.register(self.cleanup)

    @property
    def connected(self):
        return self._connected and self._ser is not None and self._ser.is_open

    def connect(self, retries=3):
        """Open the serial port with retries. Safe to call multiple times."""
        for attempt in range(1, retries + 1):
            # Always close first to avoid orphan handles
            self._close_port()

            try:
                print(f"[SERIAL] Connecting to {self.port}... (attempt {attempt}/{retries})")
                self._ser = serial.Serial(
                    self.port,
                    self.baud,
                    timeout=SERIAL_TIMEOUT,
                    write_timeout=SERIAL_TIMEOUT
                )
                time.sleep(2)  # BT needs time to stabilize

                # Flush startup garbage
                if self._ser.in_waiting:
                    self._ser.read(self._ser.in_waiting)

                self._connected = True
                self._last_success = time.time()
                print(f"[SERIAL] ✅ Connected to {self.port} at {self.baud} baud")
                return True

            except Exception as e:
                print(f"[SERIAL] Attempt {attempt} failed: {e}")
                self._close_port()
                if attempt < retries:
                    wait = min(attempt * 2, 6)
                    print(f"[SERIAL] Retrying in {wait}s...")
                    time.sleep(wait)

        print(f"[SERIAL] ❌ All {retries} attempts failed")
        self._connected = False
        return False

    def send(self, cmd, wait_for_ack=True):
        """Send a command with lock timeout — NEVER hangs forever."""
        if not self.connected:
            # Try auto-reconnect
            print("[SERIAL] Port not connected — attempting reconnect...")
            if not self.connect(retries=2):
                return {"success": False, "error": "Serial not connected (reconnect failed)"}

        # Acquire lock with timeout — prevents deadlocks
        acquired = self._lock.acquire(timeout=self.lock_timeout)
        if not acquired:
            print(f"[SERIAL] ⚠️ Lock timeout ({self.lock_timeout}s) — port may be stuck")
            # Force recovery
            self._force_recovery()
            return {"success": False, "error": f"Port lock timeout after {self.lock_timeout}s — recovering"}

        try:
            return self._send_locked(cmd, wait_for_ack)
        except serial.SerialException as e:
            print(f"[SERIAL] Port error: {e} — will reconnect on next command")
            self._connected = False
            return {"success": False, "error": f"Serial error: {e}"}
        except Exception as e:
            print(f"[SERIAL] Unexpected error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            try:
                self._lock.release()
            except RuntimeError:
                pass  # Lock wasn't held (shouldn't happen but safety first)

    def _send_locked(self, cmd, wait_for_ack):
        """Internal send — must be called with lock held."""
        ser = self._ser
        if not ser or not ser.is_open:
            self._connected = False
            return {"success": False, "error": "Port closed unexpectedly"}

        # Flush stale input
        try:
            if ser.in_waiting:
                ser.read(ser.in_waiting)
        except OSError:
            self._connected = False
            return {"success": False, "error": "Port read failed — disconnected"}

        # Send command
        cmd_clean = cmd.strip()
        ser.write((cmd_clean + '\n').encode('utf-8'))
        print(f"[TX] >>> {cmd_clean}")

        if not wait_for_ack:
            time.sleep(0.05)
            self._last_success = time.time()
            return {"success": True, "response": "sent (no-wait)"}

        # Wait for acknowledgment with hard timeout
        time.sleep(CMD_SPACING)
        response_chunks = []
        deadline = time.time() + ACK_TIMEOUT

        while time.time() < deadline:
            try:
                waiting = ser.in_waiting
            except OSError:
                self._connected = False
                return {"success": False, "error": "Port disappeared during read"}

            if waiting > 0:
                chunk = ser.read(waiting).decode('utf-8', errors='replace')
                response_chunks.append(chunk)
                time.sleep(0.15)
                # Grab any remaining bytes
                if ser.in_waiting > 0:
                    response_chunks.append(
                        ser.read(ser.in_waiting).decode('utf-8', errors='replace')
                    )
                break
            time.sleep(0.1)

        response = ''.join(response_chunks).strip()
        print(f"[RX] <<< {response[:120] if response else '(no response)'}")

        self._last_success = time.time()
        return {"success": True, "response": response if response else "acknowledged"}

    def _force_recovery(self):
        """Nuclear option — close and reopen the port to break a deadlock."""
        print("[SERIAL] 🔄 Force recovery — closing and reopening port...")
        self._close_port()
        # Release the lock if somehow stuck
        try:
            self._lock.release()
        except RuntimeError:
            pass
        # Reinitialize the lock
        self._lock = threading.Lock()
        time.sleep(2)
        self.connect(retries=2)

    def _close_port(self):
        """Safely close the serial port."""
        if self._ser:
            try:
                if self._ser.is_open:
                    self._ser.close()
                    print(f"[SERIAL] Port {self.port} closed")
            except Exception as e:
                print(f"[SERIAL] Error closing port: {e}")
            finally:
                self._ser = None
                self._connected = False

    def cleanup(self):
        """Called on exit — guarantees port release."""
        print(f"\n[CLEANUP] Releasing {self.port}...")
        self._close_port()
        print("[CLEANUP] ✅ Port released cleanly")

    def health_check(self):
        """Quick check — is the port alive?"""
        if not self.connected:
            return {"healthy": False, "reason": "not connected"}
        try:
            # Just check if port is accessible
            _ = self._ser.in_waiting
            return {"healthy": True, "last_success": self._last_success}
        except Exception as e:
            self._connected = False
            return {"healthy": False, "reason": str(e)}

    def scan_ports(self):
        """List all available COM ports."""
        ports = serial.tools.list_ports.comports()
        return [{"port": p.device, "desc": p.description} for p in ports]


# ─── Global serial manager ─────────────────────────────────
serial_mgr = SerialManager(COM_PORT, BAUD_RATE, LOCK_TIMEOUT)


# ─── Signal Handlers (Ctrl+C, SIGTERM, etc.) ───────────────
def graceful_shutdown(signum, frame):
    """Handle Ctrl+C / kill signals cleanly."""
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else signum
    print(f"\n[SIGNAL] Caught {sig_name} — shutting down gracefully...")
    serial_mgr.cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)
# Windows also has SIGBREAK (Ctrl+Break)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, graceful_shutdown)


# ─── LLM System Prompt ────────────────────────────────────
SKILL_SUMMARY = "\n".join(
    f"- {skill_id}: {spec['explanation']} Commands={','.join(spec['commands'])}"
    for skill_id, spec in SKILL_LIBRARY.items()
)

SYSTEM_PROMPT = f"""You interpret natural-language requests for a Petoi Bittle X robot dog.

Your job is to choose up to {MAX_PLAN_STEPS} high-level skill identifiers from the allowed list.

Allowed skills:
{SKILL_SUMMARY}

Rules:
1. Return between 1 and {MAX_PLAN_STEPS} skill_ids from the allowed list.
2. Prefer safe, classroom-friendly actions.
3. If the request sounds multi-step, break it into a short ordered sequence.
4. If the request is ambiguous, choose the closest safe plan.
5. Never invent raw serial commands.
6. Write the rationale as one or two plain sentences a middle-school student
   can read. If the exact request is not a supported skill, name what was
   asked and say you picked the closest safe alternative. Lower the
   confidence when you had to substitute or guess.

Return strict JSON only:
{{"steps":["walk_forward","wave"],"confidence":0.91,"rationale":"The student asked the dog to move and then greet."}}
"""


def keyword_match_skill(user_text):
    """Deterministic fallback planner based on keyword overlap."""
    normalized = user_text.lower().strip()
    best_skill = None
    best_score = 0

    for skill_id, spec in SKILL_LIBRARY.items():
        score = 0
        for keyword in spec["keywords"]:
            if keyword in normalized:
                score += len(keyword.split())
        if score > best_score:
            best_skill = skill_id
            best_score = score

    if best_score == 0:
        if "slow" in normalized or "careful" in normalized:
            best_skill = "crawl_forward"
        elif "fast" in normalized or "run" in normalized:
            best_skill = "trot_forward"
        elif "left" in normalized:
            best_skill = "walk_left"
        elif "right" in normalized:
            best_skill = "walk_right"
        elif "back" in normalized or "away" in normalized:
            best_skill = "walk_backward"
        elif "walk" in normalized or "forward" in normalized or "come" in normalized:
            best_skill = "walk_forward"

    return {
        "skill_id": best_skill,
        "confidence": 0.45 if best_score == 0 else min(0.65 + (best_score * 0.08), 0.98),
        "rationale": "Matched the request against a constrained local keyword library." if best_skill else "No supported classroom skill clearly matched the prompt.",
        "source": "rule",
    }


def detect_negated_request(user_text):
    """Detect simple requests that ask the robot not to do an action."""
    normalized = (user_text or "").lower().strip()
    has_negation = re.search(r"\b(?:don't|dont|do not|never)\b", normalized)
    if not has_negation:
        return False

    action_cues = {
        keyword
        for spec in SKILL_LIBRARY.values()
        for keyword in spec["keywords"]
    } | {"move", "go", "walk", "run", "turn", "spin", "dance"}
    return any(_phrase_present(cue, normalized) for cue in action_cues)


def has_robot_action_cue(user_text):
    """Return True when text appears to ask for an available robot behavior."""
    normalized = (user_text or "").lower().strip()
    action_cues = {
        keyword
        for spec in SKILL_LIBRARY.values()
        for keyword in spec["keywords"]
    } | {"move", "go", "walk", "run", "turn", "spin", "dance", "act"}
    return any(_phrase_present(cue, normalized) for cue in action_cues)


def looks_like_non_action_question(user_text):
    normalized = (user_text or "").lower().strip()
    return (
        re.match(r"^(?:what|who|where|when|why|how)\b", normalized) is not None
        and not has_robot_action_cue(normalized)
    )


def no_action_interpretation(user_text, reason, confidence=0.9, source="rule"):
    """Return a non-executing interpretation that still teaches the pipeline."""
    return {
        "steps": [],
        "confidence": confidence,
        "rationale": reason,
        "source": source,
        "status": "needs_revision",
        "blocked_reason": reason,
    }


def infer_steps_from_text(user_text):
    """Broader local chain guessing for up to three steps."""
    normalized = (user_text or "").lower().strip()
    if detect_negated_request(normalized):
        return no_action_interpretation(
            user_text,
            "The prompt appears to ask the robot not to perform an action, so no movement was sent.",
        )

    split_parts = [
        p.strip(" ,.!?")
        for p in re.split(r"\b(?:and then|then|and|after that|afterwards|after|next)\b|[,;]+", normalized)
        if p and p.strip(" ,.!?")
    ]

    candidates = split_parts[:MAX_PLAN_STEPS] if len(split_parts) > 1 else [normalized]
    steps = []
    rationales = []

    for part in candidates:
        matched = keyword_match_skill(part)
        skill_id = matched["skill_id"]
        if skill_id and skill_id not in steps:
            steps.append(skill_id)
            rationales.append(f"'{part}' -> {skill_id}")
        if len(steps) >= MAX_PLAN_STEPS:
            break

    if len(steps) == 1:
        # Broader guessing: accumulate distinct action cues from the whole sentence.
        for skill_id, spec in SKILL_LIBRARY.items():
            if skill_id in steps:
                continue
            if any(keyword in normalized for keyword in spec["keywords"]):
                steps.append(skill_id)
                rationales.append(f"whole prompt -> {skill_id}")
            if len(steps) >= MAX_PLAN_STEPS:
                break

    if not steps:
        return no_action_interpretation(
            user_text,
            "The prompt did not clearly match one of the supported classroom skills.",
            confidence=0.35,
        )

    return {
        "steps": steps[:MAX_PLAN_STEPS],
        "confidence": 0.6 if len(steps) > 1 else 0.55,
        "rationale": "Built a short plan from the prompt using constrained skill matching: " + "; ".join(rationales[:MAX_PLAN_STEPS]),
        "source": "rule",
    }


def call_llm(user_text):
    """Interpret user's request as a short plan of skill identifiers."""
    normalized = (user_text or "").lower().strip().strip(" .!?")
    if detect_negated_request(normalized):
        return no_action_interpretation(
            user_text,
            "The prompt appears to ask the robot not to perform an action, so no movement was sent.",
        )
    if looks_like_non_action_question(normalized):
        return no_action_interpretation(
            user_text,
            "The prompt sounds like a question rather than a request for a supported robot action.",
            confidence=0.8,
        )

    for skill_id, spec in SKILL_LIBRARY.items():
        if normalized == skill_id.replace("_", " ") or normalized in spec["keywords"]:
            return {
                "steps": [skill_id],
                "confidence": 1.0,
                "rationale": f"The prompt directly names the supported {skill_id.replace('_', ' ')} skill.",
                "source": "rule",
            }

    if not API_KEY:
        return infer_steps_from_text(user_text)

    try:
        print(f"[LLM] Calling {MODEL}...")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:8080",
                "X-Title": "Petoi Bittle X Voice Controller"
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text}
                ],
                "temperature": 0.3,
                "max_tokens": 300
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        print(f"[LLM] Raw response: {content[:200]}")

        # Clean up JSON from markdown wrappers
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)
        content = content.strip()

        result = json.loads(content)
        steps = result.get("steps")
        if isinstance(steps, str):
            steps = [steps]
        if not isinstance(steps, list):
            skill_id = str(result.get("skill_id", "")).strip()
            steps = [skill_id] if skill_id else []

        cleaned_steps = []
        for step in steps:
            step_id = str(step).strip()
            if step_id in ALLOWED_SKILL_IDS and step_id not in cleaned_steps:
                cleaned_steps.append(step_id)
            if len(cleaned_steps) >= MAX_PLAN_STEPS:
                break

        if not cleaned_steps:
            fallback = infer_steps_from_text(user_text)
            fallback["rationale"] = (
                "Model returned unsupported or empty steps; "
                "used constrained fallback planner."
            )
            return fallback

        interpreted = {
            "steps": cleaned_steps,
            "confidence": max(0.0, min(float(result.get("confidence", 0.75)), 1.0)),
            "rationale": result.get("rationale", "Mapped request to the closest supported classroom plan."),
            "source": "llm",
        }
        print(f"[LLM] Parsed: steps={interpreted['steps']} confidence={interpreted['confidence']}")
        return interpreted

    except json.JSONDecodeError as e:
        print(f"[LLM] JSON parse error: {e}")
        print(f"[LLM] Content was: {content}")
        fallback = infer_steps_from_text(user_text)
        fallback["rationale"] = "Model returned invalid JSON; used constrained fallback planner."
        return fallback
    except Exception as e:
        print(f"[LLM] Error: {e}")
        fallback = infer_steps_from_text(user_text)
        fallback["rationale"] = "The language model was unavailable, so the constrained local planner was used."
        return fallback


def plan_actions(user_text, interpretation):
    """Convert a high-level skill sequence into a concrete command plan."""
    if interpretation.get("status") == "needs_revision":
        reason = interpretation.get("blocked_reason") or interpretation.get("rationale") or "The prompt needs a clearer supported action."
        return {
            "skill_id": None,
            "steps": [],
            "commands": [],
            "command_delays": [],
            "delay": 0,
            "emotion": "confused",
            "explanation": "I did not send a robot movement. Try a clearer supported action like 'wave', 'sit', or 'walk forward'.",
            "trace": {
                "prompt": user_text,
                "planner_source": interpretation.get("source", "rule"),
                "skill_id": None,
                "steps": [],
                "confidence": round(float(interpretation.get("confidence", 0.0)), 2),
                "rationale": reason,
                "step_count": 0,
                "command_count": 0,
                "status": "needs_revision",
            },
            "step_summaries": [],
        }

    step_ids = interpretation.get("steps")
    if not isinstance(step_ids, list) or not step_ids:
        fallback = infer_steps_from_text(user_text)
        step_ids = fallback["steps"]
        interpretation = fallback

    step_ids = [step for step in step_ids if step in SKILL_LIBRARY][:MAX_PLAN_STEPS]
    if not step_ids:
        step_ids = ["check_around"]

    commands = []
    step_summaries = []
    emotion = "curious"
    explanation_parts = []
    delay_values = []
    command_delays = []
    included_steps = []

    for step_id in step_ids:
        skill = SKILL_LIBRARY[step_id]
        skill_commands = list(skill["commands"])
        skill_delays = list(skill.get("command_delays", []))
        # Only add a skill if its whole command sequence fits in the budget.
        # Never split a skill across the cap -- that would make the spoken
        # explanation promise an action that never actually gets sent.
        if commands and len(commands) + len(skill_commands) > MAX_PLAN_COMMANDS:
            break
        commands.extend(skill_commands[:MAX_PLAN_COMMANDS])
        for cmd_idx, _ in enumerate(skill_commands[:MAX_PLAN_COMMANDS]):
            if cmd_idx < len(skill_delays):
                command_delays.append(float(skill_delays[cmd_idx]))
            else:
                command_delays.append(float(skill.get("delay", 0)))
        included_steps.append(step_id)
        step_summaries.append({
            "skill_id": step_id,
            "commands": skill_commands,
            "explanation": skill.get("explanation", ""),
        })
        skill_explanation = skill.get("explanation", step_id.replace("_", " "))
        explanation_part = re.sub(r"^I'll\s+", "", skill_explanation, flags=re.IGNORECASE).rstrip(".")
        explanation_parts.append(explanation_part)
        delay_values.append(int(skill.get("delay", 0)))
        emotion = skill.get("emotion", emotion)

    # Keep the returned plan, trace, and explanation consistent with what will
    # actually run on the robot.
    step_ids = included_steps or step_ids[:1]
    delay = max([0] + delay_values)
    explanation = "I'll " + ", then ".join(explanation_parts) + "."

    return {
        "skill_id": step_ids[0],
        "steps": step_ids,
        "commands": commands,
        "command_delays": command_delays,
        "delay": delay,
        "emotion": emotion,
        "explanation": explanation,
        "trace": {
            "prompt": user_text,
            "planner_source": interpretation.get("source", "rule"),
            "skill_id": step_ids[0],
            "steps": step_ids,
            "confidence": round(float(interpretation.get("confidence", 0.0)), 2),
            "rationale": interpretation.get("rationale", ""),
            "step_count": len(step_ids),
            "command_count": len(commands),
        },
        "step_summaries": step_summaries,
    }


def matched_keywords_for_skill(skill_id, user_text):
    normalized = (user_text or "").lower()
    return [
        keyword
        for keyword in SKILL_LIBRARY[skill_id]["keywords"]
        if _phrase_present(keyword, normalized)
    ]


def skill_fit_reason(skill_id, matches, selected, interpretation, plan_steps, sentiment):
    """Explain why a skill did or did not fit in student-friendly language."""
    label = skill_id.replace("_", " ")

    if skill_id == "meme_67" and matches:
        return "The prompt asks for the 6-7 meme routine, so the planner selects the custom playful sequence."
    if skill_id == "meme_67":
        return "The 6-7 routine is available as a playful custom sequence, but this prompt did not ask for it."
    if matches and skill_id == "celebrate":
        return "The prompt includes success or excitement cues, so celebrating is a strong match."
    if matches:
        return f"The prompt directly includes cue{'s' if len(matches) > 1 else ''} for {label}."
    if selected and interpretation.get("source") == "llm":
        return "The language interpretation layer selected this as the best supported action even without an exact keyword."
    if selected:
        return "The local planner selected this as the closest supported classroom action."
    if skill_id == "wave":
        return "Wave is friendly and safe, but the prompt does not mainly ask for greeting."
    if skill_id == "celebrate":
        return "Celebrate is available, but the prompt does not clearly express success or excitement."
    if skill_id == "check_around":
        return "Look around is a safe option, but the prompt does not clearly ask the robot to search or scan."
    if plan_steps:
        return "This action is available, but another supported skill matched the prompt better."
    return "This action is available, but no clear cue in the prompt matched it."


def score_skill_candidate(skill_id, user_text, interpretation, plan, sentiment):
    """Score a candidate skill using visible evidence, not hidden chain-of-thought."""
    steps = plan.get("steps", [])
    selected = skill_id in steps
    matches = matched_keywords_for_skill(skill_id, user_text)
    score = 0

    score += len(matches) * 3
    if selected:
        score += 5
    if skill_id == "celebrate" and sentiment.get("affect") == "excited":
        score += 3
    if skill_id == "rest" and sentiment.get("affect") == "frustrated":
        score += 1

    if selected:
        fit = "strong"
    elif score >= 4:
        fit = "medium"
    elif matches:
        fit = "medium"
    else:
        fit = "weak"

    commands = SKILL_LIBRARY[skill_id]["commands"]
    return {
        "skill_id": skill_id,
        "label": skill_id.replace("_", " "),
        "selected": selected,
        "fit": fit,
        "score": score,
        "matched_cues": matches[:4],
        "available": True,
        "commands": commands,
        "command_text": " -> ".join(commands),
        "reason": skill_fit_reason(skill_id, matches, selected, interpretation, steps, sentiment),
    }


def build_candidate_scores(user_text, sentiment, interpretation, plan):
    """Return the most useful candidate rows for the student-facing explanation."""
    steps = plan.get("steps", [])
    required_ids = list(steps)

    scored = [
        score_skill_candidate(skill_id, user_text, interpretation, plan, sentiment)
        for skill_id in SKILL_LIBRARY
    ]

    rows_by_skill = {row["skill_id"]: row for row in scored}
    selected_rows = [
        rows_by_skill[skill_id]
        for skill_id in steps
        if skill_id in rows_by_skill
    ]
    evidence_rows = [
        row for row in scored
        if row["matched_cues"] and not row["selected"]
    ]
    fallback_rows = [
        row for row in scored
        if row["skill_id"] in ("wave", "celebrate", "check_around")
        and not row["selected"]
        and row not in evidence_rows
    ]

    rows = selected_rows + sorted(
        evidence_rows,
        key=lambda row: (row["score"], len(row["matched_cues"])),
        reverse=True
    )

    for row in fallback_rows:
        if row["skill_id"] not in required_ids and row not in rows:
            rows.append(row)
        if len(rows) >= 3:
            break

    if len(rows) < 3:
        for row in scored:
            if row not in rows:
                rows.append(row)
            if len(rows) >= 3:
                break

    return rows[:3]


def describe_intent(user_text, sentiment, plan):
    """Create a short classroom-friendly interpretation label."""
    if not plan.get("steps"):
        return "The prompt needs a clearer supported robot action."
    if sentiment.get("affect") == "excited" and "celebrate" in plan.get("steps", []):
        return "The prompt sounds like a successful or exciting event."
    if len(plan.get("steps", [])) > 1:
        return "The prompt asks for more than one robot action."
    first_step = plan["steps"][0].replace("_", " ")
    return f"The prompt maps most closely to {first_step}."


def build_reasoning(user_text, sentiment, interpretation, plan, serial_responses):
    """Build a visible, structured explanation for students and logs."""
    steps = plan.get("steps", [])
    selected = steps[0] if steps else None
    candidates = build_candidate_scores(user_text, sentiment, interpretation, plan)

    blocked = [sr for sr in serial_responses if sr.get("blocked")]
    failures = [sr for sr in serial_responses if sr.get("success") is False]
    successes = [sr for sr in serial_responses if sr.get("success")]

    if not plan.get("commands"):
        validation_message = "No command was sent because the prompt needs revision."
        validation_status = "needs_revision"
    elif blocked:
        validation_message = "One or more commands were blocked by the classroom safety rules."
        validation_status = "blocked"
    elif failures:
        validation_message = "The plan was safe, but the robot connection did not acknowledge every command."
        validation_status = "connection_issue"
    elif successes:
        validation_message = "All selected commands were in the classroom allowlist and were sent to the robot."
        validation_status = "sent"
    else:
        validation_message = "All selected commands are in the classroom allowlist."
        validation_status = "validated"

    return {
        "intent": describe_intent(user_text, sentiment, plan),
        "status": validation_status,
        "planner_source": interpretation.get("source", "rule"),
        "confidence_note": "Confidence is a planner estimate, not a guarantee.",
        "candidates": candidates,
        "selected_skill": selected,
        "commands": plan.get("commands", []),
        "validation": {
            "status": validation_status,
            "message": validation_message,
        },
        "revision_tip": "If the result is not what you wanted, change the prompt by naming the desired behavior more clearly.",
    }


def validate_command(cmd):
    """Validate direct serial commands against an allowlist and joint limits.

    Parameterized joint commands use the Petoi serial form where the token
    letter is immediately followed by the first joint index, e.g. "m0 30" or
    "i0 30 1 -10". This is exactly what the camera tracker and the manual
    joint controls send, so the validator must accept it (the older
    space-separated form "m 0 30" is tolerated too). Joint-range limits are
    enforced here before anything reaches the robot.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return False, "Empty command"

    if cmd in ALLOWED_DIRECT_COMMANDS:
        return True, None

    token = cmd[0]

    if token == "m":
        parts = cmd[1:].split()
        if len(parts) != 2:
            return False, "Single-joint command must be: m<joint> <angle>"
        try:
            joint = int(parts[0])
            angle = int(parts[1])
        except ValueError:
            return False, "Single-joint command arguments must be integers"
        if joint not in JOINT_LIMITS:
            return False, f"Joint {joint} is not enabled"
        low, high = JOINT_LIMITS[joint]
        if not (low <= angle <= high):
            return False, f"Joint {joint} angle must be between {low} and {high}"
        return True, None

    if token == "i":
        parts = cmd[1:].split()
        if len(parts) < 2 or len(parts) % 2 != 0:
            return False, "Multi-joint command must contain joint/angle pairs"
        try:
            values = [int(p) for p in parts]
        except ValueError:
            return False, "Multi-joint command arguments must be integers"
        for idx in range(0, len(values), 2):
            joint = values[idx]
            angle = values[idx + 1]
            if joint not in JOINT_LIMITS:
                return False, f"Joint {joint} is not enabled"
            low, high = JOINT_LIMITS[joint]
            if not (low <= angle <= high):
                return False, f"Joint {joint} angle must be between {low} and {high}"
        return True, None

    return False, "Command is not part of the allowed classroom command set"


def append_interaction_log(record):
    """Persist a structured interaction record as JSONL for later analysis."""
    payload = dict(record)
    payload["logged_at"] = datetime.now(timezone.utc).isoformat()

    with interaction_log_lock:
        with open(INTERACTION_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _phrase_present(phrase, text):
    """Word-boundary match so short cues like 'no' don't fire inside
    'now'/'know', and 'why' only matches the standalone word. Handles
    multi-word and apostrophe phrases (e.g. \"doesn't work\")."""
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def analyze_sentiment(user_text):
    """Lightweight lexicon-based sentiment for classroom interaction analysis."""
    normalized = (user_text or "").lower().strip()
    tokens = re.findall(r"[a-z']+", normalized)

    score = 0.0
    matches = []

    for phrase, weight in POSITIVE_SENTIMENT_TERMS.items():
        if _phrase_present(phrase, normalized):
            score += weight
            matches.append(phrase)

    for phrase, weight in NEGATIVE_SENTIMENT_TERMS.items():
        if _phrase_present(phrase, normalized):
            score += weight
            matches.append(phrase)

    # Mild intensity boost for repeated punctuation and emphatic wording.
    if "!" in user_text:
        score += 0.2
    if user_text.isupper() and user_text.strip():
        score -= 0.4

    uncertainty = any(_phrase_present(phrase, normalized) for phrase in UNCERTAINTY_TERMS)
    if uncertainty and score > -0.5:
        score -= 0.2

    score = max(-3.0, min(3.0, score))
    normalized_score = round(score / 3.0, 2)

    if normalized_score <= -0.3:
        label = "negative"
    elif normalized_score >= 0.3:
        label = "positive"
    else:
        label = "neutral"

    if normalized_score <= -0.45:
        affect = "frustrated"
    elif uncertainty:
        affect = "uncertain"
    elif normalized_score >= 0.45:
        affect = "excited"
    else:
        affect = "calm"

    return {
        "label": label,
        "score": normalized_score,
        "affect": affect,
        "matched_terms": matches[:6],
        "token_count": len(tokens),
    }

# ─── Flask App ─────────────────────────────────────────────
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]}})

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)

@app.route('/api/status', methods=['GET'])
def status():
    health = serial_mgr.health_check()
    return jsonify({
        "connected": serial_mgr.connected,
        "port": COM_PORT,
        "model": MODEL,
        "health": health
    })

@app.route('/api/command', methods=['POST'])
def handle_command():
    """Receive voice text, interpret it, then execute a constrained skill plan."""
    started_at = time.time()
    data = request.json
    user_text = data.get("text", "").strip()

    if not user_text:
        return jsonify({"error": "No text provided"}), 400

    print(f"\n{'='*50}")
    print(f"[VOICE] \"{user_text}\"")
    print(f"{'='*50}")

    sentiment = analyze_sentiment(user_text)
    interpretation = call_llm(user_text)
    plan = plan_actions(user_text, interpretation)
    commands = plan.get("commands", [])
    command_delays = plan.get("command_delays", [])
    explanation = plan.get("explanation", "")
    emotion = plan.get("emotion", "curious")
    delay = plan.get("delay", 0)
    trace = plan.get("trace", {})

    # Execute commands sequentially with delays between them
    serial_responses = []
    for idx, cmd in enumerate(commands):
        cmd_clean = cmd.strip()

        is_valid, validation_error = validate_command(cmd_clean)
        if not is_valid:
            serial_responses.append({"cmd": cmd_clean, "blocked": True, "reason": validation_error})
            continue

        print(f"[EXEC] Command {idx+1}/{len(commands)}: {cmd_clean}")
        result = serial_mgr.send(cmd_clean, wait_for_ack=True)
        serial_responses.append({"cmd": cmd_clean, **result})

        # Wait between commands so robot can finish the action
        wait_after = command_delays[idx] if idx < len(command_delays) else delay
        if idx < len(commands) - 1 and wait_after > 0:
            wait_time = min(wait_after, 5)  # Cap at 5 seconds
            print(f"[WAIT] {wait_time}s before next command...")
            time.sleep(wait_time)

    if sentiment["affect"] == "frustrated":
        explanation += " If that was frustrating, try a short command like 'sit' or 'wave'."
        if emotion == "alert":
            emotion = "confused"
    elif sentiment["affect"] == "uncertain":
        explanation += " If you want, try a simple command first like 'walk forward' or 'sit'."

    reasoning = build_reasoning(user_text, sentiment, interpretation, plan, serial_responses)

    response_data = {
        "input": user_text,
        "explanation": explanation,
        "emotion": emotion,
        "commands": commands,
        "command_delays": command_delays,
        "delay": delay,
        "serial_responses": serial_responses,
        "trace": trace,
        "reasoning": reasoning,
        "sentiment": sentiment,
    }

    append_interaction_log({
        "type": "nl_command",
        "input": user_text,
        "sentiment": sentiment,
        "interpretation": interpretation,
        "plan": {
            "skill_id": plan.get("skill_id"),
            "steps": plan.get("steps", []),
            "commands": commands,
            "command_delays": command_delays,
            "delay": delay,
            "emotion": emotion,
        },
        "reasoning": reasoning,
        "serial_responses": serial_responses,
        "latency_ms": round((time.time() - started_at) * 1000, 1),
    })

    return jsonify(response_data)

@app.route('/api/direct', methods=['POST'])
def direct_command():
    """Send a raw serial command (for camera tracking — low latency)."""
    started_at = time.time()
    data = request.json
    cmd = data.get("cmd", "").strip()

    if not cmd:
        return jsonify({"error": "No command"}), 400

    is_valid, validation_error = validate_command(cmd)
    if not is_valid:
        append_interaction_log({
            "type": "direct_command",
            "input": cmd,
            "valid": False,
            "error": validation_error,
            "latency_ms": round((time.time() - started_at) * 1000, 1),
        })
        return jsonify({"error": validation_error}), 403

    is_tracking = cmd.startswith('i0 ') or cmd.startswith('m0 ')
    result = serial_mgr.send(cmd, wait_for_ack=not is_tracking)
    append_interaction_log({
        "type": "direct_command",
        "input": cmd,
        "valid": True,
        "tracking_mode": is_tracking,
        "result": result,
        "latency_ms": round((time.time() - started_at) * 1000, 1),
    })
    return jsonify(result)

@app.route('/api/joints', methods=['GET'])
def get_joints():
    """Query current joint angles."""
    result = serial_mgr.send("j", wait_for_ack=True)
    return jsonify(result)

@app.route('/api/stop', methods=['POST'])
def emergency_stop():
    """Emergency stop — immediately rest all servos."""
    print("\n[!!!] EMERGENCY STOP")
    result = serial_mgr.send("d", wait_for_ack=True)
    return jsonify({"stopped": True, **result})

@app.route('/api/reconnect', methods=['POST'])
def reconnect():
    """Force reconnect the serial port."""
    print("\n[RECONNECT] Manual reconnect requested...")
    success = serial_mgr.connect(retries=3)
    return jsonify({"reconnected": success, "port": COM_PORT})

@app.route('/api/ports', methods=['GET'])
def list_ports():
    """List all available COM ports."""
    ports = serial_mgr.scan_ports()
    return jsonify({"ports": ports, "current": COM_PORT})

# ─── Main ──────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  🐾 Petoi Bittle X — Voice & Camera Controller")
    print("  🛡️  Safeguards: atexit cleanup, lock timeout,")
    print("      auto-reconnect, signal handlers, watchdog")
    print("=" * 60)
    print(f"  Model:  {MODEL}")
    print(f"  Port:   {COM_PORT}")
    print()

    if serial_mgr.connect():
        print(f"  ✅ Robot connected on {COM_PORT}")
    else:
        print(f"  ⚠️  Could not connect to {COM_PORT} — running in demo mode")
        print(f"      (will auto-reconnect when first command is sent)")

    print(f"\n  🌐 Open http://localhost:8080 in your browser")
    print("=" * 60)

    app.run(host=HOST, port=8080, debug=False, threaded=True)
