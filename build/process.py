"""Turn raw downloads into compact map files for the deck.

Writes deck/data/region.json, deck/data/<city>.json and deck/data/places.json.
Coordinates are Web Mercator, y flipped to point down. Region layers are in km
relative to ORIGIN; city layers are integer metres relative to the city origin,
delta-encoded as flat [x0, y0, dx1, dy1, ...] arrays.

Run with: .venv/bin/python -I build/process.py
"""
import json
import math
import pathlib

from shapely.geometry import LineString, Polygon, MultiPolygon, MultiLineString, GeometryCollection, box, shape
from shapely.ops import polygonize, linemerge, unary_union, transform

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "deck" / "data"
R = 6378.137  # km
ORIGIN = (14.0, 49.0)

REGION_BOX = (-2.0, 40.0, 32.0, 58.5)  # lon/lat
HIGHLIGHT = {"AUT", "CZE", "DEU"}

CITY_BOXES = {  # (west, south, east, north), matches fetch.py
    "vienna": ((16.265, 48.160, 16.430, 48.270), (16.345, 48.192, 16.395, 48.222)),
    "prague": ((14.355, 50.050, 14.480, 50.115), (14.395, 50.074, 14.440, 50.096)),
    "munich": ((11.460, 48.075, 11.650, 48.215), (11.550, 48.128, 11.600, 48.152)),
    "salzburg": ((13.015, 47.785, 13.070, 47.822), (13.030, 47.793, 13.055, 47.812)),
    "lednice": ((16.725, 48.712, 16.900, 48.835), (16.795, 48.797, 16.812, 48.808)),
    "neuschwanstein": ((10.690, 47.538, 10.775, 47.585), (10.725, 47.550, 10.755, 47.562)),
    "wachau": ((15.46, 48.37, 15.615, 48.425), (15.46, 48.37, 15.615, 48.425)),
    "kahlenberg": ((16.3, 48.248, 16.375, 48.3), (16.3, 48.248, 16.375, 48.3)),
    "hallstatt": ((13.625, 47.548, 13.662, 47.575), (13.625, 47.548, 13.662, 47.575)),
    "karlstejn": ((14.165, 49.922, 14.205, 49.968), (14.165, 49.922, 14.205, 49.968)),
    "kutnahora": ((15.25, 49.937, 15.3, 49.968), (15.25, 49.937, 15.3, 49.968)),
    "bohemian": ((14.23, 50.85, 14.335, 50.9), (14.23, 50.85, 14.335, 50.9)),
    "starnberg": ((11.285, 47.935, 11.37, 48.005), (11.285, 47.935, 11.37, 48.005)),
    "partnach": ((11.08, 47.452, 11.145, 47.5), (11.08, 47.452, 11.145, 47.5)),
}


def merc(lon, lat):
    return R * math.radians(lon), -R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


OX, OY = merc(*ORIGIN)


def to_km(lon, lat, z=None):
    x, y = merc(lon, lat)
    return x - OX, y - OY


def iter_parts(g):
    if g.is_empty:
        return
    if isinstance(g, (MultiPolygon, MultiLineString, GeometryCollection)):
        for p in g.geoms:
            yield from iter_parts(p)
    else:
        yield g


def flat_km(coords, nd=2):
    out = []
    for x, y in coords:
        out += [round(x, nd), round(y, nd)]
    return out


def flat_delta(coords):
    out, px, py = [], 0, 0
    for x, y in coords:
        ix, iy = round(x), round(y)
        if out and ix == px and iy == py:
            continue
        out += [ix - px, iy - py] if out else [ix, iy]
        px, py = ix, iy
    return out


# ---------- region ----------

def region():
    lonlat_box = box(*REGION_BOX)
    countries = []
    for f in json.loads((RAW / "ne" / "ne_10m_admin_0_countries.geojson").read_text())["features"]:
        g = shape(f["geometry"]).buffer(0).intersection(lonlat_box)
        if g.is_empty:
            continue
        g = transform(to_km, g)
        if g.is_empty:
            continue
        g = g.simplify(0.4)
        p = f["properties"]
        rings = []
        for poly in iter_parts(g):
            if isinstance(poly, Polygon) and poly.area > 2:
                rings.append([flat_km(poly.exterior.coords, 1)] + [flat_km(i.coords, 1) for i in poly.interiors])
        if not rings:
            continue
        lx, ly = to_km(p["LABEL_X"], p["LABEL_Y"])
        countries.append({"a3": p["ADM0_A3"], "name": p["NAME_EN"], "hi": p["ADM0_A3"] in HIGHLIGHT,
                          "label": [round(lx), round(ly)], "polys": rings})

    rivers = []
    for f in json.loads((RAW / "ne" / "ne_10m_rivers_lake_centerlines.geojson").read_text())["features"]:
        p = f["properties"]
        if p.get("scalerank", 10) > 9 or f["geometry"] is None:
            continue
        g = shape(f["geometry"]).intersection(lonlat_box)
        if g.is_empty:
            continue
        g = transform(to_km, g).simplify(0.3)
        for part in iter_parts(g):
            if isinstance(part, LineString) and part.length > 3:
                rivers.append({"name": p.get("name_en") or p.get("name"), "rank": p["scalerank"],
                               "line": flat_km(part.coords, 1)})

    lakes = []
    for f in json.loads((RAW / "ne" / "ne_10m_lakes.geojson").read_text())["features"]:
        g = shape(f["geometry"]).buffer(0).intersection(lonlat_box)
        if g.is_empty:
            continue
        g = transform(to_km, g).simplify(0.3)
        for poly in iter_parts(g):
            if isinstance(poly, Polygon) and poly.area > 1:
                lakes.append([flat_km(poly.exterior.coords, 1)])
    return {"origin": ORIGIN, "countries": countries, "rivers": rivers, "lakes": lakes}


# ---------- cities ----------

GREEN = {"park", "garden", "forest", "grass", "cemetery", "vineyard", "wood"}


def classify(tags):
    h = tags.get("highway")
    if h:
        if h in ("motorway", "trunk", "primary"):
            return "r1", "line"
        if h in ("secondary", "tertiary"):
            return "r2", "line"
        return "r3", "line"
    if tags.get("railway") == "rail":
        if tags.get("service") or tags.get("usage") in ("industrial", "military"):
            return None, None
        return "rl", "line"
    if tags.get("waterway") in ("river", "canal"):
        return "wl", "line"
    if tags.get("natural") == "water":
        return "wa", "poly"
    if tags.get("building"):
        return "bd", "poly"
    if tags.get("leisure") in GREEN or tags.get("landuse") in GREEN or tags.get("natural") in GREEN:
        return "gr", "poly"
    return None, None


def way_geom(el, kind, proj):
    pts = [proj(n["lon"], n["lat"]) for n in el.get("geometry", []) if n]
    if len(pts) < 2:
        return None
    if kind == "poly":
        if len(pts) < 4 or pts[0] != pts[-1]:
            return None
        return Polygon(pts).buffer(0)
    return LineString(pts)


def relation_geom(el, proj):
    outer, inner = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        pts = [proj(n["lon"], n["lat"]) for n in m["geometry"] if n]
        if len(pts) >= 2:
            (inner if m.get("role") == "inner" else outer).append(LineString(pts))
    if not outer:
        return None
    o = unary_union(list(polygonize(linemerge(outer))))
    if inner:
        o = o.difference(unary_union(list(polygonize(linemerge(inner)))))
    return o


TOL = {"r1": 2.0, "r2": 2.0, "r3": 2.5, "rl": 3.0, "wl": 3.0, "wa": 2.0, "gr": 3.0, "bd": 0.8}
MIN_AREA = {"wa": 100, "gr": 400, "bd": 15}


def city(name):
    full, core = CITY_BOXES[name]
    cx, cy = merc((full[0] + full[2]) / 2, (full[1] + full[3]) / 2)

    def proj(lon, lat):
        x, y = merc(lon, lat)
        return (x - cx) * 1000, (y - cy) * 1000  # metres

    clip_full = transform(proj, box(*full))
    clip_core = transform(proj, box(*core))
    layers = {k: [] for k in TOL}
    seen = set()
    for fname in (f"{name}_base.json", f"{name}_buildings.json"):
        for el in json.loads((RAW / "osm" / fname).read_text())["elements"]:
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            layer, kind = classify(el.get("tags", {}))
            if not layer:
                continue
            g = relation_geom(el, proj) if el["type"] == "relation" else way_geom(el, kind, proj)
            if g is None or g.is_empty:
                continue
            g = g.intersection(clip_core if layer == "bd" else clip_full).simplify(TOL[layer])
            for part in iter_parts(g):
                if isinstance(part, Polygon):
                    if part.area < MIN_AREA.get(layer, 0):
                        continue
                    layers[layer].append([flat_delta(part.exterior.coords)] + [flat_delta(i.coords) for i in part.interiors if i.length > 30])
                elif isinstance(part, LineString) and part.length > 5:
                    layers[layer].append(flat_delta(part.coords))
    ox, oy = cx - OX, cy - OY
    w, s = proj(full[0], full[1])
    e, n = proj(full[2], full[3])
    return {"origin": [round(ox, 4), round(oy, 4)], "bounds": [round(w), round(n), round(e), round(s)],
            "layers": layers, "counts": {k: len(v) for k, v in layers.items()}}


# ---------- places ----------

def places():
    data = json.loads((ROOT / "build" / "places.json").read_text())
    geo = json.loads((RAW / "geocode.json").read_text())
    def walk(o):
        if isinstance(o, dict):
            if "q" in o:
                g = geo[o.pop("q")]
                o["xy"] = [round(v, 4) for v in to_km(g["lon"], g["lat"])]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reg = region()
    (OUT / "region.json").write_text(json.dumps(reg, separators=(",", ":")))
    print("region", (OUT / "region.json").stat().st_size // 1024, "KB", len(reg["countries"]), "countries")
    for name in CITY_BOXES:
        c = city(name)
        (OUT / f"{name}.json").write_text(json.dumps(c, separators=(",", ":")))
        print(name, (OUT / f"{name}.json").stat().st_size // 1024, "KB", c["counts"])
    (OUT / "places.json").write_text(json.dumps(places(), ensure_ascii=False, indent=1))
    check_edges()


def check_edges(min_km=1.0):
    """Warn when a pin sits within min_km of its city's detail-map edge (it would fall off the map)."""
    pts = [(v["name"].split(",")[0], v["lon"], v["lat"]) for v in json.loads((RAW / "geocode.json").read_text()).values() if v]
    stays_path = OUT / "stays.json"
    if stays_path.exists():
        for city, items in json.loads(stays_path.read_text()).items():
            for s in items:
                if "xy" in s:  # back-project from km
                    x, y = s["xy"][0] + OX, s["xy"][1] + OY
                    lon = math.degrees(x / R)
                    lat = math.degrees(2 * math.atan(math.exp(-y / R)) - math.pi / 2)
                    pts.append((s["desc"][:30], lon, lat))
    ok = True
    for name, lon, lat in pts:
        for city, (full, _) in CITY_BOXES.items():
            w, s, e, n = full
            if w - 0.05 < lon < e + 0.05 and s - 0.05 < lat < n + 0.05:
                kx = 111.32 * math.cos(math.radians(lat))
                margin = min((lon - w) * kx, (e - lon) * kx, (lat - s) * 111.32, (n - lat) * 111.32)
                if margin < min_km:
                    ok = False
                    print(f"EDGE WARNING {city}: {name} is {margin:.2f} km from the map edge")
    if ok:
        print("all pins at least", min_km, "km inside their city map")


if __name__ == "__main__":
    main()
