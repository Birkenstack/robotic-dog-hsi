"""
Petoi Bittle X — Reproducible Demonstration Harness
===================================================

Purpose (for the paper, NOT pass/fail testing — see test_pipeline.py for that):
  Run a FIXED, curated set of natural-language utterances through the live
  pipeline and capture the full grounding trace for each one. Each demo is
  tagged with the paper claim it is meant to support, so the output doubles as:

    1. A SHOWABLE artifact  -> demo_transcript.md  (drop figures/tables in paper)
    2. A REPRODUCIBLE artifact -> demo_results.jsonl (re-runnable evidence)

Reproducibility notes:
  - Works with the robot DISCONNECTED: grounding + validation are still shown;
    only the execution acks are unavailable, and that is stated explicitly.
  - Works with NO API key: the server falls back to the DETERMINISTIC rule-based
    planner, so a reviewer with no OpenRouter key and no Bittle X can still
    reproduce the language->plan->command grounding identically.
  - The per-demo "planner" field reports whether that result came from the LLM
    or the deterministic fallback, so the artifact is always honest about which.

Usage:
  1. Start the server first:   python server.py
  2. In another terminal:      python reproduce_demo.py
"""

import sys
import json
import time
import textwrap
from datetime import datetime, timezone

import requests

SERVER = "http://localhost:8080"
TRANSCRIPT_PATH = "demo_transcript.md"
RESULTS_PATH = "demo_results.jsonl"

# ─────────────────────────────────────────────────────────────────────────────
# The curated demonstrations. Each one maps an input to the paper claim it
# supports. Edit this list to match the exact examples you cite in the paper.
# ─────────────────────────────────────────────────────────────────────────────
DEMOS = [
    {
        "use_case": "Use Case 1 — Single-utterance grounding",
        "claim": "A single natural-language utterance is grounded end-to-end "
                 "into a validated, embodied robot action.",
        "kind": "nl",
        "input": "wave hello",
    },
    {
        "use_case": "Use Case 2 — Compositional grounding",
        "claim": "Language is decomposed into an ordered multi-step plan "
                 "(not a 1:1 lookup), then grounded into a command sequence.",
        "kind": "nl",
        "input": "sit then wave",
    },
    {
        "use_case": "Safety boundary (NL) — constrained interpretation",
        "claim": "Out-of-scope requests are kept inside the classroom-safe "
                 "skill set; the LLM never emits raw firmware/calibration tokens.",
        "kind": "nl",
        "input": "calibrate all servos",
    },
    {
        "use_case": "Safety boundary (direct) — allowlist enforcement",
        "claim": "A raw calibration command is rejected by the server allowlist "
                 "BEFORE reaching the robot, and the attempt is logged.",
        "kind": "direct",
        "input": "c",
    },
]


def get_status():
    try:
        r = requests.get(f"{SERVER}/api/status", timeout=5)
        return r.json()
    except Exception:
        return None


def run_nl(text):
    r = requests.post(f"{SERVER}/api/command", json={"text": text}, timeout=30)
    return r.status_code, r.json()


def run_direct(cmd):
    r = requests.post(f"{SERVER}/api/direct", json={"cmd": cmd}, timeout=15)
    return r.status_code, r.json()


def wrap(text, indent="      "):
    return textwrap.fill(
        str(text), width=78,
        initial_indent=indent, subsequent_indent=indent,
    )


def fmt_serial(serial_responses):
    """Human-readable one-liners for each serial command + its result."""
    lines = []
    for sr in serial_responses or []:
        cmd = sr.get("cmd", "?")
        if sr.get("blocked"):
            lines.append(f"{cmd} -> BLOCKED ({sr.get('reason')})")
        elif sr.get("success"):
            lines.append(f"{cmd} -> ok ({sr.get('response')})")
        else:
            lines.append(f"{cmd} -> not executed ({sr.get('error', 'no robot')})")
    return lines


def print_nl_demo(demo, data, connected):
    trace = data.get("trace", {})
    interp_source = trace.get("planner_source", "?")
    steps = trace.get("steps", [])
    commands = data.get("commands", [])
    conf = trace.get("confidence", "?")

    print(f"\n{'='*70}")
    print(f"  {demo['use_case']}")
    print(f"{'='*70}")
    print(f"  PAPER CLAIM:")
    print(wrap(demo["claim"]))
    print(f"\n  [1] Student utterance:  \"{data.get('input')}\"")
    print(f"  [2] Interpreter:        {interp_source.upper()} "
          f"(confidence {conf})")
    print(f"      rationale:")
    print(wrap(trace.get("rationale", ""), indent="        "))
    print(f"  [3] Grounded plan:      {steps}  ->  commands {commands}")
    print(f"  [4] Spoken explanation: \"{data.get('explanation')}\"  "
          f"[emotion: {data.get('emotion')}]")
    print(f"  [5] Robot execution:")
    for line in fmt_serial(data.get("serial_responses")):
        print(f"        {line}")
    if not connected:
        print(f"        (robot not connected — grounding + validation shown above;")
        print(f"         execution acks unavailable in this run)")


def print_direct_demo(demo, status_code, data):
    print(f"\n{'='*70}")
    print(f"  {demo['use_case']}")
    print(f"{'='*70}")
    print(f"  PAPER CLAIM:")
    print(wrap(demo["claim"]))
    print(f"\n  [1] Raw command sent:   \"{demo['input']}\"")
    print(f"  [2] HTTP status:        {status_code}")
    if status_code == 403 or "error" in data:
        print(f"  [3] Result:             REJECTED by allowlist")
        print(f"      reason:             {data.get('error')}")
        print(f"  [4] Reached robot?:     NO (blocked at server, logged)")
    else:
        print(f"  [3] Result:             {data}")


def main():
    print("=" * 70)
    print("  Petoi Bittle X — Reproducible Demonstration Harness")
    print("=" * 70)

    status = get_status()
    if status is None:
        print("\n  ERROR: server not reachable at", SERVER)
        print("  Start it first in another terminal:  python server.py")
        sys.exit(1)

    connected = bool(status.get("connected"))
    print(f"  Server:  {SERVER}")
    print(f"  Model:   {status.get('model')}")
    print(f"  Robot:   {'CONNECTED' if connected else 'not connected (demo mode)'}")
    print(f"  Run at:  {datetime.now(timezone.utc).isoformat()}")

    captured = []
    summary_rows = []

    for demo in DEMOS:
        if demo["kind"] == "nl":
            code, data = run_nl(demo["input"])
            print_nl_demo(demo, data, connected)
            trace = data.get("trace", {})
            summary_rows.append({
                "input": demo["input"],
                "planner": trace.get("planner_source", "?"),
                "steps": "+".join(trace.get("steps", [])) or "-",
                "commands": " ".join(data.get("commands", [])) or "-",
                "confidence": trace.get("confidence", "-"),
                "result": "executed" if connected else "grounded (no robot)",
            })
            captured.append({"demo": demo, "http_status": code, "response": data})
        else:
            code, data = run_direct(demo["input"])
            print_direct_demo(demo, code, data)
            summary_rows.append({
                "input": demo["input"],
                "planner": "n/a (raw)",
                "steps": "-",
                "commands": demo["input"],
                "confidence": "-",
                "result": "REJECTED" if (code == 403 or "error" in data) else "sent",
            })
            captured.append({"demo": demo, "http_status": code, "response": data})
        time.sleep(0.5)

    # ── Summary table (paper-ready) ──────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  SUMMARY")
    print(f"{'='*70}")
    header = f"  {'utterance':<22}{'planner':<12}{'plan':<20}{'conf':<7}{'result'}"
    print(header)
    print("  " + "-" * 66)
    for row in summary_rows:
        print(f"  {row['input']:<22}{row['planner']:<12}"
              f"{row['steps']:<20}{str(row['confidence']):<7}{row['result']}")

    # ── Write artifacts ──────────────────────────────────────────────────────
    write_transcript(status, connected, captured, summary_rows)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        for item in captured:
            fh.write(json.dumps(item, ensure_ascii=True) + "\n")

    print(f"\n  Wrote: {TRANSCRIPT_PATH}  (paste into paper)")
    print(f"  Wrote: {RESULTS_PATH}  (machine-readable evidence)")
    print(f"{'='*70}\n")


def write_transcript(status, connected, captured, summary_rows):
    """Emit a clean Markdown transcript suitable for the paper appendix."""
    lines = []
    lines.append("# Reproducible Demonstration Transcript\n")
    lines.append(f"- Model: `{status.get('model')}`")
    lines.append(f"- Robot: {'connected' if connected else 'not connected (demo mode)'}")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}\n")
    lines.append("Each demonstration maps a fixed input to the paper claim it supports. "
                 "The `planner` column reports whether the interpretation came from the "
                 "LLM or the deterministic rule-based fallback.\n")

    lines.append("## Summary\n")
    lines.append("| Utterance | Planner | Grounded plan | Conf. | Result |")
    lines.append("|---|---|---|---|---|")
    for row in summary_rows:
        lines.append(f"| {row['input']} | {row['planner']} | {row['steps']} "
                     f"| {row['confidence']} | {row['result']} |")
    lines.append("")

    lines.append("## Full traces\n")
    for item in captured:
        demo = item["demo"]
        data = item["response"]
        lines.append(f"### {demo['use_case']}\n")
        lines.append(f"**Claim:** {demo['claim']}\n")
        if demo["kind"] == "nl":
            trace = data.get("trace", {})
            lines.append(f"1. **Utterance:** `{data.get('input')}`")
            lines.append(f"2. **Interpreter:** {trace.get('planner_source', '?')} "
                         f"(confidence {trace.get('confidence', '?')})")
            lines.append(f"3. **Rationale:** {trace.get('rationale', '')}")
            lines.append(f"4. **Grounded plan:** `{trace.get('steps', [])}` -> "
                         f"`{data.get('commands', [])}`")
            lines.append(f"5. **Explanation:** {data.get('explanation')} "
                         f"(emotion: {data.get('emotion')})")
            lines.append(f"6. **Execution:**")
            for line in fmt_serial(data.get("serial_responses")):
                lines.append(f"   - `{line}`")
        else:
            lines.append(f"1. **Raw command:** `{demo['input']}`")
            lines.append(f"2. **HTTP status:** {item['http_status']}")
            lines.append(f"3. **Result:** {'REJECTED by allowlist' if 'error' in data else data}")
            if "error" in data:
                lines.append(f"4. **Reason:** {data.get('error')}")
        lines.append("")

    with open(TRANSCRIPT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    main()
