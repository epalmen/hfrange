# HF Range Tracker

Track how far your HF transmission reaches by automatically sampling signal
strength on distributed [KiwiSDR](http://kiwisdr.com/public/) receivers around the world.

## What it does

1. **Controls your IC-7300** via USB (hamlib/rigctld) — sets frequency and mode
2. **Fetches public KiwiSDR receivers** sorted by distance from your location
3. **Skips the skip zone** — HF signals bounce off the ionosphere so you cannot
   be heard within ~300–1000 km; the scanner only checks receivers outside that zone
4. **Samples RSSI** on each receiver and reports whether your signal is heard
5. **Generates an HTML map** showing coverage with green/grey markers

## Quick start

```bash
pip install -r requirements.txt
sudo apt install hamlib-utils          # for rigctld
rigctld -m 3073 -r /dev/ttyUSB0 -s 19200 &
cp config/config.yaml config/my_config.yaml
# edit my_config.yaml: callsign, coordinates, frequency
cd src
python tracker.py --config ../config/my_config.yaml
```

See [docs/setup.md](docs/setup.md) for full instructions.

## Project structure

```
hfrange/
├── config/
│   └── config.yaml          # Your station settings
├── src/
│   ├── tracker.py           # Main entry point
│   ├── radio_control.py     # IC-7300 via rigctld
│   ├── kiwisdr_scanner.py   # Fetch receivers, sample RSSI
│   ├── propagation.py       # Skip zone estimation
│   └── report.py            # HTML map + JSON log
├── docs/
│   └── setup.md
├── output/                  # Generated maps and logs (git-ignored)
└── requirements.txt
```

## Dependencies

- [hamlib](https://hamlib.sourceforge.net/) — radio control
- [kiwiclient](https://github.com/jks-prv/kiwiclient) — KiwiSDR WebSocket client
- [folium](https://python-visualization.github.io/folium/) — interactive maps
- [rich](https://github.com/Textualize/rich) — terminal output
- [geopy](https://geopy.readthedocs.io/) — distance calculations

## Propagation note

HF signals reflect off the ionosphere. There is always a **skip zone** around
the transmitter where the signal cannot be heard via sky-wave. The size depends
on frequency and time of day (roughly 300–1500 km for typical HF bands).
The scanner automatically estimates and skips this zone.

## License

MIT
