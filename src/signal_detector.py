"""
FFT-based tone detector for KiwiSDR audio frames.

When transmitting a 1000 Hz tone on USB, it appears at dial_freq + 1000 Hz
on the spectrum. On a KiwiSDR tuned to dial_freq USB, the tone lands in the
audio at exactly 1000 Hz — easy to detect with a narrow FFT bin check.

Usage:
    detector = ToneDetector(tone_hz=1000, sample_rate=12000)
    detector.add_samples(pcm_int16_array)
    result = detector.evaluate()
    print(result.tone_snr_db, result.heard)
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class DetectionResult:
    tone_hz: float
    tone_power_db: float
    noise_floor_db: float
    tone_snr_db: float
    heard: bool
    n_frames: int


class ToneDetector:
    """
    Accumulates 16-bit PCM audio samples and detects a specific tone via FFT.

    Parameters
    ----------
    tone_hz      : expected audio frequency of our transmitted tone
    sample_rate  : KiwiSDR audio sample rate (default 12000 Hz)
    snr_threshold: minimum SNR in dB to declare the tone "heard"
    bandwidth_hz : half-width of the detection window around the tone
    """

    def __init__(
        self,
        tone_hz: float = 1000.0,
        sample_rate: int = 12000,
        snr_threshold: float = 10.0,
        bandwidth_hz: float = 50.0,
    ):
        self.tone_hz = tone_hz
        self.sample_rate = sample_rate
        self.snr_threshold = snr_threshold
        self.bandwidth_hz = bandwidth_hz
        self._buffer: list[np.ndarray] = []
        self._n_frames = 0

    def reset(self):
        self._buffer.clear()
        self._n_frames = 0

    def add_samples(self, pcm: bytes | np.ndarray):
        """
        Accept raw 16-bit signed PCM bytes or an int16 numpy array.
        KiwiSDR sends little-endian 16-bit mono PCM at 12 kHz.
        """
        if isinstance(pcm, (bytes, bytearray)):
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        else:
            samples = pcm.astype(np.float32) / 32768.0 if pcm.dtype != np.float32 else pcm
        self._buffer.append(samples)
        self._n_frames += 1

    @property
    def total_samples(self) -> int:
        return sum(len(b) for b in self._buffer)

    def evaluate(self) -> DetectionResult:
        """Run FFT on accumulated samples and check for tone."""
        if not self._buffer:
            return DetectionResult(self.tone_hz, -200, -200, 0, False, 0)

        audio = np.concatenate(self._buffer)

        # Use a Hann window to reduce spectral leakage
        window = np.hanning(len(audio))
        spectrum = np.abs(np.fft.rfft(audio * window))
        freqs = np.fft.rfftfreq(len(audio), 1.0 / self.sample_rate)

        # Tone bin: narrow window around our expected tone
        tone_mask = (
            (freqs >= self.tone_hz - self.bandwidth_hz) &
            (freqs <= self.tone_hz + self.bandwidth_hz)
        )

        # Noise: everything from 200 Hz to 5 kHz, excluding the tone window
        noise_mask = (
            (freqs >= 200) &
            (freqs <= 5000) &
            ~tone_mask
        )

        eps = 1e-10  # avoid log(0)
        tone_power_db = 20 * np.log10(np.max(spectrum[tone_mask]) + eps)
        noise_floor_db = 20 * np.log10(np.percentile(spectrum[noise_mask], 50) + eps)
        snr = tone_power_db - noise_floor_db

        return DetectionResult(
            tone_hz=self.tone_hz,
            tone_power_db=round(float(tone_power_db), 1),
            noise_floor_db=round(float(noise_floor_db), 1),
            tone_snr_db=round(float(snr), 1),
            heard=snr >= self.snr_threshold,
            n_frames=self._n_frames,
        )
