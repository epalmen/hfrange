"""
KiwiSDR receiver list fetcher and per-receiver RSSI sampler.

Flow:
  1. Fetch the public KiwiSDR list from sdr.hu (JSON) or scrape rx.kiwisdr.com
  2. Filter receivers by distance (skip zone aware)
  3. For each receiver: connect via WebSocket, tune to our frequency, sample RSSI
"""

import json
import logging
import math
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class KiwiReceiver:
    host: str
    port: int
    name: str
    latitude: float
    longitude: float
    distance_km: float = 0.0
    online: bool = True

@dataclass
class RSSISample:
    receiver: KiwiReceiver
    rssi_dbm: float
    snr_db: float
    timestamp: float = field(default_factory=time.time)
    heard: bool = False

# ---------------------------------------------------------------------------
# Receiver list
# ---------------------------------------------------------------------------

# sdr.hu provides a machine-readable JSON list that includes KiwiSDRs with
# their lat/lon, making distance filtering straightforward.
SDR_HU_URL = "http://sdr.hu/api/v1/sdr/list?type=kiwisdr"

# Fallback: the rx.kiwisdr.com page (HTML, needs parsing)
KIWISDR_PUBLIC_URL = "http://rx.kiwisdr.com/"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points (degrees) in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_receiver_list(
    my_lat: float,
    my_lon: float,
    min_km: float = 300,
    max_km: float = 15000,
    limit: int = 20,
) -> list[KiwiReceiver]:
    """
    Return KiwiSDR receivers sorted by distance, filtered to [min_km, max_km].
    Tries sdr.hu JSON API first, falls back to rx.kiwisdr.com scrape.
    """
    receivers = _fetch_from_sdrhu(my_lat, my_lon)
    if not receivers:
        log.warning("sdr.hu returned nothing, trying rx.kiwisdr.com scrape")
        receivers = _fetch_from_kiwisdr_public(my_lat, my_lon)

    # Distance filter (skip zone + max range)
    filtered = [r for r in receivers if min_km <= r.distance_km <= max_km]
    filtered.sort(key=lambda r: r.distance_km)
    log.info(
        "Found %d receivers in range %.0f–%.0f km (from %d total)",
        len(filtered), min_km, max_km, len(receivers),
    )
    return filtered[:limit]


def _fetch_from_sdrhu(my_lat: float, my_lon: float) -> list[KiwiReceiver]:
    try:
        resp = requests.get(SDR_HU_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("sdr.hu fetch failed: %s", exc)
        return []

    receivers = []
    for entry in data:
        try:
            lat = float(entry.get("gps_lat") or entry.get("lat") or 0)
            lon = float(entry.get("gps_lon") or entry.get("lon") or 0)
            host = entry.get("url", "").replace("http://", "").replace("https://", "").split("/")[0]
            port = int(entry.get("port", 8073))
            name = entry.get("name", host)
            dist = _haversine_km(my_lat, my_lon, lat, lon)
            receivers.append(KiwiReceiver(host=host, port=port, name=name,
                                          latitude=lat, longitude=lon,
                                          distance_km=dist))
        except (KeyError, ValueError, TypeError):
            continue
    return receivers


def _fetch_from_kiwisdr_public(my_lat: float, my_lon: float) -> list[KiwiReceiver]:
    """Minimal HTML scrape of rx.kiwisdr.com as a fallback."""
    import re
    try:
        resp = requests.get(KIWISDR_PUBLIC_URL, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.error("rx.kiwisdr.com fetch failed: %s", exc)
        return []

    # Each receiver is a row; extract host and gps coords from data attributes or text
    pattern = re.compile(
        r'href="http://([^/"]+)(?::(\d+))?/?"[^>]*>([^<]+)<.*?'
        r'(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)',
        re.DOTALL
    )
    receivers = []
    for m in pattern.finditer(resp.text):
        host = m.group(1)
        port = int(m.group(2) or 8073)
        name = m.group(3).strip()
        lat = float(m.group(4))
        lon = float(m.group(5))
        dist = _haversine_km(my_lat, my_lon, lat, lon)
        receivers.append(KiwiReceiver(host=host, port=port, name=name,
                                      latitude=lat, longitude=lon,
                                      distance_km=dist))
    return receivers


# ---------------------------------------------------------------------------
# Per-receiver RSSI sampling via kiwiclient s-meter
# ---------------------------------------------------------------------------

def sample_rssi(
    receiver: KiwiReceiver,
    frequency_hz: int,
    mode: str = "USB",
    duration_s: int = 10,
) -> Optional[RSSISample]:
    """
    Connect to a KiwiSDR, tune to frequency_hz, read the S-meter for
    duration_s seconds and return average RSSI.

    Uses kiwiclient's kiwirecorder via subprocess so we don't need to
    bundle the full kiwiclient source. Falls back to a lightweight
    WebSocket implementation if kiwirecorder is not installed.
    """
    try:
        return _sample_via_kiwirecorder(receiver, frequency_hz, mode, duration_s)
    except FileNotFoundError:
        log.debug("kiwirecorder not found, using built-in WebSocket sampler")
        return _sample_via_websocket(receiver, frequency_hz, mode, duration_s)


def _sample_via_kiwirecorder(
    receiver: KiwiReceiver,
    frequency_hz: int,
    mode: str,
    duration_s: int,
) -> Optional[RSSISample]:
    """Invoke kiwirecorder.py --s-meter and parse its output."""
    import subprocess
    freq_khz = frequency_hz / 1e3
    cmd = [
        "python3", "-m", "kiwiclient.kiwirecorder",
        "-s", receiver.host,
        "-p", str(receiver.port),
        "-f", str(freq_khz),
        "--s-meter", str(duration_s),
        "--tlimit", str(duration_s + 5),
        "--quiet",
    ]
    log.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration_s + 15)

    # kiwirecorder prints lines like:  2024-01-01T12:00:00  -105.3 dBm
    rssi_values = []
    import re
    for line in result.stdout.splitlines():
        m = re.search(r'(-?\d+\.?\d*)\s+dBm', line)
        if m:
            rssi_values.append(float(m.group(1)))

    if not rssi_values:
        log.warning("%s: no RSSI readings", receiver.host)
        return None

    avg_rssi = sum(rssi_values) / len(rssi_values)
    # Simple SNR estimate: peak - average of bottom 20%
    sorted_vals = sorted(rssi_values)
    noise_floor = sum(sorted_vals[:max(1, len(sorted_vals) // 5)]) / max(1, len(sorted_vals) // 5)
    snr = avg_rssi - noise_floor

    return RSSISample(
        receiver=receiver,
        rssi_dbm=round(avg_rssi, 1),
        snr_db=round(snr, 1),
        heard=avg_rssi > -120,
    )


def _sample_via_websocket(
    receiver: KiwiReceiver,
    frequency_hz: int,
    mode: str,
    duration_s: int,
) -> Optional[RSSISample]:
    """
    Lightweight WebSocket sampler that speaks the KiwiSDR audio channel
    protocol and reads the RSSI field embedded in audio frames.
    """
    import websocket
    import struct

    freq_khz = frequency_hz / 1e3
    url = f"ws://{receiver.host}:{receiver.port}/kiwi/{int(time.time())}/SND"

    rssi_values = []
    error: list[Exception] = []
    done = threading.Event()

    def on_open(ws):
        # Authenticate and set frequency/mode
        ws.send(f"SET auth t=kiwi p=")
        ws.send(f"SET mod={mode.lower()} low_cut=-5000 high_cut=5000 freq={freq_khz:.3f}")
        ws.send("SET AR OK in=12000 out=44100")
        ws.send("SET compression=0")
        ws.send("SET ident_user=hfrange_scanner")

    def on_message(ws, message):
        if isinstance(message, bytes) and len(message) > 4:
            tag = message[:3].decode("ascii", errors="ignore")
            if tag == "SND":
                # Bytes 8–9 contain the RSSI as a signed 16-bit int (dBm * 10)
                if len(message) >= 10:
                    rssi_raw = struct.unpack(">h", message[8:10])[0]
                    rssi_values.append(rssi_raw / 10.0)

    def on_error(ws, exc):
        error.append(exc)
        done.set()

    def on_close(ws, *args):
        done.set()

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    thread = threading.Thread(target=ws.run_forever, daemon=True)
    thread.start()
    done.wait(timeout=duration_s + 5)
    ws.close()

    if error:
        log.warning("%s WebSocket error: %s", receiver.host, error[0])
        return None

    if not rssi_values:
        log.warning("%s: no RSSI frames received", receiver.host)
        return None

    avg_rssi = sum(rssi_values) / len(rssi_values)
    sorted_vals = sorted(rssi_values)
    noise_floor = sum(sorted_vals[:max(1, len(sorted_vals) // 5)]) / max(1, len(sorted_vals) // 5)
    snr = avg_rssi - noise_floor

    return RSSISample(
        receiver=receiver,
        rssi_dbm=round(avg_rssi, 1),
        snr_db=round(snr, 1),
        heard=avg_rssi > -120,
    )
