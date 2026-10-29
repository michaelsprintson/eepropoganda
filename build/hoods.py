"""Fetch outlines of historic neighborhoods from OSM boundaries and write deck/data/hoods.json.

Each outline is the union of one or more OSM boundary polygons (e.g. Munich's Altstadt is its four
medieval quarters). Run with: .venv/bin/python -I build/hoods.py
"""
import json
import math
import pathlib
import sys

from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import linemerge, polygonize, unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch import OVERPASS, get  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "osm"
R, ORIGIN = 6378.137, (14.0, 49.0)

# (south, west, north, east) search area, then each neighborhood: display name, OSM names to merge, blurb
HOODS = {
    "vienna": {"bbox": (48.17, 16.32, 48.23, 16.40), "hoods": [
        ("Innere Stadt", ["Innere Stadt"], "The old town inside the Ringstraße: UNESCO World Heritage, the cathedral, palaces and coffee houses."),
        ("Neubau & Spittelberg", ["Neubau"], "Cobbled Biedermeier lanes at Spittelberg, plus design shops, bars and cafés."),
        ("Josefstadt", ["Josefstadt"], "Quiet 19th-century streets, little theaters and good neighborhood restaurants."),
    ]},
    "prague": {"bbox": (50.07, 14.38, 50.10, 14.44), "hoods": [
        ("Staré Město", ["Staré Město"], "Old Town: Gothic spires, the Astronomical Clock and a maze of medieval lanes."),
        ("Malá Strana", ["Malá Strana"], "Baroque palaces and hidden gardens under the castle hill."),
        ("Hradčany", ["Hradčany"], "The castle district, with St. Vitus Cathedral and quiet lanes past the crowds."),
        ("Josefov", ["Josefov"], "The old Jewish Quarter, with its synagogues and centuries-old cemetery."),
    ]},
    "munich": {"bbox": (48.12, 11.55, 48.15, 11.61), "hoods": [
        ("Altstadt & Lehel", ["Altstadt-Lehel"], "The medieval core inside the old walls (Marienplatz, Frauenkirche, Viktualienmarkt) and the elegant 19th-century Lehel by the river."),
        ("Haidhausen & Au", ["Au-Haidhausen"], "The old French Quarter: leafy squares, cafés and beer gardens across the Isar."),
    ]},
}


def km(lon, lat):
    m = lambda lo, la: (R * math.radians(lo), -R * math.log(math.tan(math.pi / 4 + math.radians(la) / 2)))
    x, y = m(lon, lat)
    ox, oy = m(*ORIGIN)
    return x - ox, y - oy


def query(bbox, names):
    rx = "^(" + "|".join(sorted(set(names))) + ")$"
    b = ",".join(map(str, bbox))
    return f"""[out:json][timeout:120];
(
  relation["boundary"="administrative"]["name"~"{rx}"]({b});
  relation["place"]["name"~"{rx}"]({b});
  way["place"]["name"~"{rx}"]({b});
  way["boundary"="administrative"]["name"~"{rx}"]({b});
);
out geom;"""


def polygon(el):
    if el["type"] == "way":
        pts = [(n["lon"], n["lat"]) for n in el.get("geometry", []) if n]
        return Polygon(pts).buffer(0) if len(pts) > 3 and pts[0] == pts[-1] else None
    outer, inner = [], []
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            pts = [(n["lon"], n["lat"]) for n in m["geometry"] if n]
            if len(pts) >= 2:
                (inner if m.get("role") == "inner" else outer).append(LineString(pts))
    if not outer:
        return None
    g = unary_union(list(polygonize(linemerge(outer))))
    if inner:
        g = g.difference(unary_union(list(polygonize(linemerge(inner)))))
    return g if not g.is_empty else None


def main():
    out = {}
    for city, cfg in HOODS.items():
        names = [n for _, ns, _ in cfg["hoods"] for n in ns]
        path = RAW / f"{city}_hoods.json"
        get(OVERPASS, path, method="POST", data={"data": query(cfg["bbox"], names)})
        best = {}  # OSM name -> largest polygon (an area can be mapped at several admin levels)
        for el in json.loads(path.read_text())["elements"]:
            g = polygon(el)
            n = el.get("tags", {}).get("name")
            if g is not None and (n not in best or g.area > best[n].area):
                best[n] = g
        out[city] = []
        for display, ns, blurb in cfg["hoods"]:
            parts = [best[n] for n in ns if n in best]
            if not parts:
                print("MISSING", city, display)
                continue
            g = unary_union(parts).buffer(0.00005).buffer(-0.00005).simplify(0.00004)
            polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
            rings = [[[round(c, 4) for c in km(x, y)] for x, y in p.exterior.coords] for p in polys if p.area > 1e-7]
            lab = g.representative_point()
            out[city].append({"name": display, "blurb": blurb, "rings": rings, "label": [round(c, 4) for c in km(lab.x, lab.y)]})
            print(f"{city:<7} {display:<14} from {[n for n in ns if n in best]} area {g.area * 111 * 74:.2f} km²")
    (ROOT / "deck" / "data" / "hoods.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
