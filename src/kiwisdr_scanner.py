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
    receivers = _fetch_from_kiwisdr_public(my_lat, my_lon)
    print(f"[fetch] rx.kiwisdr.com returned {len(receivers)} receivers", flush=True)

    filtered = [r for r in receivers if min_km <= r.distance_km <= max_km]
    filtered.sort(key=lambda r: r.distance_km)
    print(f"[fetch] {len(filtered)} in {min_km:.0f}–{max_km:.0f} km range", flush=True)
    return filtered[:limit]


def _fetch_from_kiwisdr_public(my_lat: float, my_lon: float) -> list[KiwiReceiver]:
    import re
    try:
        resp = requests.get(KIWISDR_PUBLIC_URL, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("rx.kiwisdr.com fetch failed: %s", exc)
        return []

    # The page embeds receiver data in HTML comment blocks inside each cl-entry div:
    #   <!-- gps=(-34.27, 138.77) -->
    #   <!-- name=My Receiver Name -->
    #   <!-- offline=no -->
    #   <a href='http://host:port' ...>
    href_re   = re.compile(r"href='http://([^/:'\s]+):?(\d+)?'\s*target='_blank'")
    gps_re    = re.compile(r"gps=\((-?\d+\.?\d*),\s*(-?\d+\.?\d*)\)")
    name_re   = re.compile(r"<!-- name=([^\n]+?) -->")
    offline_re = re.compile(r"<!-- offline=(\w+) -->")

    receivers = []
    for entry in re.split(r"<div class='cl-entry[^']*'>", resp.text)[1:]:
        try:
            m_gps = gps_re.search(entry)
            m_href = href_re.search(entry)
            if not m_gps or not m_href:
                continue
            m_offline = offline_re.search(entry)
            if m_offline and m_offline.group(1) == "yes":
                continue
            lat  = float(m_gps.group(1))
            lon  = float(m_gps.group(2))
            host = m_href.group(1)
            port = int(m_href.group(2) or 8073)
            m_name = name_re.search(entry)
            name = m_name.group(1).strip() if m_name else host
            dist = _haversine_km(my_lat, my_lon, lat, lon)
            receivers.append(KiwiReceiver(host=host, port=port, name=name,
                                          latitude=lat, longitude=lon,
                                          distance_km=dist))
        except (ValueError, AttributeError):
            continue
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
