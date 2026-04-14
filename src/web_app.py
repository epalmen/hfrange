"""
HF Range Tracker — FastAPI web server.

Start with:
    python web_app.py                          # uses config/config.yaml
    python web_app.py --port-override COM5     # override serial port
    python web_app.py --host 0.0.0.0 --port 8080

Then open http://localhost:8000 in your browser.
"""

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import serial.tools.list_ports
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Adjust import path when running from src/
import sys
sys.path.insert(0, str(Path(__file__).parent))

from kiwisdr_scanner import fetch_receiver_list, sample_receiver, ScanResult
from propagation import estimate_skip_zone_km, best_scan_range, mhz_to_band
from radio_control import Radio, start_rigctld
from tone_generator import transmit_tone, list_audio_devices
from report import save_html_map, save_json_log

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="HF Range Tracker", version="1.0")

BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Scan state (one scan at a time)
# ---------------------------------------------------------------------------

class ScanState:
    def __init__(self):
        self.running = False
        self.events: list[dict] = []          # SSE event queue
        self.results: list[ScanResult] = []
        self.lock = threading.Lock()
        self._listeners: list[asyncio.Queue] = []

    def emit(self, event_type: str, data: dict):
        payload = {"type": event_type, "data": data, "ts": time.time()}
        with self.lock:
            self.events.append(payload)
            for q in self._listeners:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self.lock:
            # Replay recent events so a late subscriber sees current state
            for ev in self.events[-50:]:
                q.put_nowait(ev)
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self.lock:
            self._listeners.discard(q) if hasattr(self._listeners, "discard") else None
            try:
                self._listeners.remove(q)
            except ValueError:
                pass

    def reset(self):
        with self.lock:
            self.events.clear()
            self.results.clear()
            self.running = False


state = ScanState()


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    port: str                          # e.g. "COM3" or "/dev/ttyUSB0"
    bands: list[str]                   # e.g. ["40m", "20m"]
    tone_hz: float = 1000.0
    audio_device: str = ""
    no_radio: bool = False             # listen-only (no TX)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/ports")
async def list_ports():
    """List available serial ports (for the COM port dropdown)."""
    ports = [
        {"port": p.device, "description": p.description or p.device}
        for p in serial.tools.list_ports.comports()
    ]
    return {"ports": ports}


@app.get("/api/audio-devices")
async def audio_devices():
    """List audio output devices (to find the IC-7300 USB audio)."""
    try:
        devs = list_audio_devices()
        return {"devices": devs}
    except Exception as exc:
        return {"devices": [], "error": str(exc)}


@app.get("/api/bands")
async def get_bands():
    """Return configured bands from config."""
    cfg = load_config()
    return {"bands": cfg["bands"]}


@app.get("/api/config")
async def get_config():
    """Return non-sensitive config for the UI."""
    cfg = load_config()
    return {
        "callsign": cfg["station"]["callsign"],
        "bands": cfg["bands"],
        "scan": cfg["scan"],
    }


@app.post("/api/scan/start")
async def start_scan(req: ScanRequest):
    """Start a background scan. Use /api/scan/stream (SSE) to follow progress."""
    if state.running:
        raise HTTPException(status_code=409, detail="Scan already running")

    cfg = load_config()
    # Override port
    cfg["radio"]["port"] = req.port

    # Filter requested bands
    bands = [b for b in cfg["bands"] if b["name"] in req.bands]
    if not bands:
        raise HTTPException(status_code=400, detail="No matching bands in config")

    # Override tone
    for b in bands:
        b["tone_hz"] = req.tone_hz

    state.reset()
    state.running = True

    thread = threading.Thread(
        target=_run_scan,
        args=(cfg, bands, req),
        daemon=True,
    )
    thread.start()
    return {"status": "started", "bands": [b["name"] for b in bands]}


@app.post("/api/scan/stop")
async def stop_scan():
    """Signal the running scan to stop after current receiver."""
    state.running = False
    return {"status": "stopping"}


@app.get("/api/scan/stream")
async def scan_stream():
    """SSE endpoint — connect here to receive live scan events."""
    q = state.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(payload)}\n\n"
                    if payload.get("type") == "scan_complete":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            state.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/results")
async def get_results():
    """Return the last scan results as JSON."""
    with state.lock:
        results = list(state.results)
    return {"results": [_result_to_dict(r) for r in results]}


@app.get("/map", response_class=HTMLResponse)
async def get_map():
    """Serve the live coverage map."""
    map_file = OUTPUT_DIR / "map.html"
    if not map_file.exists():
        return HTMLResponse("<p>No map yet — run a scan first.</p>")
    return FileResponse(str(map_file))


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------

def _run_scan(cfg: dict, bands: list[dict], req: ScanRequest):
    """Runs in a background thread. Emits SSE events via state.emit()."""
    station = cfg["station"]
    scan_cfg = cfg["scan"]
    tone_cfg = cfg["tone"]

    # Connect to radio
    radio: Optional[Radio] = None
    if not req.no_radio:
        r = cfg["radio"]
        try:
            radio = Radio(host=r["rigctld_host"], port=r["rigctld_port"])
            state.emit("radio", {"status": "connected"})
        except OSError as exc:
            state.emit("radio", {"status": "disconnected", "error": str(exc)})

    try:
        for band_cfg in bands:
            if not state.running:
                break

            freq_hz = band_cfg["frequency_hz"]
            mode = band_cfg["mode"]
            tone_hz = band_cfg["tone_hz"]
            freq_mhz = freq_hz / 1e6

            now = datetime.now(timezone.utc)
            skip_min, skip_max = estimate_skip_zone_km(freq_mhz, now)
            scan_start, scan_end = best_scan_range(freq_mhz, now)
            scan_start = max(scan_cfg["min_distance_km"], scan_start)
            scan_end = min(scan_cfg["max_distance_km"], scan_end)

            state.emit("band_start", {
                "band": band_cfg["name"],
                "frequency_hz": freq_hz,
                "mode": mode,
                "tone_hz": tone_hz,
                "skip_zone": {"min_km": skip_min, "max_km": skip_max},
                "scan_range": {"min_km": scan_start, "max_km": scan_end},
            })

            # Tune radio
            if radio:
                try:
                    radio.set_frequency(freq_hz)
                    radio.set_mode(mode)
                    state.emit("radio", {"status": "tuned", "freq_hz": freq_hz, "mode": mode})
                except Exception as exc:
                    state.emit("radio", {"status": "tune_error", "error": str(exc)})

            # Fetch receivers
            state.emit("status", {"message": f"Fetching KiwiSDR list for {band_cfg['name']}..."})
            receivers = fetch_receiver_list(
                my_lat=station["latitude"],
                my_lon=station["longitude"],
                min_km=scan_start,
                max_km=scan_end,
                limit=scan_cfg["max_receivers"],
            )

            state.emit("receivers_found", {
                "band": band_cfg["name"],
                "count": len(receivers),
                "receivers": [
                    {"name": r.name, "host": r.host, "lat": r.latitude,
                     "lon": r.longitude, "distance_km": r.distance_km}
                    for r in receivers
                ],
            })

            for idx, receiver in enumerate(receivers):
                if not state.running:
                    break

                state.emit("receiver_start", {
                    "band": band_cfg["name"],
                    "index": idx,
                    "total": len(receivers),
                    "name": receiver.name,
                    "host": receiver.host,
                    "distance_km": receiver.distance_km,
                })

                # TX thread
                tx_thread = None
                if radio:
                    tx_thread = _start_tx_thread(
                        radio, tone_hz,
                        duration_s=tone_cfg["tx_duration_s"],
                        audio_device=req.audio_device or None,
                    )

                result = sample_receiver(
                    receiver=receiver,
                    frequency_hz=freq_hz,
                    tone_hz=tone_hz,
                    mode=mode,
                    duration_s=scan_cfg["sample_duration"],
                    snr_threshold=scan_cfg["tone_detect_snr_db"],
                    progress_cb=lambda msg: state.emit("status", {"message": msg}),
                )

                if tx_thread:
                    tx_thread.join(timeout=tone_cfg["tx_duration_s"] + 5)

                if result:
                    with state.lock:
                        state.results.append(result)
                    state.emit("receiver_result", _result_to_dict(result))
                else:
                    state.emit("receiver_error", {
                        "name": receiver.name,
                        "host": receiver.host,
                        "distance_km": receiver.distance_km,
                    })

                time.sleep(scan_cfg["pause_between"])

            state.emit("band_complete", {"band": band_cfg["name"]})

        # Save reports
        with state.lock:
            all_results = list(state.results)

        if all_results:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            save_json_log(all_results, OUTPUT_DIR / f"scan_{ts}.json")
            save_html_map(all_results, cfg["station"], OUTPUT_DIR / "map.html")

        state.emit("scan_complete", {
            "heard": sum(1 for r in all_results if r.detection.heard),
            "total": len(all_results),
        })

    finally:
        state.running = False
        if radio:
            radio.close()


def _start_tx_thread(radio, tone_hz, duration_s, audio_device):
    def tx():
        try:
            transmit_tone(radio, tone_hz=tone_hz, duration_s=duration_s,
                          audio_device=audio_device)
        except Exception as exc:
            log.warning("TX error: %s", exc)

    t = threading.Thread(target=tx, daemon=True)
    t.start()
    return t


def _result_to_dict(r: ScanResult) -> dict:
    return {
        "receiver": {
            "name": r.receiver.name,
            "host": r.receiver.host,
            "port": r.receiver.port,
            "lat": r.receiver.latitude,
            "lon": r.receiver.longitude,
            "distance_km": round(r.receiver.distance_km, 1),
        },
        "rssi_dbm": r.rssi_dbm,
        "tone_snr_db": r.detection.tone_snr_db,
        "tone_power_db": r.detection.tone_power_db,
        "noise_floor_db": r.detection.noise_floor_db,
        "heard": r.detection.heard,
        "timestamp": datetime.fromtimestamp(r.timestamp, tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
