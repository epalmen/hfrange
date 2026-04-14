"""
Multi-band HF Range Tracker — CLI entry point.

Usage:
    # Scan all configured bands:
    python tracker.py --config ../config/config.yaml

    # Scan a specific band only:
    python tracker.py --band 20m

    # Override frequency directly:
    python tracker.py --frequency 14200000 --tone 1000

    # Override serial port:
    python tracker.py --port COM5

    # Continuous scan loop:
    python tracker.py --loop --interval 30

    # Auto-start rigctld:
    python tracker.py --start-rigctld
"""

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table
from rich import box

from radio_control import Radio, start_rigctld
from kiwisdr_scanner import fetch_receiver_list, sample_receiver, ScanResult
from propagation import estimate_skip_zone_km, best_scan_range, mhz_to_band
from tone_generator import transmit_tone
from report import save_html_map, save_json_log

console = Console()
log = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def scan_band(cfg: dict, band_cfg: dict, radio: Radio | None) -> list[ScanResult]:
    """Run one full scan on a single band. Returns all results."""
    station = cfg["station"]
    scan_cfg = cfg["scan"]
    tone_cfg = cfg["tone"]

    freq_hz: int = band_cfg["frequency_hz"]
    mode: str = band_cfg["mode"]
    tone_hz: float = band_cfg["tone_hz"]
    freq_mhz = freq_hz / 1e6
    band_name = band_cfg["name"]

    now = datetime.now(timezone.utc)
    skip_min, skip_max = estimate_skip_zone_km(freq_mhz, now)
    scan_start, scan_end = best_scan_range(freq_mhz, now)
    scan_start = max(scan_cfg["min_distance_km"], scan_start)
    scan_end = min(scan_cfg["max_distance_km"], scan_end)

    console.rule(
        f"[bold cyan]{station['callsign']} — {band_name} ({freq_mhz:.4f} MHz {mode}) "
        f"| tone {tone_hz:.0f} Hz"
    )
    console.print(
        f"[yellow]Skip zone:[/yellow] ~{skip_min:.0f}–{skip_max:.0f} km  "
        f"[yellow]Scan range:[/yellow] {scan_start:.0f}–{scan_end:.0f} km"
    )

    # Tune radio
    if radio:
        try:
            current = radio.status()
            if current["frequency_hz"] != freq_hz or current["mode"] != mode:
                console.print(f"Tuning radio → {freq_mhz:.4f} MHz {mode}")
                radio.set_frequency(freq_hz)
                radio.set_mode(mode)
            else:
                console.print(f"Radio on {freq_mhz:.4f} MHz {mode} ✓")
        except Exception as exc:
            console.print(f"[yellow]Radio tune failed: {exc}[/yellow]")

    # Fetch receivers
    console.print("\n[bold]Fetching KiwiSDR list...[/bold]")
    receivers = fetch_receiver_list(
        my_lat=station["latitude"],
        my_lon=station["longitude"],
        min_km=scan_start,
        max_km=scan_end,
        limit=scan_cfg["max_receivers"],
    )
    if not receivers:
        console.print("[red]No receivers found in range.[/red]")
        return []
    console.print(f"[green]{len(receivers)} receivers to check.[/green]\n")

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Receiver", style="cyan", min_width=20)
    table.add_column("Dist", justify="right")
    table.add_column("RSSI", justify="right")
    table.add_column("Tone SNR", justify="right")
    table.add_column("Heard?", justify="center")

    results: list[ScanResult] = []

    for idx, receiver in enumerate(receivers, 1):
        console.print(
            f"  [{idx}/{len(receivers)}] [cyan]{receiver.name}[/cyan] "
            f"({receiver.distance_km:.0f} km)  ",
            end="",
        )

        # Transmit tone while sampling
        tx_thread = None
        if radio:
            tx_thread = _start_tx_thread(
                radio,
                tone_hz=tone_hz,
                duration_s=tone_cfg["tx_duration_s"],
                audio_device=tone_cfg.get("audio_device") or None,
            )

        result = sample_receiver(
            receiver=receiver,
            frequency_hz=freq_hz,
            tone_hz=tone_hz,
            mode=mode,
            duration_s=scan_cfg["sample_duration"],
            snr_threshold=scan_cfg["tone_detect_snr_db"],
        )

        if tx_thread:
            tx_thread.join(timeout=tone_cfg["tx_duration_s"] + 5)

        if result is None:
            console.print("[red]timeout/error[/red]")
            table.add_row(str(idx), receiver.name, f"{receiver.distance_km:.0f} km",
                          "—", "—", "[red]✗[/red]")
        else:
            results.append(result)
            det = result.detection
            heard_str = "[bold green]✓ YES[/bold green]" if det.heard else "[dim]no[/dim]"
            rssi_color = "green" if det.heard else "yellow"
            console.print(
                f"[{rssi_color}]{result.rssi_dbm:.1f} dBm[/{rssi_color}]  "
                f"tone SNR {det.tone_snr_db:.1f} dB  {'HEARD' if det.heard else ''}"
            )
            table.add_row(
                str(idx),
                receiver.name,
                f"{receiver.distance_km:.0f} km",
                f"[{rssi_color}]{result.rssi_dbm:.1f} dBm[/{rssi_color}]",
                f"{det.tone_snr_db:.1f} dB",
                heard_str,
            )

        time.sleep(scan_cfg["pause_between"])

    console.print()
    console.print(table)
    heard = sum(1 for r in results if r.detection.heard)
    console.print(f"\n[bold]{band_name}: signal heard on {heard}/{len(results)} receivers.[/bold]\n")
    return results


def _start_tx_thread(radio, tone_hz, duration_s, audio_device):
    """Start tone transmission in a background thread."""
    import threading

    def tx():
        try:
            transmit_tone(radio, tone_hz=tone_hz, duration_s=duration_s,
                          audio_device=audio_device)
        except Exception as exc:
            log.warning("TX thread error: %s", exc)

    t = threading.Thread(target=tx, daemon=True)
    t.start()
    return t


def main():
    parser = argparse.ArgumentParser(description="HF Range Tracker — multi-band signal coverage test")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--band", help="Only scan this band (e.g. 40m, 20m, 10m)")
    parser.add_argument("--frequency", type=int, help="Override frequency in Hz")
    parser.add_argument("--tone", type=float, default=None, help="Override tone frequency in Hz")
    parser.add_argument("--port", help="Override serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--loop", action="store_true", help="Repeat scan continuously")
    parser.add_argument("--interval", type=int, default=30, help="Loop interval in minutes")
    parser.add_argument("--output", default="output", help="Output directory for reports")
    parser.add_argument("--start-rigctld", action="store_true")
    parser.add_argument("--no-radio", action="store_true", help="Skip radio control and TX (listen only)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Config is relative to the src/ dir when run from there, so adjust path
    config_path = args.config
    cfg = load_config(config_path)

    # Apply CLI overrides
    if args.port:
        cfg["radio"]["port"] = args.port

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.start_rigctld and not args.no_radio:
        r = cfg["radio"]
        start_rigctld(r["port"], r["baud"], r["model"])

    # Determine which bands to scan
    bands_to_scan = cfg["bands"]
    if args.band:
        bands_to_scan = [b for b in bands_to_scan if b["name"] == args.band]
        if not bands_to_scan:
            console.print(f"[red]Band '{args.band}' not found in config.[/red]")
            return

    if args.frequency:
        # Single custom frequency
        freq_mhz = args.frequency / 1e6
        bands_to_scan = [{
            "name": mhz_to_band(freq_mhz),
            "frequency_hz": args.frequency,
            "mode": "USB",
            "tone_hz": args.tone or 1000.0,
        }]

    if args.tone:
        for b in bands_to_scan:
            b["tone_hz"] = args.tone

    # Connect to radio
    radio: Radio | None = None
    if not args.no_radio:
        r = cfg["radio"]
        try:
            radio = Radio(host=r["rigctld_host"], port=r["rigctld_port"])
            console.print("[green]Radio connected via rigctld ✓[/green]")
        except OSError:
            console.print(
                "[yellow]rigctld not reachable — running in listen-only mode.[/yellow]\n"
                "[yellow]Start rigctld manually or use --start-rigctld.[/yellow]"
            )

    try:
        while True:
            all_results: list[ScanResult] = []
            for band_cfg in bands_to_scan:
                results = scan_band(cfg, band_cfg, radio)
                all_results.extend(results)

            if all_results:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                save_json_log(all_results, out_dir / f"scan_{ts}.json")
                save_html_map(all_results, cfg["station"], out_dir / "map.html")
                console.print(f"[dim]Reports saved to {out_dir}/[/dim]")

            if not args.loop:
                break
            console.print(f"[dim]Next scan in {args.interval} min. Ctrl-C to stop.[/dim]")
            time.sleep(args.interval * 60)
    finally:
        if radio:
            radio.close()


if __name__ == "__main__":
    main()
