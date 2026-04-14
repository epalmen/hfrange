"""
Output formatters: JSON log and interactive HTML map (via folium).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiwisdr_scanner import ScanResult


def save_json_log(results: list["ScanResult"], path: Path) -> None:
    records = []
    for r in results:
        records.append({
            "timestamp": datetime.fromtimestamp(r.timestamp, tz=timezone.utc).isoformat(),
            "receiver": {
                "name": r.receiver.name,
                "host": r.receiver.host,
                "port": r.receiver.port,
                "lat": r.receiver.latitude,
                "lon": r.receiver.longitude,
                "distance_km": r.receiver.distance_km,
            },
            "rssi_dbm": r.rssi_dbm,
            "tone_snr_db": r.detection.tone_snr_db,
            "tone_power_db": r.detection.tone_power_db,
            "noise_floor_db": r.detection.noise_floor_db,
            "heard": r.detection.heard,
        })
    path.write_text(json.dumps(records, indent=2))


def save_html_map(
    results: list["ScanResult"],
    station: dict,
    path: Path,
) -> None:
    try:
        import folium
    except ImportError:
        print("folium not installed — skipping HTML map (pip install folium)")
        return

    my_lat = station["latitude"]
    my_lon = station["longitude"]
    callsign = station.get("callsign", "TX")

    m = folium.Map(location=[my_lat, my_lon], zoom_start=4, tiles="CartoDB dark_matter")

    folium.Marker(
        location=[my_lat, my_lon],
        tooltip=f"{callsign} (TX)",
        icon=folium.Icon(color="red", icon="star", prefix="fa"),
    ).add_to(m)

    for r in results:
        heard = r.detection.heard
        color = "green" if heard else "gray"
        popup_html = (
            f"<b>{r.receiver.name}</b><br>"
            f"Distance: {r.receiver.distance_km:.0f} km<br>"
            f"RSSI: {r.rssi_dbm:.1f} dBm<br>"
            f"Tone SNR: {r.detection.tone_snr_db:.1f} dB<br>"
            f"<b>{'✓ HEARD' if heard else '✗ Not heard'}</b>"
        )
        folium.CircleMarker(
            location=[r.receiver.latitude, r.receiver.longitude],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            popup=folium.Popup(popup_html, max_width=240),
            tooltip=r.receiver.name,
        ).add_to(m)

        if heard:
            folium.PolyLine(
                locations=[[my_lat, my_lon], [r.receiver.latitude, r.receiver.longitude]],
                color="green",
                weight=1.5,
                opacity=0.5,
            ).add_to(m)

    path.write_text(m._repr_html_())
