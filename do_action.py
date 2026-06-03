"""Send a wave hello command to Bittle X on COM5."""
import serial
import time

PORT = "COM5"
BAUD = 115200

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
