"""Read data/airbnblocs.txt (city name lines followed by listing URLs), fetch each listing's
title and approximate location, and write deck/data/stays.json.

Run with: .venv/bin/python -I build/airbnb.py
"""
import html
import json
import math
import pathlib
import re
import time
from urllib.parse import urlsplit, parse_qs, urlencode

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "airbnb"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "en-US"}
KEEP = ("adults", "check_in", "check_out")
R, ORIGIN = 6378.137, (14.0, 49.0)


def to_km(lon, lat):
    m = lambda lo, la: (R * math.radians(lo), -R * math.log(math.tan(math.pi / 4 + math.radians(la) / 2)))
    x, y = m(lon, lat)
    ox, oy = m(*ORIGIN)
    return [round(x - ox, 4), round(y - oy, 4)]


def parse_list():
    out, city = {}, None
    for line in (ROOT / "data" / "airbnblocs.txt").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("http"):
            out[city].append(line)
        else:
            city = line.lower()
            out[city] = []
    return out


def clean(url):
    u = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items() if k in KEEP}
    return f"https://www.airbnb.com{u.path}" + (f"?{urlencode(q)}" if q else "")


def listing(url):
    room = urlsplit(url).path.rstrip("/").split("/")[-1]
    path = RAW / f"{room}.html"
    if not path.exists():
        r = requests.get(f"https://www.airbnb.com/rooms/{room}", headers=UA, timeout=30)
        r.raise_for_status()
        path.write_text(r.text)
        time.sleep(2)
    page = path.read_text()
    meta = lambda prop: (re.findall(rf'<meta property="og:{prop}" content="([^"]+)"', page) or [""])[0]
    lat = re.findall(r'"latitude":([\d.]+)', page)
    lon = re.findall(r'"longitude":([\d.]+)', page)
    return {"id": room, "title": html.unescape(meta("title")), "desc": html.unescape(meta("description")),
            "lat": float(lat[0]) if lat else None, "lon": float(lon[0]) if lon else None}


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    stays = {}
    for city, urls in parse_list().items():
        stays[city] = []
        for url in urls:
            d = listing(url)
            item = {"url": clean(url), "title": d["title"], "desc": d["desc"]}
            if d["lat"] is not None:
                item["xy"] = to_km(d["lon"], d["lat"])
            stays[city].append(item)
            print(city, d["id"], "|", d["title"], "|", d["desc"], "|", d["lat"], d["lon"])
    (ROOT / "deck" / "data" / "stays.json").write_text(json.dumps(stays, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
