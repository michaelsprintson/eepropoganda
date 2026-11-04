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

import heapq

import requests
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from corridors import corridor_poly  # noqa: E402
from fetch import CITIES, OVERPASS, RAW, UA, get  # noqa: E402
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
    # the catamaran, harbour to harbour: found through the water, so it never cuts across an island
    "hvar": ("split", [("sea", [(16.4425, 43.5025), (16.4405, 43.1715)])]),
    # day trips with no zoom-in map of their own are keyed <city>-<code>, as the deck's tripId() names them
    "split-T4": ("split", [("sea", [(16.4425, 43.5025), (16.0221, 42.9802)])]),  # speedboat to the Blue Cave, Biševo
    "split-T5": ("split", [("road", ["Autobusni kolodvor Split", "Veliki slap, Plitvice"])]),
    "split-T6": ("split", [("road", ["Autobusni kolodvor Split", "Stari most, Mostar"])]),
    # a sight off the city map, reached by road to the gondola and the gondola up (OSM way 666115844)
    "place:zagreb-B": ("zagreb", [("road", ["Trg bana Josipa Jelačića, Zagreb", "Žičara Sljeme, lower station"]), ("lift", "sljeme_gondola")]),
    # airport transfers, to where the city's own streets take over
    "air:prague": ("prague", [("road", ["Letiště Václava Havla Praha", "Vítězné náměstí, Praha"])]),
    "air:vienna": ("vienna", [("rail", ["Flughafen Wien Bahnhof", "Schwechat Bahnhof", "Wien Mitte Bahnhof"])]),
    "air:split": ("split", [("road", ["Split Airport", "Autobusni kolodvor Split"])]),
}


STAY_INTO = {"split": "Autobusni kolodvor Split"}  # where the drive in from an out-of-town Airbnb ends


def unkm(x, y):
    """Map km back to lon/lat (the inverse of km())."""
    R, (olon, olat) = 6378.137, (14.0, 49.0)
    oy = -R * math.log(math.tan(math.pi / 4 + math.radians(olat) / 2))
    return olon + math.degrees(x / R), math.degrees(2 * math.atan(math.exp(-(y + oy) / R)) - math.pi / 2)


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
    raw = RAW / "osm" / f"corridor_trip_{trip.replace(':', '_')}_{n}.json"
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
            seg, _ = osrm(st[i][1:], st[i + 1][1:], ROUTES / f"{trip.replace(':', '_')}_{n}_{i}_rail.json")
            print(f"   {st[i][0]} -> {st[i + 1][0]}: {ratio:.2f}x the straight line on track, so the road alongside instead")
        else:
            print(f"   {st[i][0]} -> {st[i + 1][0]}: {ratio:.2f}x the straight line")
        out += seg
    out.append(pts[-1])
    return smooth(out, out_tol=0.06, rounds=2)  # close enough to the track at a trip map's zoom


SEA_CELL = 0.12  # map km, about 90 m on the ground here


def sea(trip, n, ends):
    """The shortest way by water between two harbours, on a grid that blocks every cell touching the real
    coastline (OSM) or the land the zoomed-out map draws (Natural Earth), so the line clips neither."""
    (lon0, lat0), (lon1, lat1) = ends
    w, s_, e, n_ = min(lon0, lon1) - 0.14, min(lat0, lat1) - 0.08, max(lon0, lon1) + 0.14, max(lat0, lat1) + 0.08
    raw = RAW / "osm" / f"coast_{trip}_{n}.json"
    get(OVERPASS, raw, method="POST", data={"data": f'[out:json][timeout:300];way["natural"="coastline"]({s_},{w},{n_},{e});out geom;'})
    (x0, y1), (x1, y0) = km(w, s_), km(e, n_)  # map y grows southwards
    nx, ny = int((x1 - x0) / SEA_CELL) + 1, int((y1 - y0) / SEA_CELL) + 1
    blocked = bytearray(nx * ny)
    cell = lambda x, y: (int((x - x0) / SEA_CELL), int((y - y0) / SEA_CELL))
    for el in json.loads(raw.read_text())["elements"]:  # the coastline itself, a cell either side
        pts = [km(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            steps = max(1, int(math.dist((ax, ay), (bx, by)) / (SEA_CELL / 3)))
            for k in range(steps + 1):
                cx, cy = cell(ax + (bx - ax) * k / steps, ay + (by - ay) * k / steps)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if 0 <= cx + dx < nx and 0 <= cy + dy < ny:
                            blocked[(cy + dy) * nx + cx + dx] = 1
    ne = json.loads((RAW / "ne" / "ne_10m_admin_0_countries.geojson").read_text())["features"]
    land = prep(unary_union([transform(km, shape(f["geometry"]).buffer(0)) for f in ne if f["properties"]["ADM0_A3"] == "HRV"]).simplify(0.4))
    for cy in range(ny):
        for cx in range(nx):
            if not blocked[cy * nx + cx] and land.contains(Point(x0 + (cx + 0.5) * SEA_CELL, y0 + (cy + 0.5) * SEA_CELL)):
                blocked[cy * nx + cx] = 1
    # open water is the largest connected stretch of free cells (inland pockets are small); near shore costs more
    seen, water = set(), set()
    for start in ((cx, cy) for cy in range(ny) for cx in range(nx) if not blocked[cy * nx + cx]):
        if start in seen:
            continue
        comp, stack = {start}, [start]
        while stack:
            cx, cy = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (cx + dx, cy + dy)
                if 0 <= q[0] < nx and 0 <= q[1] < ny and q not in comp and not blocked[q[1] * nx + q[0]]:
                    comp.add(q)
                    stack.append(q)
        seen |= comp
        if len(comp) > len(water):
            water = comp
    near_shore = {c: 0 for c in water}
    for c in water:
        near_shore[c] = sum(1 for dx in range(-3, 4) for dy in range(-3, 4)
                            if not (0 <= c[0] + dx < nx and 0 <= c[1] + dy < ny) or blocked[(c[1] + dy) * nx + c[0] + dx])
    snap = lambda lon, lat: min(water, key=lambda c: (c[0] - cell(*km(lon, lat))[0]) ** 2 + (c[1] - cell(*km(lon, lat))[1]) ** 2)
    a, b = snap(lon0, lat0), snap(lon1, lat1)
    dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            break
        if d > dist[u]:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                v = (u[0] + dx, u[1] + dy)
                if v == u or v not in water:
                    continue
                nd = d + math.hypot(dx, dy) * (1 + 0.15 * near_shore[v])
                if nd < dist.get(v, 1e18):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    pts = [(x0 + (cx + 0.5) * SEA_CELL, y0 + (cy + 0.5) * SEA_CELL) for cx, cy in reversed(path)]
    pts = [km(lon0, lat0)] + pts + [km(lon1, lat1)]
    # round the corners, as much as stays clear of land
    clear = lambda line: all(cell(*line.interpolate(t, normalized=True).coords[0]) in water
                             for t in [i / 400 for i in range(8, 393)])  # ends sit in harbour, by the shore
    for rounds in (4, 3, 2, 1, 0):
        out = list(smooth(list(LineString(pts).simplify(0.15).coords), out_tol=0.0, rounds=rounds))
        if clear(LineString(out)):
            break
    print(f"   {len(water)} water cells, {rounds} rounds of rounding, "
          f"{LineString(out).length / math.dist(km(lon0, lat0), km(lon1, lat1)):.2f}x the straight line")
    return out


def lift(fname):
    """A gondola or cable car as mapped: its OSM line, bottom station first (fetched by hand into data/raw/osm)."""
    el = json.loads((RAW / "osm" / f"{fname}.json").read_text())["elements"][0]
    return [km(g["lon"], g["lat"]) for g in el["geometry"]]


def road(trip, n, names, lonlat):
    pts, dist = osrm(*(lonlat(nm) for nm in names), ROUTES / f"{trip.replace(':', '_')}_{n}.json")
    print(f"   {names[0]} -> {names[1]}: {dist:.0f} km by road")
    return smooth(pts, out_tol=0.06, rounds=2)


def main():
    lonlat = geocoder()
    out = {}
    for trip, (city, parts) in TRIPS.items():
        print(trip)
        segs = []
        for n, (kind, names) in enumerate(parts):
            pts = (rail(trip, n, names, lonlat, city) if kind == "rail" else road(trip, n, names, lonlat) if kind == "road"
                   else lift(names) if kind == "lift" else sea(trip, n, names))
            segs.append({"kind": kind, "pts": [[round(x, 3), round(y, 3)] for x, y in pts]})
        out[trip] = {"from": city, "segs": segs}
    # Airbnbs outside the city's street map get the drive into town, so a day from there follows the road
    stays = json.loads((ROOT / "deck" / "data" / "stays.json").read_text())
    for city, into in STAY_INTO.items():
        s_, w, n_, e = CITIES[city]["bbox"]
        for i, st in enumerate(stays.get(city, [])):
            lon, lat = unkm(*st["xy"])
            if w <= lon <= e and s_ <= lat <= n_:
                continue
            trip = f"stay:{city}-H{i + 1}"
            print(trip)
            here = lambda q, ll=(lon, lat): ll if q == "stay" else lonlat(q)
            room = st["url"].split("/rooms/")[1].split("?")[0]  # cached by listing, so swapping one never reuses another's drive
            pts = road(f"stay-{room}", 0, ["stay", into], here)
            out[trip] = {"from": city, "segs": [{"kind": "road", "pts": [[round(x, 3), round(y, 3)] for x, y in pts]}]}
    path = ROOT / "deck" / "data" / "trips.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print("trips.json", path.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
