"""Download raw map data: Natural Earth base layers, OSM city detail (Overpass), and geocodes for places.

Everything lands in data/raw/. Run with: .venv/bin/python -I build/fetch.py
"""
import json
import pathlib
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "ee-trip-deck/0.1 (personal trip planning map)"}

NE = ["ne_10m_admin_0_countries", "ne_10m_rivers_lake_centerlines", "ne_10m_lakes"]
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{}.geojson"

# (south, west, north, east): full detail area, and a smaller core that also gets buildings
CITIES = {
    "vienna": {"bbox": (48.160, 16.265, 48.270, 16.430), "core": (48.192, 16.345, 48.222, 16.395)},
    "prague": {"bbox": (50.050, 14.355, 50.115, 14.480), "core": (50.074, 14.395, 50.096, 14.440)},
    "munich": {"bbox": (48.075, 11.460, 48.215, 11.650), "core": (48.128, 11.550, 48.152, 11.600)},
    # day-trip detail maps
    "salzburg": {"bbox": (47.785, 13.015, 47.822, 13.070), "core": (47.793, 13.030, 47.812, 13.055)},
    "lednice": {"bbox": (48.712, 16.725, 48.835, 16.900), "core": (48.797, 16.795, 48.808, 16.812)},
    "neuschwanstein": {"bbox": (47.538, 10.690, 47.585, 10.775), "core": (47.550, 10.725, 47.562, 10.755)},
    "wachau": {"bbox": (48.37, 15.46, 48.425, 15.615), "core": (48.37, 15.46, 48.425, 15.615)},
    "kahlenberg": {"bbox": (48.248, 16.3, 48.3, 16.375), "core": (48.248, 16.3, 48.3, 16.375)},
    "hallstatt": {"bbox": (47.548, 13.625, 47.575, 13.662), "core": (47.548, 13.625, 47.575, 13.662)},
    "karlstejn": {"bbox": (49.922, 14.165, 49.968, 14.205), "core": (49.922, 14.165, 49.968, 14.205)},
    "kutnahora": {"bbox": (49.937, 15.25, 49.968, 15.3), "core": (49.937, 15.25, 49.968, 15.3)},
    "bohemian": {"bbox": (50.85, 14.23, 50.9, 14.335), "core": (50.85, 14.23, 50.9, 14.335)},
    "starnberg": {"bbox": (47.935, 11.285, 48.005, 11.37), "core": (47.935, 11.285, 48.005, 11.37)},
    "partnach": {"bbox": (47.452, 11.08, 47.5, 11.145), "core": (47.452, 11.08, 47.5, 11.145)},
}

OVERPASS = "https://overpass-api.de/api/interpreter"
MIRRORS = ["https://overpass.private.coffee/api/interpreter"]


def bb(b):
    return ",".join(str(v) for v in b)


def base_query(b):
    return f"""[out:json][timeout:240];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|pedestrian)$"]({bb(b)});
  way["waterway"~"^(river|canal)$"]({bb(b)});
  way["natural"="water"]({bb(b)});
  relation["natural"="water"]({bb(b)});
  way["leisure"~"^(park|garden)$"]({bb(b)});
  relation["leisure"="park"]({bb(b)});
  way["landuse"~"^(forest|grass|cemetery|vineyard)$"]({bb(b)});
  way["natural"="wood"]({bb(b)});
  way["railway"="rail"]({bb(b)});
);
out geom;"""


def building_query(b):
    return f'[out:json][timeout:240];(way["building"]({bb(b)}););out geom;'


def get(url, path, **kw):
    if path.exists() and path.stat().st_size > 0:
        print("have", path.name)
        return
    print("fetch", path.name)
    method = kw.pop("method", "GET")
    urls = [url] + (MIRRORS if url == OVERPASS else [])
    for attempt in range(6):
        u = urls[attempt % len(urls)]
        try:
            r = requests.request(method, u, headers=UA, timeout=300, **kw)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            print("   retry after", e.__class__.__name__, "from", u)
            time.sleep(10 + attempt * 10)
    else:
        raise RuntimeError(f"giving up on {path.name}")
    path.write_bytes(r.content)
    print("  ", len(r.content) // 1024, "KB")


def main():
    (RAW / "ne").mkdir(parents=True, exist_ok=True)
    (RAW / "osm").mkdir(parents=True, exist_ok=True)
    for name in NE:
        get(NE_URL.format(name), RAW / "ne" / f"{name}.geojson")

    for city, c in CITIES.items():
        get(OVERPASS, RAW / "osm" / f"{city}_base.json", method="POST", data={"data": base_query(c["bbox"])})
        time.sleep(5)
        get(OVERPASS, RAW / "osm" / f"{city}_buildings.json", method="POST", data={"data": building_query(c["core"])})
        time.sleep(5)

    places = json.loads((ROOT / "build" / "places.json").read_text())
    geo_path = RAW / "geocode.json"
    geo = json.loads(geo_path.read_text()) if geo_path.exists() else {}
    queries = []

    def collect(o):
        if isinstance(o, dict):
            if "q" in o:
                queries.append(o["q"])
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
    collect(places)
    for q in queries:
        if q in geo:
            continue
        r = requests.get("https://nominatim.openstreetmap.org/search", headers=UA,
                         params={"q": q, "format": "jsonv2", "limit": 1}, timeout=60)
        r.raise_for_status()
        res = r.json()
        geo[q] = {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]), "name": res[0]["display_name"]} if res else None
        print("geo", q, "->", (geo[q] or {}).get("name", "NOT FOUND")[:90])
        time.sleep(1.2)
    geo_path.write_text(json.dumps(geo, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
