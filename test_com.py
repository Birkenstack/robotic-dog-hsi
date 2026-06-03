"""Quick COM test — tries BOTH COM4 and COM5 to find the Bittle."""
import serial
import time
import sys

BAUD = 115200

def test_port(port):
    print(f"\n{'='*40}")
    print(f"  Testing {port}...")
    print(f"{'='*40}")
    try:
        ser = serial.Serial(port, BAUD, timeout=2)
        time.sleep(2)  # BT stabilization

        # Flush startup noise
        if ser.in_waiting:
            garbage = ser.read(ser.in_waiting).decode('utf-8', errors='replace')
            print(f"  [startup data] {garbage.strip()[:100]}")

        # Send 'j' joint query — this always gets a response from a live Bittle
        ser.write(b'j\n')
        time.sleep(1)

        if ser.in_waiting:
            resp = ser.read(ser.in_waiting).decode('utf-8', errors='replace')
            print(f"  [RESPONSE] {resp.strip()[:200]}")
            ser.close()
            return True
        else:
            print(f"  [NO RESPONSE] Port opened but robot didn't reply")
            ser.close()
            return False

    except serial.SerialException as e:
        print(f"  [FAILED] {e}")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False

print("🔍 Scanning for Petoi Bittle X...")
print(f"   Available ports found earlier: COM4, COM5")

for port in ["COM5", "COM4"]:
    if test_port(port):
        print(f"\n✅ Bittle X found on {port}!")
        print(f"   Update your .env: COM_PORT={port}")
        sys.exit(0)

print("\n❌ No response on either port.")
print("   Make sure the Bittle is powered ON and Bluetooth is paired.")
