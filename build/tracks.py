"""Trace each train leg along real OSM rails, station to station, for the whole journey.

The rail network is the main/branch lines downloaded along each leg's corridor (corridors.py)
plus every track inside the city map areas. Each pair of consecutive stations is joined by the
shortest path on that network. Writes deck/data/legs.json.
Run with: .venv/bin/python -I build/tracks.py
"""
import heapq
import json
import math
import pathlib

from shapely.geometry import LineString

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "osm"
R, ORIGIN = 6378.137, (14.0, 49.0)
CITIES = ["vienna", "prague", "munich"]
PENALTY = {"service": 4.0, "tunnel": 2.5}
SNAP_KM = 3.0  # mercator km; stations further than this from track keep a straight segment


def merc(lon, lat):
    return R * math.radians(lon), -R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


OX, OY = merc(*ORIGIN)


def km(lon, lat):
    x, y = merc(lon, lat)
    return x - OX, y - OY


def add_ways(path, nodes, adj, keep):
    for el in json.loads(path.read_text())["elements"]:
        t = el.get("tags", {})
        if el["type"] != "way" or t.get("railway") != "rail" or not keep(t):
            continue
        mult = (PENALTY["service"] if t.get("service") else 1.0) * (PENALTY["tunnel"] if t.get("tunnel") == "yes" else 1.0)
        ids, geom = el["nodes"], el["geometry"]
        for nid, g in zip(ids, geom):
            if g:
                nodes[nid] = km(g["lon"], g["lat"])
        for a, b in zip(ids, ids[1:]):
            if a in nodes and b in nodes:
                d = math.dist(nodes[a], nodes[b]) * mult
                adj.setdefault(a, []).append((b, d))
                adj.setdefault(b, []).append((a, d))


def build_graph(leg_id, cities):
    nodes, adj = {}, {}
    add_ways(RAW / f"corridor_{leg_id}.json", nodes, adj, lambda t: True)
    for c in cities:  # full city networks, so the in-city part follows the exact tracks
        add_ways(RAW / f"{c}_base.json", nodes, adj, lambda t: t.get("usage") not in ("industrial", "military"))
    bridge_gaps(nodes, adj)
    return nodes, adj


def bridge_gaps(nodes, adj, gap_km=0.09, mult=6.0):
    """OSM has small breaks where tracks are mapped without a shared node. Link dead ends
    to nearby track, at a cost penalty."""
    cell = {}
    for n, (x, y) in nodes.items():
        if n in adj:
            cell.setdefault((int(x // gap_km), int(y // gap_km)), []).append(n)
    for a in [n for n, nb in adj.items() if len(nb) == 1]:
        ax, ay = nodes[a]
        cx, cy = int(ax // gap_km), int(ay // gap_km)
        linked = {v for v, _ in adj[a]}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for b in cell.get((cx + dx, cy + dy), []):
                    d = math.dist(nodes[a], nodes[b])
                    if b != a and b not in linked and d < gap_km:
                        adj[a].append((b, d * mult + 0.05))
                        adj[b].append((a, d * mult + 0.05))


def component(adj, s):
    seen, stack = {s}, [s]
    while stack:
        for v, _ in adj.get(stack.pop(), []):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def largest_component(adj):
    seen, best = set(), set()
    for n in adj:
        if n not in seen:
            c = component(adj, n)
            seen |= c
            if len(c) > len(best):
                best = c
    return best


def join_closest(nodes, adj, a, b, cell_km=1.0, max_km=15.0):
    """Link the track networks containing a and b at their closest pair of nodes (grid search)."""
    ca, cb = component(adj, a), component(adj, b)
    grid = {}
    for n in ca:
        x, y = nodes[n]
        grid.setdefault((int(x // cell_km), int(y // cell_km)), []).append(n)
    best = (max_km, None, None)
    reach = int(max_km // cell_km) + 1
    for nb in cb:
        x, y = nodes[nb]
        gx, gy = int(x // cell_km), int(y // cell_km)
        r = min(reach, int(best[0] // cell_km) + 1)
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for na in grid.get((gx + dx, gy + dy), ()):
                    d = math.dist(nodes[na], (x, y))
                    if d < best[0]:
                        best = (d, na, nb)
    d, na, nb = best
    if na is None:
        return None
    adj[na].append((nb, d * 6 + 0.05))
    adj[nb].append((na, d * 6 + 0.05))
    return d


def route(nodes, adj, a, b, label):
    """Shortest track path; bridges breaks in the OSM network (up to 6 joins) when needed."""
    for _ in range(7):
        p = dijkstra(adj, a, b)
        if p:
            return p
        d = join_closest(nodes, adj, a, b)
        if d is None:
            return None
        print(f"   {label}: bridged a {d * 1000 / 1.5:.0f} m break")
    return None


def nearest(nodes, cands, p, max_km):
    best, bd = None, max_km
    for nid in cands:
        d = math.dist(p, nodes[nid])
        if d < bd:
            best, bd = nid, d
    return best


def dijkstra(adj, a, b):
    dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            break
        if d > dist.get(u, 1e18):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    if b not in dist:
        return None
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    return path[::-1]


CITY_KM = [(km(w, n), km(e, s)) for w, s, e, n in [
    (16.265, 48.160, 16.430, 48.270), (14.355, 50.050, 14.480, 50.115), (11.460, 48.075, 11.650, 48.215)]]


def in_city(p):
    return any(a[0] < p[0] < b[0] and a[1] < p[1] < b[1] for a, b in CITY_KM)


def smooth(coords, out_tol=0.6, city_tol=0.008, rounds=4):
    """Keep exact track inside the city maps; elsewhere simplify to a gentle approximation.
    Then round every corner with Chaikin corner cutting so the line has no sharp turns."""
    runs, cur = [], [coords[0]]
    for p in coords[1:]:
        if in_city(p) != in_city(cur[-1]):
            runs.append(cur)
            cur = [cur[-1]]
        cur.append(p)
    runs.append(cur)
    pts = []
    for run in runs:
        tol = city_tol if in_city(run[len(run) // 2]) else out_tol
        simp = list(LineString(run).simplify(tol).coords) if len(run) > 1 else run
        pts += simp if not pts else simp[1:]
    for _ in range(rounds):
        nxt = [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            nxt += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1), (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
        nxt.append(pts[-1])
        pts = nxt
    return LineString(pts).simplify(0.004).coords


def main():
    legs = json.loads((ROOT / "build" / "legs.json").read_text())
    for lid, leg in legs.items():
        st = leg["stations"]
        nodes, adj = build_graph(lid, [leg["from"], leg["to"]])
        main_net = largest_component(adj)
        print(f"{lid}: {len(nodes)} track nodes, main network {len(main_net)}")
        pts = [km(s[1], s[2]) for s in st]
        snap = [nearest(nodes, main_net, p, 1.2) or nearest(nodes, adj, p, SNAP_KM) for p in pts]
        out = [pts[0]]
        for i in range(len(st) - 1):
            a, b = snap[i], snap[i + 1]
            path = route(nodes, adj, a, b, f"{st[i][0]} -> {st[i + 1][0]}") if a and b else None
            straight = math.dist(pts[i], pts[i + 1])
            if not path:
                print(f"   {st[i][0]} -> {st[i + 1][0]}: no track path, straight line")
                out.append(pts[i + 1])
                continue
            seg = [nodes[n] for n in path]
            length = sum(math.dist(p, q) for p, q in zip(seg, seg[1:]))
            flag = "  <-- detour?" if length > straight * 1.6 + 2 else ""
            print(f"   {st[i][0]} -> {st[i + 1][0]}: {length:.1f} km on track vs {straight:.1f} straight{flag}")
            out += seg
        out.append(pts[-1])
        leg["path"] = [[round(x, 4), round(y, 4)] for x, y in smooth(out)]
        print(f"   {len(leg['path'])} points")
    (ROOT / "deck" / "data" / "legs.json").write_text(json.dumps(legs, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
