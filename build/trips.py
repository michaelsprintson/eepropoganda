"""Trace how each day trip is reached: real track for the rail parts, the road route for bus and tour parts.

Rail parts download main/branch lines in a corridor along their stations (data/raw/osm/corridor_trip_<id>_<n>.json)
and follow the shortest track path, as tracks.py does for the train legs. Road parts ask the public OSRM router
once and cache its answer (data/raw/routes/). Stations and endpoints are geocoded once into data/raw/geocode.json.
Writes deck/data/trips.json: {trip: {from: city, segs: [{kind: "rail" | "road", pts: [[x, y], ...]}]}}, in map km.
Run with: .venv/bin/python -I build/trips.py
"""
import json
import math
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from corridors import corridor_poly  # noqa: E402
from fetch import OVERPASS, RAW, UA, get  # noqa: E402
from tracks import add_ways, bridge_gaps, km, largest_component, nearest, route, smooth  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROUTES = RAW / "routes"
OSRM = "https://router.project-osrm.org/route/v1/driving/{};{}?overview=full&geometries=geojson"

# each trip from its base city: rail parts list their stations in order, road parts their two ends
TRIPS = {
    "karlstejn": ("prague", [("rail", ["Praha hlavní nádraží", "Praha-Smíchov", "Karlštejn railway station"])]),
    "kutnahora": ("prague", [("rail", ["Praha hlavní nádraží", "Kolín railway station", "Kutná Hora hlavní nádraží"])]),
    # stations along the way keep the corridor in the Vltava and Elbe valleys
    "bohemian": ("prague", [("rail", ["Praha hlavní nádraží", "Praha-Holešovice", "Kralupy nad Vltavou railway station",
                                      "Roudnice nad Labem railway station", "Lovosice railway station",
                                      "Ústí nad Labem hlavní nádraží", "Ústí nad Labem-Neštěmice", "Děčín hlavní nádraží"]),
                            ("road", ["Děčín hlavní nádraží", "Hřensko"])]),
    "wachau": ("vienna", [("rail", ["Wien Franz-Josefs-Bahnhof", "Heiligenstadt Bahnhof Wien", "Klosterneuburg-Kierling Bahnhof",
                                    "Greifenstein-Altenberg Bahnhof", "Tulln an der Donau Bahnhof", "Absdorf-Hippersdorf Bahnhof",
                                    "Krems an der Donau Bahnhof"])]),
    "lednice": ("vienna", [("rail", ["Wien Hauptbahnhof", "Gänserndorf Bahnhof", "Hohenau Bahnhof", "Břeclav railway station"]),
                           ("road", ["Břeclav railway station", "Lednice Chateau"])]),
    "kahlenberg": ("vienna", [("road", ["Heiligenstadt Bahnhof Wien", "Kahlenberg Wien"])]),
    "hallstatt": ("vienna", [("road", ["Wien Hauptbahnhof", "Marktplatz, Hallstatt"])]),
    "trogir": ("split", [("road", ["Autobusni kolodvor Split", "Trogir"])]),
    "krka": ("split", [("road", ["Autobusni kolodvor Split", "Skradin"])]),
}


def geocoder():
    path = RAW / "geocode.json"
    geo = json.loads(path.read_text()) if path.exists() else {}

    def lonlat(q):
        if q not in geo:
            r = requests.get("https://nominatim.openstreetmap.org/search", headers=UA,
                             params={"q": q, "format": "jsonv2", "limit": 1}, timeout=60)
            r.raise_for_status()
            res = r.json()
            geo[q] = {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]), "name": res[0]["display_name"]} if res else None
            print("geo", q, "->", (geo[q] or {}).get("name", "NOT FOUND")[:90])
            path.write_text(json.dumps(geo, indent=1, ensure_ascii=False))
            time.sleep(1.2)
        if not geo[q]:
            raise SystemExit(f"no geocode for {q!r}")
        return geo[q]["lon"], geo[q]["lat"]
    return lonlat


def drop_spurs(seg, near_km=0.3, min_km=1.5):
    """Cut out-and-back excursions: a missing link at a junction sends the shortest path past it and back."""
    out, i = [], 0
    while i < len(seg):
        out.append(seg[i])
        j = next((j for j in range(len(seg) - 1, i + 5, -1) if math.dist(seg[i], seg[j]) < near_km
                  and sum(math.dist(p, q) for p, q in zip(seg[i:j], seg[i + 1:j + 1])) > min_km), None)
        i = j if j else i + 1
    return out


def osrm(a, b, cache):
    """The driving route between two points (lon, lat), cached; as map km."""
    ROUTES.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        r = requests.get(OSRM.format(f"{a[0]},{a[1]}", f"{b[0]},{b[1]}"), headers=UA, timeout=60)
        r.raise_for_status()
        cache.write_text(r.text)
        time.sleep(1.5)
    res = json.loads(cache.read_text())["routes"][0]
    return [km(lon, lat) for lon, lat in res["geometry"]["coordinates"]], res["distance"] / 1000


def rail(trip, n, names, lonlat, city):
    st = [(nm, *lonlat(nm)) for nm in names]
    raw = RAW / "osm" / f"corridor_trip_{trip}_{n}.json"
    # every running line, tagged main/branch or not (some stretches carry no usage tag); sidings and yards left out
    q = f'[out:json][timeout:600];way["railway"="rail"][!"service"](poly:"{corridor_poly(st)}");out geom;'
    get(OVERPASS, raw, method="POST", data={"data": q})
    nodes, adj = {}, {}
    add_ways(raw, nodes, adj, lambda t: t.get("usage") not in ("industrial", "military", "tourism"))
    # the base city's own tracks, so trains leave its station on the right line
    add_ways(RAW / "osm" / f"{city}_base.json", nodes, adj, lambda t: t.get("usage") not in ("industrial", "military"))
    bridge_gaps(nodes, adj)
    main_net = largest_component(adj)
    pts = [km(s[1], s[2]) for s in st]
    snap = [nearest(nodes, main_net, p, 1.5) or nearest(nodes, adj, p, 3.0) for p in pts]
    out = [pts[0]]
    for i in range(len(st) - 1):
        a, b = snap[i], snap[i + 1]
        path = route(nodes, adj, a, b, f"{st[i][0]} -> {st[i + 1][0]}") if a and b else None
        if not path:
            print(f"   {st[i][0]} -> {st[i + 1][0]}: no track path, straight line")
            out.append(pts[i + 1])
            continue
        seg = drop_spurs([nodes[x] for x in path])
        length = sum(math.dist(p, q) for p, q in zip(seg, seg[1:]))
        ratio = length / max(math.dist(pts[i], pts[i + 1]), 0.1)
        if ratio > 1.6:  # a gap in the track data: the road between the stations runs along the same valley
            seg, _ = osrm(st[i][1:], st[i + 1][1:], ROUTES / f"{trip}_{n}_{i}_rail.json")
            print(f"   {st[i][0]} -> {st[i + 1][0]}: {ratio:.2f}x the straight line on track, so the road alongside instead")
        else:
            print(f"   {st[i][0]} -> {st[i + 1][0]}: {ratio:.2f}x the straight line")
        out += seg
    out.append(pts[-1])
    return smooth(out, out_tol=0.06, rounds=2)  # close enough to the track at a trip map's zoom


def road(trip, n, names, lonlat):
    pts, dist = osrm(*(lonlat(nm) for nm in names), ROUTES / f"{trip}_{n}.json")
    print(f"   {names[0]} -> {names[1]}: {dist:.0f} km by road")
    return smooth(pts, out_tol=0.06, rounds=2)


def main():
    lonlat = geocoder()
    out = {}
    for trip, (city, parts) in TRIPS.items():
        print(trip)
        segs = []
        for n, (kind, names) in enumerate(parts):
            pts = rail(trip, n, names, lonlat, city) if kind == "rail" else road(trip, n, names, lonlat)
            segs.append({"kind": kind, "pts": [[round(x, 3), round(y, 3)] for x, y in pts]})
        out[trip] = {"from": city, "segs": segs}
    path = ROOT / "deck" / "data" / "trips.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print("trips.json", path.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
