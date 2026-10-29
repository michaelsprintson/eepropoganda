"""Retry failed geocodes with simpler query variants (name+city, then street address)."""
import json, pathlib, time, requests
ROOT = pathlib.Path(__file__).resolve().parent.parent
p = ROOT / "data" / "raw" / "geocode.json"
geo = json.loads(p.read_text())
UA = {"User-Agent": "ee-trip-deck/0.1 (personal trip planning map)"}
for q, v in list(geo.items()):
    if v:
        continue
    parts = [s.strip() for s in q.split(",")]
    for alt in [f"{parts[0]}, {parts[-1]}", f"{parts[1]}, {parts[-1]}"]:
        res = requests.get("https://nominatim.openstreetmap.org/search", headers=UA,
                           params={"q": alt, "format": "jsonv2", "limit": 1}, timeout=60).json()
        time.sleep(1.2)
        if res:
            geo[q] = {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]), "name": res[0]["display_name"], "via": alt}
            print(q, "->", alt, "->", res[0]["display_name"][:80])
            break
    else:
        print(q, "STILL NOT FOUND")
p.write_text(json.dumps(geo, indent=1, ensure_ascii=False))
