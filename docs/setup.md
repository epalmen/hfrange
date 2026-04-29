# Setup Guide

## 1. Install dependencies

### hamlib (IC-7300 control)
```bash
# Debian/Ubuntu/Raspberry Pi OS
sudo apt install hamlib-utils

# Windows: download from https://hamlib.sourceforge.net/
# Verify IC-7300 (model 3073):
rigctl --list | grep 7300
```

### Python packages
```bash
pip install -r requirements.txt
```

## 2. Connect IC-7300

1. Connect IC-7300 to PC with USB-A to USB-B cable
2. On radio: **Menu → CONNECTORS → USB SEND/MOD → DAT**
3. The IC-7300 creates **two** USB devices:
   - A **virtual serial port** (CI-V control) → used by rigctld
   - A **USB audio codec** → used to play the tone
4. Find the serial port:
   ```bash
   # Linux
   ls /dev/ttyUSB* /dev/ttyACM*
   # Windows: Device Manager → Ports (COM & LPT)
   ```

## 3. Start rigctld (hamlib daemon)

```bash
# Linux
rigctld -m 3073 -r /dev/ttyUSB0 -s 19200 -P RIG &

# Windows (PowerShell)
rigctld.exe -m 3073 -r COM3 -s 19200 -P RIG
```

## 4. Find your audio device

```bash
python src/tone_generator.py --list-devices
# Look for "USB Audio CODEC" or similar — that's your IC-7300
```

## 5. Run the web UI (recommended)

```bash
cd /path/to/hfrange
python src/web_app.py
# Open http://localhost:8000
```

In the UI:
- Select your serial port from the dropdown
- Select the IC-7300 USB audio device
- Check the bands you want to test (40m / 20m / 10m)
- Click **Start scan**

## 6. Run via CLI

```bash
cd src

# Scan all bands:
python tracker.py --config ../config/config.yaml

# One band only:
python tracker.py --band 20m --port COM3

# Custom frequency:
python tracker.py --frequency 14200000 --tone 1000

# Continuous (every 30 min):
python tracker.py --loop --interval 30

# Listen only — no TX:
python tracker.py --no-radio
```

## 7. How tone detection works

On SSB, your IC-7300 transmits the audio as a modulated RF signal.
A 1000 Hz audio tone on USB at 14.200 MHz puts the RF at **14.201 MHz**.

On each KiwiSDR:
- We tune to 14.200 MHz USB
- Capture 12 seconds of audio (12 kHz sample rate)
- Run an FFT and look for a peak at **exactly 1000 Hz ± 50 Hz**
- If the peak is ≥10 dB above the noise floor → **HEARD**

This is far more reliable than RSSI alone because it confirms it's
specifically *your* signal, not a random QRM source on the frequency.

## Outputs

| File | Description |
|---|---|
| `output/map.html` | Interactive coverage map — open in browser |
| `output/scan_TIMESTAMP.json` | Raw results for further analysis |

## Troubleshooting

| Problem | Fix |
|---|---|
| No ports in dropdown | Check USB connection, install FTDI/CP210x driver |
| rigctld not reachable | Start rigctld before the app, check port in config |
| No receivers in range | Try a lower band or increase max_distance_km |
| SNR always 0 | Check audio device — must route to IC-7300, not speakers |
| PTT stuck on | rigctld crash — manually unkey with `rigctl T 0` |
