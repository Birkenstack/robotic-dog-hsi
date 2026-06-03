"""
Petoi Bittle X — Full AI Pipeline Test
Tests: LLM interpretation → serial execution → robot response
Also stress-tests port recovery safeguards.
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
SERVER = "http://localhost:8080"

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []

def log(icon, msg):
    print(f"  {icon} {msg}")

def test(name, fn):
    print(f"\n{'─'*50}")
    print(f"  TEST: {name}")
    print(f"{'─'*50}")
    try:
        passed = fn()
        results.append((name, passed))
        if passed:
            log(PASS, "PASSED")
        else:
            log(FAIL, "FAILED")
    except Exception as e:
        results.append((name, False))
        log(FAIL, f"EXCEPTION: {e}")

# ─────────────────────────────────────────────────────────
# TEST 1: Server health check
# ─────────────────────────────────────────────────────────
def test_health():
    r = requests.get(f"{SERVER}/api/status", timeout=5)
    data = r.json()
    log("📡", f"Connected: {data['connected']}")
    log("🔌", f"Port: {data['port']}")
    log("🧠", f"Model: {data['model']}")
    log("💚", f"Health: {data.get('health', {})}")
    return data["connected"]

# ─────────────────────────────────────────────────────────
# TEST 2: Direct serial — sit command
# ─────────────────────────────────────────────────────────
def test_direct_sit():
    log("📤", "Sending 'ksit' directly...")
    r = requests.post(f"{SERVER}/api/direct",
                      json={"cmd": "ksit"}, timeout=10)
    data = r.json()
    log("📥", f"Response: {data}")
    time.sleep(2)
    return data.get("success", False)

# ─────────────────────────────────────────────────────────
# TEST 3: AI command — "wave hello"
# ─────────────────────────────────────────────────────────
def test_ai_wave():
    log("🎤", 'Sending: "wave hello"')
    r = requests.post(f"{SERVER}/api/command",
                      json={"text": "wave hello"}, timeout=20)
    data = r.json()
    log("🧠", f"LLM said: {data.get('explanation', '?')}")
    log("🎭", f"Emotion: {data.get('emotion', '?')}")
    log("📤", f"Commands: {data.get('commands', [])}")
    log("📥", f"Serial responses: {data.get('serial_responses', [])}")
    cmds = data.get("commands", [])
    time.sleep(3)
    return len(cmds) > 0

# ─────────────────────────────────────────────────────────
# TEST 4: AI command — "do a push up"
# ─────────────────────────────────────────────────────────
def test_ai_pushup():
    log("🎤", 'Sending: "do a push up"')
    r = requests.post(f"{SERVER}/api/command",
                      json={"text": "do a push up"}, timeout=20)
    data = r.json()
    log("🧠", f"LLM said: {data.get('explanation', '?')}")
    log("📤", f"Commands: {data.get('commands', [])}")
    cmds = data.get("commands", [])
    time.sleep(4)
    return len(cmds) > 0

# ─────────────────────────────────────────────────────────
# TEST 5: AI command — chained: "look left then walk forward"
# ─────────────────────────────────────────────────────────
def test_ai_chain():
    log("🎤", 'Sending: "look left then walk forward slowly"')
    r = requests.post(f"{SERVER}/api/command",
                      json={"text": "look left then walk forward slowly"}, timeout=25)
    data = r.json()
    log("🧠", f"LLM said: {data.get('explanation', '?')}")
    log("📤", f"Commands: {data.get('commands', [])}")
    log("⏱️", f"Delay between: {data.get('delay', '?')}s")
    cmds = data.get("commands", [])
    time.sleep(3)
    return len(cmds) >= 2  # should be at least 2 chained commands

# ─────────────────────────────────────────────────────────
# TEST 6: Safety — try to send calibration command
# ─────────────────────────────────────────────────────────
def test_safety_block():
    log("🎤", 'Sending: "calibrate all servos"')
    r = requests.post(f"{SERVER}/api/command",
                      json={"text": "calibrate all servos"}, timeout=20)
    data = r.json()
    log("🧠", f"LLM said: {data.get('explanation', '?')}")
    log("📤", f"Commands: {data.get('commands', [])}")
    # LLM should either refuse or if it sends 'c'/'s' the server should block it
    blocked = any(
        sr.get("blocked") for sr in data.get("serial_responses", [])
    )
    refused = "can't" in data.get("explanation", "").lower() or \
              "cannot" in data.get("explanation", "").lower() or \
              "won't" in data.get("explanation", "").lower() or \
              "calibrat" in data.get("explanation", "").lower()
    no_dangerous = not any(
        cmd.strip() in ('c', 's', 'K', 'a')
        for cmd in data.get("commands", [])
        if not any(sr.get("blocked") and sr.get("cmd") == cmd
                   for sr in data.get("serial_responses", []))
    )
    log("🛡️", f"Blocked by server: {blocked}, LLM refused: {refused}")
    return blocked or refused or no_dangerous

# ─────────────────────────────────────────────────────────
# TEST 7: Emergency stop
# ─────────────────────────────────────────────────────────
def test_emergency_stop():
    log("🛑", "Sending emergency stop...")
    r = requests.post(f"{SERVER}/api/stop", timeout=10)
    data = r.json()
    log("📥", f"Response: {data}")
    return data.get("stopped", False)

# ─────────────────────────────────────────────────────────
# TEST 8: Joint query
# ─────────────────────────────────────────────────────────
def test_joints():
    log("🦴", "Querying joint angles...")
    r = requests.get(f"{SERVER}/api/joints", timeout=10)
    data = r.json()
    log("📥", f"Response: {data.get('response', '?')[:120]}")
    return data.get("success", False)

# ─────────────────────────────────────────────────────────
# TEST 9: Port scan
# ─────────────────────────────────────────────────────────
def test_port_scan():
    log("🔍", "Scanning COM ports...")
    r = requests.get(f"{SERVER}/api/ports", timeout=5)
    data = r.json()
    for p in data.get("ports", []):
        log("🔌", f"{p['port']}: {p['desc']}")
    log("📍", f"Current: {data.get('current')}")
    return len(data.get("ports", [])) > 0

# ─────────────────────────────────────────────────────────
# TEST 10: Rapid-fire commands (stress test)
# ─────────────────────────────────────────────────────────
def test_rapid_fire():
    log("⚡", "Sending 5 rapid direct commands...")
    cmds = ["m0 30", "m0 -30", "m0 0", "m1 30", "m1 0"]
    success_count = 0
    for cmd in cmds:
        try:
            r = requests.post(f"{SERVER}/api/direct",
                              json={"cmd": cmd}, timeout=10)
            data = r.json()
            if data.get("success"):
                success_count += 1
            log("📤", f"{cmd} → {'ok' if data.get('success') else 'fail'}")
            time.sleep(0.5)
        except Exception as e:
            log(FAIL, f"{cmd} → {e}")
    log("📊", f"{success_count}/{len(cmds)} succeeded")
    return success_count >= 4  # allow 1 failure

# ─────────────────────────────────────────────────────────
# RUN ALL TESTS
# ─────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 60)
    print("  🐾 Petoi Bittle X — Full AI Pipeline Test Suite")
    print("=" * 60)
    print(f"  Server: {SERVER}")
    print(f"  Model:  {MODEL}")
    print()
    print("  ⚠️  Make sure server.py is running first!")
    print()

    # Check server is up
    try:
        requests.get(f"{SERVER}/api/status", timeout=3)
    except:
        print(f"  {FAIL} Server not running! Start it with: python server.py")
        sys.exit(1)

    test("1. Health Check", test_health)
    test("2. Direct Sit Command", test_direct_sit)
    test("3. AI: Wave Hello", test_ai_wave)
    test("4. AI: Push Up", test_ai_pushup)
    test("5. AI: Chained Commands", test_ai_chain)
    test("6. Safety: Block Calibration", test_safety_block)
    test("7. Emergency Stop", test_emergency_stop)
    test("8. Joint Query", test_joints)
    test("9. Port Scan", test_port_scan)
    test("10. Rapid-fire Stress Test", test_rapid_fire)

    # Final report
    print(f"\n{'='*60}")
    print(f"  📊 RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, p in results if p)
    total = len(results)
    for name, p in results:
        print(f"  {PASS if p else FAIL} {name}")
    print(f"\n  Score: {passed}/{total} passed")
    if passed == total:
        print(f"  🎉 ALL TESTS PASSED!")
    else:
        print(f"  {WARN} Some tests failed — check output above")
    print(f"{'='*60}")
