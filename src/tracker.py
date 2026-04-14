"""
Main signal tracker — orchestrates radio control, KiwiSDR scanning, and reporting.

Usage:
    python tracker.py --config config/config.yaml
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
from kiwisdr_scanner import fetch_receiver_list, sample_rssi, RSSISample
from propagation import estimate_skip_zone_km, best_scan_range, mhz_to_band
from report import save_html_map, save_json_log

console = Console()
log = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_scan(cfg: dict) -> list[RSSISample]:
    """One full scan cycle: fetch receivers, sample RSSI on each, return results."""
    station = cfg["station"]
    scan = cfg["scan"]
    freq_hz = scan["frequency"]
    freq_mhz = freq_hz / 1e6
    mode = scan["mode"]

    now = datetime.now(timezone.utc)
    skip_min, skip_max = estimate_skip_zone_km(freq_mhz, now)
    scan_start, scan_end = best_scan_range(freq_mhz, now)

    # Honour config overrides
    scan_start = max(scan["min_distance_km"], scan_start)
    scan_end = min(scan["max_distance_km"], scan_end)

    band = mhz_to_band(freq_mhz)
    console.rule(f"[bold cyan]HF Range Tracker — {station['callsign']} — {band} ({freq_mhz:.4f} MHz {mode})")
    console.print(
        f"[yellow]Skip zone:[/yellow] ~{skip_min:.0f}–{skip_max:.0f} km  "
        f"[yellow]Scan range:[/yellow] {scan_start:.0f}–{scan_end:.0f} km"
    )

    # ------------------------------------------------------------------ #
    # Fetch and filter receivers
    # ------------------------------------------------------------------ #
    console.print("\n[bold]Fetching KiwiSDR receiver list...[/bold]")
    receivers = fetch_receiver_list(
        my_lat=station["latitude"],
        my_lon=station["longitude"],
        min_km=scan_start,
        max_km=scan_end,
        limit=scan["max_receivers"],
    )

    if not receivers:
        console.print("[red]No receivers found in range. Check your internet connection.[/red]")
        return []

    console.print(f"[green]Found {len(receivers)} receivers to check.[/green]\n")

    # ------------------------------------------------------------------ #
    # Optional: configure IC-7300 to the right frequency/mode
    # ------------------------------------------------------------------ #
    radio_cfg = cfg["radio"]
    radio: Radio | None = None
    try:
        radio = Radio(host=radio_cfg["rigctld_host"], port=radio_cfg["rigctld_port"])
        current = radio.status()
        if current["frequency_hz"] != freq_hz:
            console.print(f"Setting radio → {freq_mhz:.4f} MHz {mode}")
            radio.set_frequency(freq_hz)
            radio.set_mode(mode)
        else:
            console.print(f"Radio already on {freq_mhz:.4f} MHz {mode} ✓")
    except OSError:
        console.print(
            "[yellow]rigctld not reachable — skipping radio control. "
            "Start rigctld manually or check config.[/yellow]"
        )

    # ------------------------------------------------------------------ #
    # Scan each receiver
    # ------------------------------------------------------------------ #
    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Receiver", style="cyan")
    table.add_column("Distance", justify="right")
    table.add_column("RSSI", justify="right")
    table.add_column("SNR", justify="right")
    table.add_column("Heard?", justify="center")

    results: list[RSSISample] = []
    rssi_threshold = cfg["signal"]["rssi_threshold"]

    for idx, receiver in enumerate(receivers, 1):
        console.print(
            f"  [{idx}/{len(receivers)}] Sampling [cyan]{receiver.name}[/cyan] "
            f"({receiver.distance_km:.0f} km)…",
            end=" ",
        )
        sample = sample_rssi(
            receiver,
            freq_hz,
            mode,
            duration_s=scan["sample_duration"],
        )

        if sample is None:
            console.print("[red]timeout/error[/red]")
            table.add_row(str(idx), receiver.name, f"{receiver.distance_km:.0f} km", "—", "—", "[red]✗[/red]")
        else:
            sample.heard = sample.rssi_dbm >= rssi_threshold
            results.append(sample)
            heard_str = "[green]✓ YES[/green]" if sample.heard else "[dim]no[/dim]"
            rssi_color = "green" if sample.heard else "yellow"
            console.print(f"[{rssi_color}]{sample.rssi_dbm:.1f} dBm[/{rssi_color}]")
            table.add_row(
                str(idx),
                receiver.name,
                f"{receiver.distance_km:.0f} km",
                f"[{rssi_color}]{sample.rssi_dbm:.1f} dBm[/{rssi_color}]",
                f"{sample.snr_db:.1f} dB",
                heard_str,
            )

        time.sleep(scan["pause_between"])

    console.print()
    console.print(table)

    heard_count = sum(1 for r in results if r.heard)
    console.print(f"\n[bold]Signal heard on {heard_count}/{len(results)} receivers.[/bold]")

    if radio:
        radio.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="HF Range Tracker")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--loop", action="store_true", help="Repeat scan every --interval minutes")
    parser.add_argument("--interval", type=int, default=30, help="Scan interval in minutes")
    parser.add_argument("--output", default="output", help="Directory for reports")
    parser.add_argument("--start-rigctld", action="store_true", help="Launch rigctld automatically")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.start_rigctld:
        radio_cfg = cfg["radio"]
        start_rigctld(radio_cfg["port"], radio_cfg["baud"], radio_cfg["model"])

    while True:
        results = run_scan(cfg)
        if results:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            save_json_log(results, out_dir / f"scan_{ts}.json")
            save_html_map(results, cfg["station"], out_dir / "map.html")
            console.print(f"\n[dim]Reports saved to {out_dir}/[/dim]")

        if not args.loop:
            break
        console.print(f"\n[dim]Next scan in {args.interval} minutes. Ctrl-C to stop.[/dim]")
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
