"""
KiwiSDR receiver list fetcher and per-receiver tone detector.

Flow:
  1. Fetch public KiwiSDR list (sdr.hu JSON or rx.kiwisdr.com scrape)
  2. Filter receivers by distance (skip zone aware)
  3. For each receiver:
       - Connect via WebSocket
       - Tune to our frequency / mode
       - Accumulate audio frames
       - Run FFT to detect our specific tone
"""
from __future__ import annotations

import logging
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import requests

from signal_detector import ToneDetector, DetectionResult

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


@dataclass
class ScanResult:
    receiver: KiwiReceiver
    detection: DetectionResult
    rssi_dbm: float
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Receiver list
# ---------------------------------------------------------------------------

SDR_HU_URL = "http://sdr.hu/api/v1/sdr/list?type=kiwisdr"
KIWISDR_PUBLIC_URL = "http://rx.kiwisdr.com/"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
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
    receivers = _fetch_from_sdrhu(my_lat, my_lon)
    if not receivers:
        log.warning("sdr.hu empty, trying rx.kiwisdr.com")
        receivers = _fetch_from_kiwisdr_public(my_lat, my_lon)

    filtered = [r for r in receivers if min_km <= r.distance_km <= max_km]
    filtered.sort(key=lambda r: r.distance_km)
    log.info("%d receivers in %.0f–%.0f km range (from %d total)", len(filtered), min_km, max_km, len(receivers))
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
            raw_host = entry.get("url", "")
            host = raw_host.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
            port = int(entry.get("port", 8073))
            name = entry.get("name", host)
            dist = _haversine_km(my_lat, my_lon, lat, lon)
            if host:
                receivers.append(KiwiReceiver(host=host, port=port, name=name,
                                              latitude=lat, longitude=lon, distance_km=dist))
        except (KeyError, ValueError, TypeError):
            continue
    return receivers


def _fetch_from_kiwisdr_public(my_lat: float, my_lon: float) -> list[KiwiReceiver]:
    import re
    try:
        resp = requests.get(KIWISDR_PUBLIC_URL, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        log.error("rx.kiwisdr.com fetch failed: %s", exc)
        return []

    pattern = re.compile(
        r'href="http://([^/"]+)(?::(\d+))?/?"[^>]*>([^<]+)<.*?'
        r'(-?\d+\.\d+)[,\s]+(-?\d+\.\d+)',
        re.DOTALL,
    )
    receivers = []
    for m in pattern.finditer(resp.text):
        host, port_str, name = m.group(1), m.group(2), m.group(3).strip()
        lat, lon = float(m.group(4)), float(m.group(5))
        dist = _haversine_km(my_lat, my_lon, lat, lon)
        receivers.append(KiwiReceiver(host=host, port=int(port_str or 8073),
                                      name=name, latitude=lat, longitude=lon,
                                      distance_km=dist))
    return receivers


# ---------------------------------------------------------------------------
# KiwiSDR WebSocket audio sampler + tone detector
# ---------------------------------------------------------------------------

# KiwiSDR audio frame format (binary):
#   Bytes 0-2  : "SND"
#   Byte  3    : flags
#   Bytes 4-7  : sequence number (uint32 big-endian)
#   Bytes 8-9  : RSSI (int16 big-endian, dBm × 10)
#   Bytes 10-11: GPS nanoseconds (int16 big-endian, unused here)
#   Bytes 12+  : 16-bit signed PCM audio, little-endian, mono, 12000 Hz

KIWI_AUDIO_SAMPLE_RATE = 12000
KIWI_FRAME_HEADER = 12  # bytes before audio data


def sample_receiver(
    receiver: KiwiReceiver,
    frequency_hz: int,
    tone_hz: float,
    mode: str = "USB",
    duration_s: int = 12,
    snr_threshold: float = 10.0,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Optional[ScanResult]:
    """
    Connect to a KiwiSDR receiver, tune to frequency_hz, capture audio for
    duration_s seconds, and detect a tone at tone_hz using FFT.

    Returns ScanResult or None on connection failure.
    """
    try:
        import websocket
    except ImportError:
        log.error("websocket-client not installed: pip install websocket-client")
        return None

    freq_khz = frequency_hz / 1e3
    url = f"ws://{receiver.host}:{receiver.port}/kiwi/{int(time.time())}/SND"

    detector = ToneDetector(
        tone_hz=tone_hz,
        sample_rate=KIWI_AUDIO_SAMPLE_RATE,
        snr_threshold=snr_threshold,
    )
    rssi_samples: list[float] = []
    error: list[str] = []
    done = threading.Event()
    start_time = [0.0]

    def on_open(ws):
        start_time[0] = time.time()
        ws.send("SET auth t=kiwi p=")
        ws.send(f"SET mod={mode.lower()} low_cut=-5000 high_cut=5000 freq={freq_khz:.3f}")
        ws.send("SET AR OK in=12000 out=44100")
        ws.send("SET compression=0")
        ws.send("SET ident_user=hfrange_pd1lvh")
        if progress_cb:
            progress_cb(f"Connected to {receiver.host}")

    def on_message(ws, message):
        if not isinstance(message, (bytes, bytearray)):
            return
        if len(message) <= KIWI_FRAME_HEADER:
            return
        tag = message[:3]
        if tag != b"SND":
            return

        # RSSI
        rssi_raw = struct.unpack(">h", message[8:10])[0]
        rssi_samples.append(rssi_raw / 10.0)

        # Audio samples
        audio_bytes = message[KIWI_FRAME_HEADER:]
        detector.add_samples(audio_bytes)

        # Stop after requested duration
        if time.time() - start_time[0] >= duration_s:
            ws.close()

    def on_error(ws, exc):
        error.append(str(exc))
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
    thread = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 0}, daemon=True)
    thread.start()
    done.wait(timeout=duration_s + 15)
    ws.close()
    thread.join(timeout=3)

    if error and not rssi_samples:
        log.warning("%s: %s", receiver.host, error[0])
        return None

    if not rssi_samples:
        log.warning("%s: no audio frames received", receiver.host)
        return None

    avg_rssi = sum(rssi_samples) / len(rssi_samples)
    detection = detector.evaluate()

    return ScanResult(
        receiver=receiver,
        detection=detection,
        rssi_dbm=round(avg_rssi, 1),
    )
