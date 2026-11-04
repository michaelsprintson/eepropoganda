"""The rest of each city, loaded after its core map: deck/data/<city>_more.json.

The core file (process.py) stays as it is and loads first. This one adds what lies around it: streets,
parks and water out to OUTER, and buildings across the whole core map (the core file only has them in
its old-town box). Same projection, origin and encoding as the core file, so the page merges the two.
Downloads are tiled, as the public Overpass servers turn down big boxes.
Run with: .venv/bin/python -I build/more.py [city ...]
"""
import json
import math
import pathlib
import sys
import time

from shapely.geometry import LineString, Polygon, box
from shapely.ops import transform
from shapely.prepared import prep

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch import OVERPASS, RAW, base_query, building_query, get  # noqa: E402
from process import (CITY_BOXES, MIN_AREA, OUT, OX, OY, TOL, classify, flat_delta, iter_parts, merc,  # noqa: E402
                     relation_geom, sea_polygons, way_geom)

# (west, south, east, north): how far each city's map reaches once the rest has loaded
OUTER = {
    "prague": (14.25, 50.00, 14.62, 50.16),   # out to the airport
    "vienna": (16.18, 48.10, 16.58, 48.33),   # the whole city, Schwechat airport at the edge
    "zagreb": (15.87, 45.75, 16.10, 45.92),   # up to the top of Sljeme
    "split": (16.20, 43.46, 16.62, 43.60),    # Trogir and the airport to Podstrana
}
COASTAL = {"split"}
TILE = {"base": 0.06, "bd": 0.03}  # degrees per download tile


def tiles(b, step):
    w, s, e, n = b
    return [(round(y, 4), round(x, 4), round(min(y + step, n), 4), round(min(x + step, e), 4))
            for x in [w + i * step for i in range(math.ceil((e - w) / step - 1e-9))]
            for y in [s + j * step for j in range(math.ceil((n - s) / step - 1e-9))]]


def fetch(city):
    """Every tile of the outer box for streets, parks and water; of the core map for buildings."""
    out = {"base": [], "bd": []}
    for kind, b, query in (("base", OUTER[city], base_query), ("bd", CITY_BOXES[city][0], building_query)):
        for i, t in enumerate(tiles(b, TILE[kind])):
            path = RAW / "osm" / f"more_{city}_{kind}_{i}.json"
            if not path.exists():
                get(OVERPASS, path, method="POST", data={"data": query(t)})
                time.sleep(2)
            out[kind].append(path)
    coast = None
    if city in COASTAL:
        w, s, e, n = OUTER[city]
        coast = RAW / "osm" / f"more_{city}_coast.json"
        get(OVERPASS, coast, method="POST", data={"data": f'[out:json][timeout:240];way["natural"="coastline"]({s},{w},{n},{e});out geom;'})
    return out, coast


def build(city):
    files, coast = fetch(city)
    full, core = CITY_BOXES[city]
    cx, cy = merc((full[0] + full[2]) / 2, (full[1] + full[3]) / 2)  # the core file's projection centre

    def proj(lon, lat):
        x, y = merc(lon, lat)
        return (x - cx) * 1000, (y - cy) * 1000

    outer, inner, core_bd = (transform(proj, box(*b)) for b in (OUTER[city], full, core))
    ring = outer.difference(inner)  # streets, parks, water: only what the core file doesn't have
    bd_ring = inner.difference(core_bd)  # buildings: the core map outside its old-town box
    ring_p, bd_p = prep(ring), prep(bd_ring)
    layers = {k: [] for k in TOL}
    seen = set()
    for kind in ("base", "bd"):
        for path in files[kind]:
            for el in json.loads(path.read_text())["elements"]:
                key = (el["type"], el["id"])
                if key in seen:
                    continue
                seen.add(key)
                layer, shape_kind = classify(el.get("tags", {}))
                if not layer or (layer == "bd") != (kind == "bd"):
                    continue
                g = relation_geom(el, proj) if el["type"] == "relation" else way_geom(el, shape_kind, proj)
                if g is None or g.is_empty:
                    continue
                area, area_p = (bd_ring, bd_p) if layer == "bd" else (ring, ring_p)
                if not area_p.intersects(g):
                    continue
                g = g.intersection(area).simplify(TOL[layer])
                for part in iter_parts(g):
                    if isinstance(part, Polygon):
                        if part.area >= MIN_AREA.get(layer, 0):
                            layers[layer].append([flat_delta(part.exterior.coords)] + [flat_delta(i.coords) for i in part.interiors if i.length > 30])
                    elif isinstance(part, LineString) and part.length > 5:
                        layers[layer].append(flat_delta(part.coords))
    replace = []
    if coast:  # the sea is redrawn for the whole wider box
        layers["sea"] = sea_polygons(json.loads(coast.read_text())["elements"], proj, outer)
        replace.append("sea")
    w, s = proj(OUTER[city][0], OUTER[city][1])
    e, n = proj(OUTER[city][2], OUTER[city][3])
    data = {"origin": [round(cx - OX, 4), round(cy - OY, 4)], "bounds": [round(w), round(n), round(e), round(s)],
            "replace": replace, "layers": {k: v for k, v in layers.items() if v}}
    path = OUT / f"{city}_more.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(city, path.stat().st_size // 1024, "KB", {k: len(v) for k, v in data["layers"].items()})


def main():
    for city in sys.argv[1:] or OUTER:
        build(city)


if __name__ == "__main__":
    main()
