# Petoi Bittle X AI Controller

A browser-based controller for the Petoi Bittle X robot dog with three control modes:

- Voice mode: speak natural-language commands and translate them into robot actions with OpenRouter.
- Camera mode: track a face with the webcam and drive the head pan/tilt servos.
- Manual mode: send raw Petoi serial commands or quick action presets.

The project uses a Flask server as the bridge between the browser UI, the OpenRouter API, and the robot's serial/Bluetooth connection.

## Features

- Natural-language control through an LLM
- Static web UI served directly from Flask
- Manual command entry for direct Petoi serial commands
- Webcam face tracking for head movement
- Emergency stop endpoint and UI button
- Serial safety guards to block calibration and firmware commands
- Auto-reconnect and cleanup logic for unstable serial/Bluetooth sessions

## Tech Stack

- Frontend: plain HTML, CSS, and JavaScript
- Backend: Python + Flask
- Robot connection: `pyserial`
- AI command parsing: OpenRouter chat completions API

## Project Structure

```text
.
├── index.html          # Main UI
├── style.css           # Frontend styling
├── app.js              # Frontend behavior and API calls
├── server.py           # Flask app, serial bridge, OpenRouter integration
├── do_action.py        # Minimal one-off serial command test
├── test_com.py         # COM port probing helper
├── test_pipeline.py    # End-to-end API/robot test script
├── test_port.py        # Extra serial port test helper
├── requirements.txt    # Python dependencies
└── .env.example        # Example local configuration
```

## Requirements

Hardware:

- Petoi Bittle X
- A working serial or Bluetooth connection to the robot
- A webcam for camera tracking mode

Software:

- Python 3.10+
- Google Chrome or another browser with Web Speech API support
- An OpenRouter API key for voice and natural-language control

## Quick Start

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Copy `.env.example` to `.env` and fill in your settings.
5. Start the Flask server.
6. Open `http://localhost:8080`.

Example setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python server.py
```

On Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python server.py
```

Then open:

```text
http://localhost:8080
```

## Environment Variables

Create a `.env` file with:

```env
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini
COM_PORT=COM5
BAUD_RATE=115200
```

Notes:

- `COM_PORT` is currently set up with a Windows-style default (`COM5`).
- On macOS or Linux, use the correct serial device path, for example `/dev/tty.usbserial-*` or `/dev/cu.*`.
- If you only want to use the UI without a connected robot, the server can still start in a disconnected/demo state.

## How To Use

### Voice Mode

- Click the microphone button.
- Speak a command such as `wave hello`, `walk forward`, or `do a push up`.
- The browser sends your transcript to the Flask API.
- The server asks OpenRouter to turn that into Petoi commands.
- The server sends those commands to the robot one by one.

Voice mode depends on browser speech recognition support. Chrome is the safest default.

### Camera Mode

- Switch to Camera mode.
- Click `Start Camera`.
- Allow webcam access in the browser.
- The app detects a face-like skin-tone region and sends head pan/tilt commands.

This is a lightweight heuristic tracker, not a full face-detection model, so lighting and background can affect accuracy.

### Manual Mode

Use this if you already know Petoi commands.

Examples:

- `ksit`
- `kbalance`
- `kwkF`
- `m0 30`
- `i0 20 1 -10`

You can also type natural language in the manual box. If the input does not look like a raw serial command, the frontend routes it through the LLM pipeline.

## Safety Behavior

The server uses an allowlist, not a blocklist. Only a fixed set of
classroom-safe commands can ever reach the robot:

- the named skills in the skill library (stand, sit, walk, wave, etc.)
- a small set of safe direct tokens (for example `d`, `j`)
- parameterized joint moves (`m<joint> <angle>` and `i<joint> <angle> ...`),
  which are additionally clamped to per-joint angle limits

Anything outside this set — including calibration or firmware-write commands
such as `c`, `s`, `K`, or `a` — is rejected before it is sent, and the attempt
is recorded in the interaction log.

There is also an emergency stop button in the UI and an `Esc` keyboard
shortcut, both of which send the `d` command.

This project can still move physical hardware. Test carefully, keep clear space around the robot, and do not assume the LLM will always choose ideal actions.

## API Endpoints

- `GET /api/status`: connection status, current port, model, serial health
- `POST /api/command`: natural-language command input
- `POST /api/direct`: raw Petoi serial command
- `GET /api/joints`: query current joint angles
- `POST /api/stop`: emergency stop
- `POST /api/reconnect`: reconnect serial port
- `GET /api/ports`: scan available serial ports

## Testing Helpers

These scripts are useful during setup:

- `python test_com.py`: probes likely COM ports for a live robot response
- `python do_action.py`: sends a simple `khi` wave command
- `python test_pipeline.py`: exercises the API and robot control pipeline

`test_pipeline.py` expects `server.py` to already be running.

## Troubleshooting

### Server starts but the UI shows disconnected

- Check that the robot is powered on.
- Confirm the serial/Bluetooth port is correct in `.env`.
- Call `GET /api/ports` or run `python test_com.py` to find the right port.
- Make sure no other application is holding the serial connection open.

### Voice mode does not work

- Use Chrome.
- Confirm microphone permissions are allowed.
- Make sure `OPENROUTER_API_KEY` is present in `.env`.
- Check the terminal logs for API or JSON parsing errors.

### Camera mode does not track well

- Improve lighting.
- Keep the face centered and reasonably close to the camera.
- Reduce clutter or skin-colored objects in the background.

### Serial commands fail intermittently

- Reconnect the robot Bluetooth/serial link.
- Restart `server.py`.
- Use the emergency stop if the robot becomes unresponsive.

## Known Limitations

- No authentication or multi-user access control
- No packaged installer or service manager
- No automated unit/integration test suite for CI
- Camera tracking uses a simple color heuristic
- Setup is currently optimized for local development rather than polished distribution

## Suggested Next Improvements

- Add screenshots or a short demo GIF to the README
- Add a license file if you plan to share the repo publicly
- Add a `Makefile` or startup script for one-command setup
- Add better platform-specific serial setup notes
- Add structured logging and request error reporting
- Add a real face-detection model for camera mode

