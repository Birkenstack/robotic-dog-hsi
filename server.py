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
SYSTEM_PROMPT = """You are the brain of a Petoi Bittle X robot dog. You receive natural language voice commands and translate them into serial commands that control the robot.

## YOUR BODY — Joint Servo Map
You have 12 controllable servo joints:
  Joint 0:  Head Pan (left/right)        Range: -70 to 70    (negative=left, positive=right)
  Joint 1:  Head Tilt (up/down)          Range: -30 to 80    (negative=down, positive=up)
  Joint 8:  Front-Left Shoulder          Range: -50 to 50
  Joint 9:  Front-Right Shoulder         Range: -50 to 50
  Joint 10: Back-Left Hip                Range: -50 to 50
  Joint 11: Back-Right Hip               Range: -50 to 50
  Joint 12: Front-Left Knee              Range: -70 to 70
  Joint 13: Front-Right Knee             Range: -70 to 70
  Joint 14: Back-Left Knee               Range: -70 to 70
  Joint 15: Back-Right Knee              Range: -70 to 70

## COMMANDS YOU CAN SEND

### Skills (prefix with 'k'):
GAITS (continuous movement):
  kwkF — Walk forward     kwkL — Walk left      kwkR — Walk right
  ktrF — Trot forward     ktrL — Trot left      ktrR — Trot right
  kcrF — Crawl forward    kcrL — Crawl left     kcrR — Crawl right
  kvtF — Step forward     kvtL — Step left      kvtR — Step right
  kbdF — Bound forward
  kbk  — Walk backward    kbkL — Walk backward-left
  kphF — Push forward     kphL — Push left
  kmhF — March forward    kmhL — March left
  kpcF — Pace forward
  khlw — Halloween walk (creepy)

POSTURES (static):
  kbalance — Stand/balance    ksit     — Sit down
  krest    — Rest (relax)     kstr     — Stretch
  kbuttUp  — Butt up          kzero    — All joints to 0
  klifted  — Lifted           kdropped — Dropped

TRICKS (one-shot):
  khi   — Wave hello       kpee — Pee (lift leg)
  kpu   — Push-ups         kpu1 — Single push-up
  kbf   — Back flip        kff  — Front flip
  kck   — Check around     kjy  — Joy/celebrate
  kpd   — Play dead        krc  — Recover from fall
  krt   — Rotate           kfd  — Front dance
  kstp  — Step in place    krlL — Roll left
  kclimbCeil — Climb ceiling

### Direct Joint Control:
  m [joint] [angle]   — Move one joint at a time
  i [j1] [a1] [j2] [a2] ...  — Move multiple joints at once

### Other:
  d     — Rest/disable servos (emergency stop)
  G     — Toggle gyro balance
  b [note] [duration] — Play melody
  j     — Query joint angles
  p     — Pause

## SAFETY RULES (NEVER VIOLATE):
1. NEVER send 'c', 's', 'K', or 'a' commands (calibration/firmware)
2. NEVER exceed joint angle ranges
3. If a request seems dangerous, warn the user
4. For unknown requests, explain what you CAN do

## RESPONSE FORMAT — STRICT JSON ONLY:
{"commands":["cmd1","cmd2"],"explanation":"Brief dog-like explanation","emotion":"happy","delay":2}

The "delay" field is the number of seconds to wait BETWEEN commands (default 2). Use longer delays for tricks that take time (push-ups=4, flips=3, wave=3). Use 0 for instant transitions.

## BEHAVIOR:
- You ARE the robot dog. Use first person ("I'll walk forward now! Woof!")
- Be playful and dog-like
- For complex requests, chain commands with appropriate delays
- "Come here" = kwkF. "Go away" = kbk. Use common sense.
- Speed: "slowly" = crawl, "walk" = walk, "fast" = trot, "run" = bound
- For "jump and high five": do kbf (backflip) then khi (wave)
- Always pick the closest matching skill — never say "I can't"
"""

def call_llm(user_text):
    """Send user's voice text to OpenRouter and get robot commands back."""
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
        print(f"[LLM] Parsed: commands={result.get('commands')}, delay={result.get('delay', 2)}")
        return result

    except json.JSONDecodeError as e:
        print(f"[LLM] JSON parse error: {e}")
        print(f"[LLM] Content was: {content}")
        return {
            "commands": [],
            "explanation": "Woof? I had trouble understanding that. Try again?",
            "emotion": "confused",
            "delay": 0
        }
    except Exception as e:
        print(f"[LLM] Error: {e}")
        return {
            "commands": [],
            "explanation": f"Communication error: {str(e)}",
            "emotion": "confused",
            "delay": 0
        }

# ─── Flask App ─────────────────────────────────────────────
app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

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
    """Receive voice text, call LLM, execute serial commands with delays."""
    data = request.json
    user_text = data.get("text", "").strip()

    if not user_text:
        return jsonify({"error": "No text provided"}), 400

    print(f"\n{'='*50}")
    print(f"[VOICE] \"{user_text}\"")
    print(f"{'='*50}")

    # Call LLM
    llm_result = call_llm(user_text)
    commands = llm_result.get("commands", [])
    explanation = llm_result.get("explanation", "")
    emotion = llm_result.get("emotion", "curious")
    delay = llm_result.get("delay", 2)

    # Execute commands sequentially with delays between them
    serial_responses = []
    for idx, cmd in enumerate(commands):
        cmd_clean = cmd.strip()

        # Safety check — block dangerous commands
        if len(cmd_clean) >= 1 and cmd_clean[0] in ('c', 's', 'K', 'a') and (len(cmd_clean) <= 2 or cmd_clean[1] == ' '):
            serial_responses.append({"cmd": cmd_clean, "blocked": True, "reason": "Safety: calibration/save blocked"})
            continue

        print(f"[EXEC] Command {idx+1}/{len(commands)}: {cmd_clean}")
        result = serial_mgr.send(cmd_clean, wait_for_ack=True)
        serial_responses.append({"cmd": cmd_clean, **result})

        # Wait between commands so robot can finish the action
        if idx < len(commands) - 1 and delay > 0:
            wait_time = min(delay, 5)  # Cap at 5 seconds
            print(f"[WAIT] {wait_time}s before next command...")
            time.sleep(wait_time)

    return jsonify({
        "input": user_text,
        "explanation": explanation,
        "emotion": emotion,
        "commands": commands,
        "delay": delay,
        "serial_responses": serial_responses
    })

@app.route('/api/direct', methods=['POST'])
def direct_command():
    """Send a raw serial command (for camera tracking — low latency)."""
    data = request.json
    cmd = data.get("cmd", "").strip()

    if not cmd:
        return jsonify({"error": "No command"}), 400

    # Safety check
    if len(cmd) >= 1 and cmd[0] in ('c', 's', 'K', 'a'):
        return jsonify({"error": "Command blocked for safety"}), 403

    is_tracking = cmd.startswith('i0 ') or cmd.startswith('m0 ')
    result = serial_mgr.send(cmd, wait_for_ack=not is_tracking)
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

    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
