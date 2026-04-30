"""
Transmit a test tone through the IC-7300.

Flow:
  1. Connect to rigctld → enable PTT (TX)
  2. Play a sine-wave tone to the IC-7300 USB audio output
  3. After duration: stop tone, disable PTT (RX)

The IC-7300 appears as a USB audio device ("USB Audio CODEC" on Windows).
Use --audio-device to select it, or leave empty to print available devices.

Standalone usage:
    python tone_generator.py --port COM3 --duration 10 --tone 1000
"""
from __future__ import annotations

import argparse
import logging
import math
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio device helpers
# ---------------------------------------------------------------------------

def list_audio_devices() -> list[dict]:
    """Return all audio output devices."""
    import sounddevice as sd
    sd._terminate()
    sd._initialize()
    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    return [
        {
            "index": i,
            "name": f"{d['name']} [{hostapis[d['hostapi']]['name']}]",
            "outputs": d["max_output_channels"],
        }
        for i, d in enumerate(devices)
        if d["max_output_channels"] > 0
    ]


def find_device_index(name_fragment: str) -> Optional[int]:
    """Find an output device whose name contains name_fragment (case-insensitive)."""
    for dev in list_audio_devices():
        if name_fragment.lower() in dev["name"].lower():
            return dev["index"]
    return None


# ---------------------------------------------------------------------------
# Tone generation
# ---------------------------------------------------------------------------

class TonePlayer:
    """
    Plays a continuous sine-wave tone to a specific audio output device.
    Designed to feed into the IC-7300's USB audio input on SSB.

    Parameters
    ----------
    tone_hz     : audio frequency in Hz (default 1000)
    sample_rate : output sample rate in Hz (default 48000, IC-7300 USB accepts this)
    amplitude   : output level 0.0–1.0 (keep below 1.0 to avoid clipping)
    device      : sounddevice device name substring or None for system default
    """

    def __init__(
        self,
        tone_hz: float = 1000.0,
        sample_rate: int = 48000,
        amplitude: float = 0.7,
        device: Optional[str] = None,
    ):
        self.tone_hz = tone_hz
        self.sample_rate = sample_rate
        self.amplitude = amplitude
        self._device_name = device
        self._stream = None
        self._phase = 0.0
        self._playing = False

    def _find_device(self) -> Optional[int]:
        if not self._device_name:
            return None  # sounddevice uses system default
        idx = find_device_index(self._device_name)
        if idx is None:
            log.warning("Audio device %r not found — using system default", self._device_name)
        return idx

    def start(self):
        """Begin streaming the tone."""
        import sounddevice as sd

        device_idx = self._find_device()
        self._playing = True

        def callback(outdata, frames, time_info, status):
            if status:
                log.debug("Audio callback status: %s", status)
            t = (self._phase + np.arange(frames)) / self.sample_rate
            self._phase = (self._phase + frames) % self.sample_rate
            tone = (self.amplitude * np.sin(2 * math.pi * self.tone_hz * t)).astype(np.float32)
            outdata[:, 0] = tone
            if outdata.shape[1] > 1:
                outdata[:, 1] = tone  # stereo: copy to both channels

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=2,
            dtype="float32",
            device=device_idx,
            callback=callback,
        )
        self._stream.start()
        log.info("Tone %.0f Hz started (device=%s)", self.tone_hz, device_idx)

    def stop(self):
        """Stop the tone."""
        self._playing = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        log.info("Tone stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# ---------------------------------------------------------------------------
# High-level: transmit tone for a fixed duration
# ---------------------------------------------------------------------------

def transmit_tone(
    radio,                  # radio_control.Radio instance (or None for audio-only)
    tone_hz: float = 1000.0,
    duration_s: float = 10.0,
    audio_device: Optional[str] = None,
    amplitude: float = 0.7,
) -> bool:
    """
    Key the IC-7300, play a tone for duration_s seconds, then unkey.

    Returns True on success, False if PTT control failed (audio still played).
    """
    player = TonePlayer(tone_hz=tone_hz, amplitude=amplitude, device=audio_device)
    ptt_ok = True

    if radio:
        try:
            radio.ptt_on()
            log.info("PTT ON")
        except Exception as exc:
            log.warning("PTT control failed: %s — continuing audio only", exc)
            ptt_ok = False

    try:
        with player:
            log.info("Transmitting %.0f Hz tone for %.1f s", tone_hz, duration_s)
            time.sleep(duration_s)
    finally:
        if radio and ptt_ok:
            try:
                radio.ptt_off()
                log.info("PTT OFF")
            except Exception as exc:
                log.error("PTT OFF failed: %s — MANUAL PTT RELEASE REQUIRED", exc)

    return ptt_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Transmit a test tone via IC-7300")
    parser.add_argument("--port", default="COM3", help="IC-7300 serial port")
    parser.add_argument("--baud", type=int, default=19200)
    parser.add_argument("--tone", type=float, default=1000.0, help="Tone frequency in Hz")
    parser.add_argument("--duration", type=float, default=10.0, help="TX duration in seconds")
    parser.add_argument("--audio-device", default="", help="Audio device name substring")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--no-ptt", action="store_true", help="Audio only, no PTT control")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.list_devices:
        for dev in list_audio_devices():
            print(f"  [{dev['index']}] {dev['name']}  ({dev['outputs']} out)")
        return

    from radio_control import Radio, start_rigctld
    radio = None
    if not args.no_ptt:
        try:
            radio = Radio()
        except OSError:
            print("rigctld not reachable — audio only (no PTT)")

    transmit_tone(
        radio=radio,
        tone_hz=args.tone,
        duration_s=args.duration,
        audio_device=args.audio_device or None,
    )
    if radio:
        radio.close()


if __name__ == "__main__":
    main()
