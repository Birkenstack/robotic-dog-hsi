"""Send a wave hello command to Bittle X.

Reads the serial port from COM_PORT (in your environment or .env) so it works
on macOS/Linux (e.g. /dev/cu.usbserial-*) as well as Windows (COMx). If
COM_PORT isn't set, it tries to auto-detect a likely USB serial device.
"""
import os
import time

import serial
import serial.tools.list_ports
from dotenv import load_dotenv

load_dotenv()

BAUD = int(os.getenv("BAUD_RATE", "115200"))


def pick_port():
    """Use COM_PORT if set, otherwise guess a likely USB serial device."""
    env_port = os.getenv("COM_PORT")
    if env_port:
        return env_port
    for p in serial.tools.list_ports.comports():
        dev = p.device
        if any(tag in dev for tag in (
            "cu.usbserial", "cu.usbmodem", "cu.SLAB", "cu.wchusbserial",
            "ttyUSB", "ttyACM", "COM",
        )):
            return dev
    raise SystemExit(
        "No serial port found. Set COM_PORT in .env "
        "(e.g. COM_PORT=/dev/cu.usbserial-10 on macOS)."
    )


PORT = pick_port()

print(f"[1/4] Opening {PORT}...")
ser = serial.Serial(PORT, BAUD, timeout=2)
print(f"[2/4] Waiting for BT to stabilize...")
time.sleep(2)

# Flush startup data
if ser.in_waiting:
    ser.read(ser.in_waiting)

# Send wave hello
print(f"[3/4] Sending 'khi' (wave hello)...")
ser.write(b'khi\n')
time.sleep(3)

# Read response
if ser.in_waiting:
    resp = ser.read(ser.in_waiting).decode('utf-8', errors='replace').strip()
    print(f"[4/4] Robot response: {resp[:150]}")
else:
    print(f"[4/4] Command sent (no text response)")

print("\n✅ Done! Did the Bittle wave?")
ser.close()
