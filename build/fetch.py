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
    # day-trip detail maps
    "lednice": {"bbox": (48.712, 16.725, 48.835, 16.900), "core": (48.797, 16.795, 48.808, 16.812)},
    "wachau": {"bbox": (48.37, 15.46, 48.425, 15.615), "core": (48.37, 15.46, 48.425, 15.615)},
    "kahlenberg": {"bbox": (48.248, 16.3, 48.3, 16.375), "core": (48.248, 16.3, 48.3, 16.375)},
    "hallstatt": {"bbox": (47.535, 13.605, 47.592, 13.705), "core": (47.535, 13.605, 47.592, 13.705)},
    "karlstejn": {"bbox": (49.922, 14.165, 49.968, 14.205), "core": (49.922, 14.165, 49.968, 14.205)},
    "kutnahora": {"bbox": (49.937, 15.25, 49.968, 15.3), "core": (49.937, 15.25, 49.968, 15.3)},
    "bohemian": {"bbox": (50.85, 14.23, 50.9, 14.335), "core": (50.85, 14.23, 50.9, 14.335)},
    "split": {"bbox": (43.485, 16.39, 43.565, 16.56),  # wide enough for the Airbnbs (Kaštel Sućurac to Podstrana)
                "core": (43.502, 16.432, 43.513, 16.448)},
    "trogir": {"bbox": (43.508, 16.232, 43.527, 16.268), "core": (43.514, 16.244, 43.52, 16.256)},
    "hvar": {"bbox": (43.15, 16.365, 43.185, 16.46), "core": (43.168, 16.435, 43.176, 16.448)},
    "krka": {"bbox": (43.79, 15.895, 43.875, 15.99), "core": (43.812, 15.918, 43.82, 15.93)},
    "zagreb": {"bbox": (45.785, 15.935, 45.865, 16.01),  # the centre and north to the Sljeme gondola
                 "core": (45.805, 15.965, 45.818, 15.985)},
}

COASTAL = ["split", "trogir", "hvar"]
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


# day trips are mostly countryside: forests mapped as multipolygons, and the trails, funiculars and
# cable cars that reach the sights (city maps leave these out to stay small)
TRAIL_MAPS = ["lednice", "wachau", "kahlenberg", "hallstatt", "karlstejn", "kutnahora", "bohemian"]


def trail_query(b):
    return f"""[out:json][timeout:240];
(
  relation["landuse"="forest"]({bb(b)});
  relation["natural"="wood"]({bb(b)});
  way["highway"~"^(path|footway|track|steps|bridleway)$"]({bb(b)});
  way["railway"="funicular"]({bb(b)});
  way["aerialway"~"^(cable_car|gondola|chair_lift)$"]({bb(b)});
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
        if city in TRAIL_MAPS:
            get(OVERPASS, RAW / "osm" / f"{city}_trails.json", method="POST", data={"data": trail_query(c["bbox"])})
            time.sleep(5)

    for city in COASTAL:  # the sea is mapped as coastline lines, not water areas
        q = f'[out:json][timeout:240];way["natural"="coastline"]({bb(CITIES[city]["bbox"])});out geom;'
        get(OVERPASS, RAW / "osm" / f"{city}_coast.json", method="POST", data={"data": q})

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
