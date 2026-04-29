"""
IC-7300 radio control via rigctld (hamlib daemon).

Usage:
    Start rigctld first:
        rigctld -m 3073 -r /dev/ttyUSB0 -s 19200 &

    Then use this module:
        radio = Radio()
        radio.set_frequency(14_200_000)
        radio.set_mode("USB")
        print(radio.get_frequency())
        radio.close()
"""
from __future__ import annotations

import socket
import time
import logging

log = logging.getLogger(__name__)


class Radio:
    """Talk to hamlib's rigctld daemon over TCP."""

    def __init__(self, host: str = "localhost", port: int = 4532):
        self.host = host
        self.port = port
        self._sock = None
        self._connect()

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def _connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=5)
        self._sock.settimeout(5)
        log.info("Connected to rigctld at %s:%s", self.host, self.port)

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None

    # ------------------------------------------------------------------ #
    # Low-level send/receive
    # ------------------------------------------------------------------ #

    def _send(self, cmd: str) -> str:
        """Send a rigctld command and return the response."""
        payload = (cmd.strip() + "\n").encode()
        self._sock.sendall(payload)
        response = b""
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"RPRT" in response or response.endswith(b"\n"):
                break
        decoded = response.decode().strip()
        log.debug("CMD %r -> %r", cmd, decoded)
        return decoded

    # ------------------------------------------------------------------ #
    # Frequency & mode
    # ------------------------------------------------------------------ #

    def get_frequency(self) -> int:
        """Return current VFO frequency in Hz."""
        resp = self._send("f")
        return int(resp.split()[0])

    def set_frequency(self, freq_hz: int):
        """Set VFO frequency in Hz."""
        resp = self._send(f"F {freq_hz}")
        if not resp.startswith("RPRT 0"):
            raise RuntimeError(f"set_frequency failed: {resp}")
        log.info("Frequency set to %s Hz", freq_hz)

    def get_mode(self) -> tuple[str, int]:
        """Return (mode_string, passband_hz)."""
        resp = self._send("m")
        parts = resp.split()
        return parts[0], int(parts[1])

    def set_mode(self, mode: str, passband: int = 0):
        """
        Set mode (USB, LSB, CW, AM, FM) and passband in Hz.
        passband=0 lets the radio use its default.
        """
        resp = self._send(f"M {mode} {passband}")
        if not resp.startswith("RPRT 0"):
            raise RuntimeError(f"set_mode failed: {resp}")
        log.info("Mode set to %s passband=%s", mode, passband)

    # ------------------------------------------------------------------ #
    # PTT (transmit control)
    # ------------------------------------------------------------------ #

    def ptt_on(self):
        """Key the transmitter."""
        resp = self._send("T 1")
        if not resp.startswith("RPRT 0"):
            raise RuntimeError(f"PTT on failed: {resp}")
        log.info("PTT ON")

    def ptt_off(self):
        """Release the transmitter."""
        resp = self._send("T 0")
        if not resp.startswith("RPRT 0"):
            raise RuntimeError(f"PTT off failed: {resp}")
        log.info("PTT OFF")

    # ------------------------------------------------------------------ #
    # Signal meters
    # ------------------------------------------------------------------ #

    def get_signal_strength(self) -> int:
        """Return S-meter reading (dBm or raw units depending on rig)."""
        resp = self._send("l STRENGTH")
        return int(resp.split()[0])

    def get_swr(self) -> float:
        """Return SWR reading."""
        resp = self._send("l SWR")
        return float(resp.split()[0])

    def get_rf_power(self) -> float:
        """Return RF output power level (0.0 – 1.0 relative)."""
        resp = self._send("l RFPOWER")
        return float(resp.split()[0])

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    def status(self) -> dict:
        freq = self.get_frequency()
        mode, passband = self.get_mode()
        return {
            "frequency_hz": freq,
            "frequency_mhz": round(freq / 1e6, 4),
            "mode": mode,
            "passband_hz": passband,
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def start_rigctld(port: str, baud: int, model: int = 3073) -> None:
    """
    Helper to launch rigctld as a subprocess.
    Call this before creating a Radio() instance.
    """
    import subprocess
    cmd = ["rigctld", "-m", str(model), "-r", port, "-s", str(baud)]
    log.info("Starting rigctld: %s", " ".join(cmd))
    subprocess.Popen(cmd)
    time.sleep(1)  # give the daemon a moment to bind
