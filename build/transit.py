"""Subway, tram and bus lines for the three main cities, from OSM route relations.

Writes deck/data/transit_<city>.json: per mode, the line geometries in integer metres relative to
the city origin (same encoding as the city map layers), plus line refs and colours for subways.
Run with: .venv/bin/python -I build/transit.py
"""
import json
import math
import pathlib
import sys

from shapely.geometry import LineString, box
from shapely.ops import transform

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch import CITIES, OVERPASS, RAW, get  # noqa: E402
from process import CITY_BOXES, flat_delta, iter_parts, merc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = ["vienna", "prague", "munich"]
MODES = {"subway": "subway", "light_rail": "subway", "tram": "tram", "bus": "bus", "trolleybus": "bus"}
DEFAULT_COLOR = {"subway": "#3b6fd1", "tram": "#d0453b", "bus": "#8a63c8"}


def query(b):
    bb = ",".join(map(str, b))
    return f'[out:json][timeout:300];relation["type"="route"]["route"~"^(subway|light_rail|tram|bus|trolleybus)$"]({bb});out geom;'


def main():
    for city in MAIN:
        raw = RAW / "osm" / f"{city}_transit.json"
        get(OVERPASS, raw, method="POST", data={"data": query(CITIES[city]["bbox"])})
        full, _ = CITY_BOXES[city]
        cx, cy = merc((full[0] + full[2]) / 2, (full[1] + full[3]) / 2)

        def proj(lon, lat):
            x, y = merc(lon, lat)
            return (x - cx) * 1000, (y - cy) * 1000

        clip = transform(proj, box(*full))
        out = {"subway": [], "tram": [], "bus": []}
        seen = {"tram": set(), "bus": set()}  # way ids already drawn (many lines share streets)
        subway = {}
        for el in json.loads(raw.read_text())["elements"]:
            t = el.get("tags", {})
            mode = MODES.get(t.get("route"))
            if not mode:
                continue
            ref = t.get("ref") or t.get("name", "")
            color = t.get("colour") or DEFAULT_COLOR[mode]
            for m in el.get("members", []):
                if m.get("type") != "way" or not m.get("geometry") or m.get("role") in ("platform", "stop"):
                    continue
                if mode != "subway":
                    if m["ref"] in seen[mode]:
                        continue
                    seen[mode].add(m["ref"])
                pts = [proj(n["lon"], n["lat"]) for n in m["geometry"] if n]
                if len(pts) < 2:
                    continue
                g = LineString(pts).intersection(clip).simplify(3.0)
                for part in iter_parts(g):
                    if isinstance(part, LineString) and part.length > 5:
                        if mode == "subway":
                            line = subway.setdefault(ref, {"ref": ref, "color": color, "ways": set(), "paths": []})
                            if m["ref"] in line["ways"]:
                                continue
                            line["ways"].add(m["ref"])
                            line["paths"].append(flat_delta(part.coords))
                        else:
                            out[mode].append(flat_delta(part.coords))
        out["subway"] = [{"ref": l["ref"], "color": l["color"], "paths": l["paths"]} for l in sorted(subway.values(), key=lambda l: l["ref"])]
        path = ROOT / "deck" / "data" / f"transit_{city}.json"
        path.write_text(json.dumps(out, separators=(",", ":")))
        print(f"{city}: {len(out['subway'])} subway lines ({', '.join(l['ref'] for l in out['subway'])}), "
              f"{len(out['tram'])} tram segments, {len(out['bus'])} bus segments, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
