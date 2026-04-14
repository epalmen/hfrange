# Setup Guide

## 1. Install system dependencies

### hamlib (for IC-7300 control)
```bash
# Debian/Ubuntu/Raspberry Pi OS
sudo apt install hamlib-utils

# Verify IC-7300 support
rigctl --list | grep 7300
```

### Python packages
```bash
pip install -r requirements.txt
```

### kiwiclient (optional but recommended)
```bash
pip install kiwiclient
# or from source:
git clone https://github.com/jks-prv/kiwiclient
cd kiwiclient && pip install .
```

## 2. Connect and configure the IC-7300

1. Connect IC-7300 to PC via USB cable (standard USB-A to USB-B)
2. On the radio: **Menu → CONNECTORS → USB SEND/MOD** — set to **DAT**
3. Check which serial port appeared:
   ```bash
   ls /dev/ttyUSB* /dev/ttyACM*
   ```
4. Update `config/config.yaml` → `radio.port`

## 3. Start rigctld

```bash
# Replace /dev/ttyUSB0 with your actual port
rigctld -m 373 -r /dev/ttyUSB0 -s 19200 -t 4532 &

# Test it:
rigctl -m 2 get_freq   # model 2 = dummy/network rig talking to rigctld
```

## 4. Edit config

```bash
cp config/config.yaml config/my_config.yaml
# Edit: callsign, latitude, longitude, frequency, mode
```

## 5. Run a scan

```bash
cd src
python tracker.py --config ../config/config.yaml

# Continuous mode (every 30 min):
python tracker.py --config ../config/config.yaml --loop --interval 30

# Auto-start rigctld:
python tracker.py --config ../config/config.yaml --start-rigctld
```

## 6. View results

- **Terminal table**: shown live during scan
- **HTML map**: `output/map.html` — open in browser
- **JSON log**: `output/scan_<timestamp>.json`

## Troubleshooting

| Problem | Fix |
|---|---|
| `rigctld not reachable` | Start rigctld first, check port in config |
| No receivers found | Check internet, try increasing max_distance_km |
| All RSSI = timeout | KiwiSDR is offline or geo-blocked; try another time |
| Skip zone too large | Try a lower band (e.g. 40m at night) |
