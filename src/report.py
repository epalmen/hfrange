"""
Output formatters: JSON log and interactive HTML map (via folium).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiwisdr_scanner import RSSISample


def save_json_log(results: list["RSSISample"], path: Path) -> None:
    """Write scan results as JSON for later analysis."""
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
            "snr_db": r.snr_db,
            "heard": r.heard,
        })
    path.write_text(json.dumps(records, indent=2))


def save_html_map(
    results: list["RSSISample"],
    station: dict,
    path: Path,
) -> None:
    """
    Generate an interactive HTML map with:
    - Your transmitter location (star marker)
    - Each KiwiSDR receiver (circle, green=heard, grey=not heard)
    - Popup with RSSI / SNR on click
    """
    try:
        import folium
    except ImportError:
        print("folium not installed — skipping HTML map. pip install folium")
        return

    my_lat = station["latitude"]
    my_lon = station["longitude"]
    callsign = station.get("callsign", "TX")

    m = folium.Map(location=[my_lat, my_lon], zoom_start=4, tiles="CartoDB positron")

    # Transmitter marker
    folium.Marker(
        location=[my_lat, my_lon],
        tooltip=f"{callsign} (you)",
        icon=folium.Icon(color="red", icon="star", prefix="fa"),
    ).add_to(m)

    for r in results:
        color = "green" if r.heard else "gray"
        popup_html = (
            f"<b>{r.receiver.name}</b><br>"
            f"Distance: {r.receiver.distance_km:.0f} km<br>"
            f"RSSI: {r.rssi_dbm:.1f} dBm<br>"
            f"SNR: {r.snr_db:.1f} dB<br>"
            f"Heard: {'YES ✓' if r.heard else 'no'}"
        )
        folium.CircleMarker(
            location=[r.receiver.latitude, r.receiver.longitude],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=r.receiver.name,
        ).add_to(m)

        # Draw a line from TX to receiver if heard
        if r.heard:
            folium.PolyLine(
                locations=[[my_lat, my_lon], [r.receiver.latitude, r.receiver.longitude]],
                color="green",
                weight=1.5,
                opacity=0.5,
            ).add_to(m)

    path.write_text(m._repr_html_())
    print(f"Map saved: {path}")
