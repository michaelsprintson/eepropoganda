"""Country-level map detail for the three countries on the trip: main roads, main railways, rivers, lakes, towns.

Downloads each country's layers from Overpass once (data/raw/osm/land_<iso>_<layer>.json), merges the pieces into
long lines and simplifies them at two levels: deck/data/land.json (all three countries, light, for the zoomed-out map)
and deck/data/land_<iso>.json (one per country, finer, for zooming in). Coordinates are map km like region.json,
stored as delta-encoded integer metres.
Run with: .venv/bin/python -I build/land.py
"""
import json
import math
import pathlib
import sys
import time

from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, polygonize, unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch import OVERPASS, RAW, get  # noqa: E402
from process import iter_parts, to_km  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "deck" / "data"
COUNTRIES = {"cz": "CZ", "at": "AT", "hr": "HR"}

QUERIES = {
    "roads": 'way["highway"~"^(motorway|trunk|primary)$"](area.a);',
    "rail": 'way["railway"="rail"]["usage"~"^(main|branch)$"][!"service"](area.a);',
    "rivers": 'way["waterway"="river"](area.a);',
    "lakes": 'way["natural"="water"]["water"~"^(lake|reservoir)$"](area.a);relation["natural"="water"]["water"~"^(lake|reservoir)$"](area.a);',
    "towns": 'node["place"~"^(city|town)$"]["name"](area.a);',
}
# simplification tolerances in map km: the light level for the whole-trip view, the fine one for a closer look
TOL = {"lo": 0.35, "hi": 0.04}
MIN_LAKE_KM2 = {"lo": 4.0, "hi": 0.6}  # mercator km², about half that on the ground at these latitudes
MIN_LINE_KM = {"lo": 2.0, "hi": 0.3}


BOXES = {"cz": (12.09, 48.55, 18.86, 51.06), "at": (9.53, 46.37, 17.16, 49.02), "hr": (13.49, 42.39, 19.45, 46.55)}  # lon/lat
TILE_DEG = {"towns": (20.0, 20.0), "lakes": (4.0, 3.0)}  # light layers in one or a few requests; the rest 2° × 1.5°


def fetch(iso):
    """Each layer in tiles of the country's box, each clipped to the country: whole-country queries are more than
    the public servers will take. The tiles are joined into one file per layer, without duplicates."""
    w, s, e, n = BOXES[iso]
    for layer, body in QUERIES.items():
        out = RAW / "osm" / f"land_{iso}_{layer}.json"
        if out.exists():
            continue
        tw, th = TILE_DEG.get(layer, (2.0, 1.5))
        tiles = [(x, y, min(x + tw, e), min(y + th, n))
                 for x in [w + i * tw for i in range(math.ceil((e - w) / tw))]
                 for y in [s + j * th for j in range(math.ceil((n - s) / th))]]
        seen, elements = set(), []
        for t, (x0, y0, x1, y1) in enumerate(tiles):
            part = RAW / "osm" / f"land_{iso}_{layer}_{t}.json"
            clipped = body.replace("(area.a)", f"(area.a)({y0},{x0},{y1},{x1})")
            q = f'[out:json][timeout:600];area["ISO3166-1"="{COUNTRIES[iso]}"][admin_level=2]->.a;({clipped});out geom;'
            get(OVERPASS, part, method="POST", data={"data": q})
            for el in json.loads(part.read_text())["elements"]:
                if (el["type"], el["id"]) not in seen:
                    seen.add((el["type"], el["id"]))
                    elements.append(el)
            time.sleep(3)
        out.write_text(json.dumps({"elements": elements}))
        print("  ", out.name, len(elements), "features")


def elements(iso, layer):
    return json.loads((RAW / "osm" / f"land_{iso}_{layer}.json").read_text())["elements"]


def line_of(el):
    pts = [to_km(n["lon"], n["lat"]) for n in el.get("geometry", []) if n]
    return LineString(pts) if len(pts) >= 2 else None


def merged(lines):
    """Join the pieces into as few long lines as possible: far fewer path segments to stroke."""
    m = linemerge(unary_union(lines)) if lines else None
    return [p for p in iter_parts(m) if isinstance(p, LineString)] if m else []


def enc(coords):
    """Delta-encoded integer metres."""
    out, px, py = [], 0, 0
    for i, (x, y) in enumerate(coords):
        mx, my = round(x * 1000), round(y * 1000)
        out += [mx, my] if i == 0 else [mx - px, my - py]
        px, py = mx, my
    return out


def lakes_of(iso):
    polys = []
    for el in elements(iso, "lakes"):
        if el["type"] == "way":
            ln = line_of(el)
            if ln and len(ln.coords) >= 4 and ln.coords[0] == ln.coords[-1]:
                polys.append(Polygon(ln.coords).buffer(0))
        else:
            outer = [line_of(m) for m in el.get("members", []) if m.get("type") == "way" and m.get("role") != "inner"]
            inner = [line_of(m) for m in el.get("members", []) if m.get("type") == "way" and m.get("role") == "inner"]
            o = unary_union(list(polygonize(linemerge([x for x in outer if x])))) if any(outer) else None
            if o is not None and not o.is_empty:
                if any(inner):
                    o = o.difference(unary_union(list(polygonize(linemerge([x for x in inner if x])))))
                polys.append(o)
    return polys


def level(iso, lv, cache):
    tol, out = TOL[lv], {}
    for key, layer, pick in [("r1", "roads", lambda t: t.get("highway") in ("motorway", "trunk")),
                             ("r2", "roads", lambda t: t.get("highway") == "primary"),
                             ("rl", "rail", lambda t: True),
                             ("wl", "rivers", lambda t: True)]:
        if (key, iso) not in cache:
            cache[(key, iso)] = merged([ln for el in elements(iso, layer) if pick(el.get("tags", {})) and (ln := line_of(el))])
        lines = [ln.simplify(tol) for ln in cache[(key, iso)]]
        out[key] = [enc(ln.coords) for ln in lines if ln.length >= MIN_LINE_KM[lv]]
    if ("wa", iso) not in cache:
        cache[("wa", iso)] = lakes_of(iso)
    out["wa"] = []
    for g in cache[("wa", iso)]:
        for poly in iter_parts(g.simplify(tol)):
            if isinstance(poly, Polygon) and poly.area >= MIN_LAKE_KM2[lv]:
                out["wa"].append(enc(poly.exterior.coords))
    return out


def towns(iso):
    out = []
    for el in elements(iso, "towns"):
        t = el.get("tags", {})
        try:
            pop = int(str(t.get("population", "0")).replace(" ", "").replace(",", "").split(";")[0] or 0)
        except ValueError:
            pop = 0
        x, y = to_km(el["lon"], el["lat"])
        out.append([t["name"], round(x, 2), round(y, 2), pop, t["place"] == "city"])
    return sorted(out, key=lambda r: -r[3])


def main():
    for iso in COUNTRIES:
        fetch(iso)
    cache, light = {}, {"r1": [], "r2": [], "rl": [], "wl": [], "wa": [], "towns": []}
    for iso in COUNTRIES:
        hi = level(iso, "hi", cache)
        hi["towns"] = towns(iso)
        (OUT / f"land_{iso}.json").write_text(json.dumps(hi, separators=(",", ":"), ensure_ascii=False))
        lo = level(iso, "lo", cache)
        for k, v in lo.items():
            light[k] += v
        light["towns"] += [t for t in hi["towns"] if t[3] >= 20000 or t[4]]  # the bigger towns label the whole-trip view
        print(iso, (OUT / f"land_{iso}.json").stat().st_size // 1024, "KB fine,", {k: len(v) for k, v in hi.items()})
    (OUT / "land.json").write_text(json.dumps(light, separators=(",", ":"), ensure_ascii=False))
    print("land.json", (OUT / "land.json").stat().st_size // 1024, "KB light,", {k: len(v) for k, v in light.items()})


if __name__ == "__main__":
    main()
