"""Download main-line railway tracks in a narrow corridor along each train leg (data/raw/osm/corridor_<leg>.json).

Run with: .venv/bin/python -I build/corridors.py
"""
import json
import pathlib
import sys

from shapely.geometry import LineString

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch import OVERPASS, RAW, get  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUFFER_DEG = 0.07  # about 5-8 km either side of the station-to-station line


def corridor_poly(stations):
    line = LineString([(s[1], s[2]) for s in stations])
    poly = line.buffer(BUFFER_DEG).simplify(0.01)
    return " ".join(f"{lat:.4f} {lon:.4f}" for lon, lat in poly.exterior.coords)


def main():
    legs = json.loads((ROOT / "build" / "legs.json").read_text())
    for lid, leg in legs.items():
        poly = corridor_poly(leg["stations"])
        q = f'''[out:json][timeout:600];
way["railway"="rail"]["usage"~"^(main|branch)$"](poly:"{poly}");
out geom;'''
        get(OVERPASS, RAW / "osm" / f"corridor_{lid}.json", method="POST", data={"data": q})


if __name__ == "__main__":
    main()
