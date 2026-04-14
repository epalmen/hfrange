# Project Context — HF Range Tracker

> This file is for Claude (and humans) starting a new session.
> It captures decisions, design choices, and current state so nothing needs to be re-explained.

## What this project does

Automatically measures how far the HF signal from **PD1LVH** (Amsterdam) reaches,
by transmitting a test tone from an Icom IC-7300 and listening on public
[KiwiSDR](http://rx.kiwisdr.com) receivers at varying distances around the world.
Results are shown on an interactive map and saved as JSON.

## Station facts

| Key | Value |
|---|---|
| Callsign | PD1LVH |
| Location | Amsterdam, Netherlands |
| Grid | JO22 |
| Coordinates | 52.3676 N, 4.9041 E |
| Radio | Icom IC-7300 |
| Max power | 100 W (recommended: **25 W** for testing) |
| Bands | 40m (7.090 MHz), 20m (14.300 MHz), 10m (28.500 MHz) |
| Mode | USB (upper sideband) |
| Test signal | 1000 Hz sine tone via USB audio → IC-7300 |
| OS | Windows (primary), cross-platform Python |

## Key design decisions (and why)

### 1. Tone detection instead of RSSI
Decided to detect a specific **1000 Hz tone with FFT** rather than just measuring
raw RSSI. Reason: RSSI alone cannot confirm it's *our* signal — any QRM on the
frequency would trigger a false positive. The FFT finds a sharp peak at exactly
1000 ± 50 Hz; if SNR ≥ 10 dB it's our tone. Much more reliable.

### 2. 1000 Hz tone on USB SSB
On USB at 14.200 MHz, a 1000 Hz audio tone produces RF at 14.201 MHz — a single
narrow carrier. KiwiSDR receives this as audio at 1000 Hz. This is the standard
"single-tone SSB test" method (IEC 60268).

### 3. hamlib/rigctld for radio control
Used hamlib daemon (`rigctld`) rather than direct serial CI-V because:
- Language-agnostic (TCP socket, simple text protocol)
- Works on Windows, Linux, Mac without recompiling
- IC-7300 is hamlib model **373**, CI-V baud **19200**, USB serial port

### 4. Audio via sounddevice → USB audio codec
IC-7300 presents as two USB devices: a serial port (CI-V) and a USB audio codec.
`tone_generator.py` uses `sounddevice` to stream tone to the audio codec.
Menu setting required: **SET → Connectors → USB SEND/MOD = Data**

### 5. KiwiSDR WebSocket audio protocol
Direct WebSocket connection to each KiwiSDR (no kiwiclient dependency needed).
Binary frame format: 3-byte "SND" tag + 9-byte header + 16-bit PCM at 12000 Hz.
RSSI is in bytes 8–9 (int16 big-endian, dBm × 10). Audio starts at byte 12.

### 6. Skip zone filtering
HF signals skip over a dead zone around the TX. The scanner estimates skip zone
from frequency + time of day and only checks receivers *outside* the skip zone.
Minimum configurable distance: 300 km (config: `scan.min_distance_km`).

### 7. Web UI + CLI
Both interfaces available:
- `python src/web_app.py` → FastAPI + Leaflet.js at http://localhost:8000
- `python src/tracker.py` → rich terminal output
Web UI uses Server-Sent Events (SSE) for live scan progress.

## File structure

```
hfrange/
├── config/
│   └── config.yaml          ← edit callsign, port, bands here
├── src/
│   ├── web_app.py           ← FastAPI server (main entry point for web UI)
│   ├── tracker.py           ← CLI entry point
│   ├── radio_control.py     ← IC-7300 via rigctld TCP
│   ├── tone_generator.py    ← 1000 Hz tone via sounddevice + PTT
│   ├── kiwisdr_scanner.py   ← WebSocket audio capture from KiwiSDRs
│   ├── signal_detector.py   ← FFT tone detection (ToneDetector class)
│   ├── propagation.py       ← skip zone estimation
│   └── report.py            ← HTML map (folium) + JSON log
├── static/
│   ├── index.html           ← web frontend
│   ├── app.js               ← Leaflet map, SSE, scan control
│   └── style.css            ← dark theme UI
├── docs/
│   ├── how-it-works.html    ← technical guide (open in Chrome, print to PDF)
│   ├── generate_pdf.py      ← PDF generator (playwright/chrome headless)
│   └── setup.md             ← installation guide
├── output/                  ← generated maps + JSON logs (git-ignored)
├── requirements.txt
├── CONTEXT.md               ← this file
└── README.md
```

## Dependencies

```
fastapi       uvicorn[standard]   pyyaml       websocket-client
numpy         folium              requests     pyserial
rich          sounddevice
```

External system tools:
- `rigctld` (part of hamlib) — must be running before the app
- Hamlib model 373 = IC-7300, baud 19200

## How to start (Windows)

```powershell
# 1. Start rigctld (find port in Device Manager)
rigctld.exe -m 373 -r COM3 -s 19200

# 2. Start web app
python src/web_app.py

# 3. Open browser
start http://localhost:8000
```

## Things not yet built / possible next steps

- [ ] Scheduled scans (cron-style) with email/notification when DX path opens
- [ ] Compare two scan results to see propagation change
- [ ] Live waterfall view from a selected KiwiSDR
- [ ] WSPR beacon mode instead of tone (uses established WSPR network for reporting)
- [ ] SWR / power meter display in web UI (rigctld `l SWR` and `l RFPOWER`)
- [ ] Multiple simultaneous KiwiSDR connections (parallel scanning)
- [ ] Dark/light map theme toggle

## GitHub

https://github.com/epalmen/hfrange
