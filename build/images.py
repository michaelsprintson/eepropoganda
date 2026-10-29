"""Find, download and resize a photo for every place and Airbnb listing.

Sources, tried in order per place: freely licensed Wikimedia Commons images (via a Wikipedia
article or a Commons search), then the venue's own website share image (og:image).
Airbnb listings use the listing's cover photo. Writes deck/img/*.jpg and deck/data/images.json.

Run with: .venv/bin/python -I build/images.py
"""
import html
import io
import os
import json
import pathlib
import re
import time

import requests
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "img"
OUT = ROOT / "deck" / "img"
UA = {"User-Agent": "ee-trip-deck/0.1 (personal trip planning deck; python-requests)"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

# per place name: list of ("wiki", "lang:Title") / ("site", url) / ("commons", "search terms")
SOURCES = {
    "Figlmüller Wollzeile": [("wiki", "de:Figlmüller"), ("site", "https://figlmueller.at"), ("commons", "Figlmüller Wien")],
    "Mayer am Pfarrplatz": [("commons", "Mayer am Pfarrplatz Heiligenstadt"), ("site", "https://pfarrplatz.at"), ("wiki", "de:Pfarrplatz (Wien)")],
    "Steirereck im Stadtpark": [("wiki", "en:Steirereck"), ("commons", "Steirereck Stadtpark"), ("site", "https://www.steirereck.at")],
    "Café Sperl": [("wiki", "en:Café Sperl"), ("commons", "Café Sperl Wien")],
    "Schönbrunn Palace": [("wiki", "en:Schönbrunn Palace")],
    "Stephansdom": [("wiki", "en:St. Stephen's Cathedral, Vienna")],
    "Prater & Riesenrad": [("wiki", "en:Wiener Riesenrad")],
    "Film Festival, Rathausplatz": [("commons", "Film Festival Rathausplatz Wien"), ("wiki", "en:Vienna City Hall")],
    "Lokál Dlouhá": [("commons", "Lokál Dlouhá"), ("commons", "Lokál Praha restaurace"), ("commons", "Dlouhá 33 Praha")],
    "Kantýna": [("commons", "Svíčková na smetaně"), ("commons", "Czech pork knuckle")],
    "Field": [("site", "https://www.fieldrestaurant.cz"), ("commons", "Field restaurant Prague")],
    "Café Savoy": [("commons", "Café Savoy Praha"), ("site", "https://cafesavoy.ambi.cz")],
    "Charles Bridge": [("wiki", "en:Charles Bridge")],
    "Prague Castle": [("wiki", "en:Prague Castle")],
    "Old Town Square": [("wiki", "en:Old Town Square")],
    "Letná Beer Garden": [("commons", "Letná beer garden"), ("wiki", "en:Letná Park")],
    "Augustiner-Keller": [("wiki", "de:Augustiner-Keller"), ("commons", "Augustiner-Keller München"), ("commons", "Augustinerkeller Biergarten"), ("commons", "Augustinerkeller München Biergarten"), ("site", "https://www.augustinerkeller.de")],
    "Schneider Bräuhaus": [("wiki", "de:Weisses Bräuhaus (München)"), ("commons", "Weisses Bräuhaus Tal München"), ("site", "https://www.schneider-brauhaus.de")],
    "Tantris": [("wiki", "en:Tantris"), ("commons", "Tantris München"), ("site", "https://tantris.de")],
    "Café Frischhut": [("commons", "Schmalznudel Café Frischhut"), ("commons", "Schmalznudel")],
    "Marienplatz & Viktualienmarkt": [("wiki", "en:Marienplatz")],
    "Englischer Garten & Eisbach": [("commons", "Eisbachwelle Surfer Sommer"), ("commons", "Eisbach surfing Munich"), ("wiki", "en:Eisbach (Isar)")],
    "Nymphenburg Palace": [("wiki", "en:Nymphenburg Palace")],
    "Neuschwanstein Castle": [("wiki", "en:Neuschwanstein Castle")],
    "Marienbrücke": [("commons", "Marienbrücke Neuschwanstein Blick"), ("wiki", "de:Marienbrücke (Schwangau)")],
    "Hohenschwangau Castle": [("wiki", "en:Hohenschwangau Castle")],
    "Dürnstein Abbey": [('commons', 'Stift Dürnstein Turm'), ('wiki', 'de:Stift Dürnstein'), ('wiki', 'en:Dürnstein Abbey')],
    "Kuenringer Castle ruin": [('wiki', 'de:Burgruine Dürnstein'), ('commons', 'Burgruine Dürnstein')],
    "Weißenkirchen": [('wiki', 'en:Weißenkirchen in der Wachau')],
    "Krems old town": [('wiki', 'en:Krems an der Donau')],
    "Kahlenberg viewpoint": [('commons', 'Blick vom Kahlenberg auf Wien')],
    "Leopoldsberg": [('wiki', 'en:Leopoldsberg')],
    "Kahlenbergerdorf": [('commons', 'Kahlenbergerdorf Wien'), ('wiki', 'de:Kahlenbergerdorf')],
    "Grinzing": [('wiki', 'en:Grinzing')],
    "Marktplatz": [('wiki', 'en:Hallstatt')],
    "Skywalk World Heritage View": [('commons', 'Welterbeblick Hallstatt'), ('commons', 'Hallstatt Skywalk')],
    "Salt mine": [('wiki', 'en:Hallstatt Salt Mine'), ('commons', 'Salzwelten Hallstatt')],
    "Bone House": [('commons', 'Beinhaus Hallstatt')],
    "Karlštejn village": [('commons', 'Karlštejn obec náměstí')],
    "Velká Amerika quarry": [('wiki', 'en:Velká Amerika')],
    "Sedlec Ossuary": [('wiki', 'en:Sedlec Ossuary')],
    "St. Barbara's Church": [('wiki', "en:St. Barbara's Church, Kutná Hora")],
    "Italian Court": [('commons', 'Vlašský dvůr Kutná Hora'), ('wiki', 'cs:Vlašský dvůr'), ('wiki', 'en:Italian Court (Kutná Hora)')],
    "Sedlec Cathedral": [('wiki', 'en:Church of the Assumption of Our Lady and Saint John the Baptist')],
    "Pravčická brána": [('wiki', 'en:Pravčická brána')],
    "Edmund's Gorge boats": [('commons', 'Edmundova soutěska'), ('wiki', 'cs:Edmundova soutěska')],
    "Hřensko": [('wiki', 'en:Hřensko')],
    "Mezní Louka": [('commons', 'Mezní Louka')],
    "Starnberg lakefront": [('wiki', 'en:Starnberg')],
    "Roseninsel": [('wiki', 'en:Roseninsel')],
    "Possenhofen Castle": [('wiki', 'en:Possenhofen Castle')],
    "Votive Chapel, Berg": [('commons', 'Votivkapelle Berg Starnberger See')],
    "Olympic Ski Stadium": [('wiki', 'en:Große Olympiaschanze')],
    "Eckbauer cable car": [('commons', 'Eckbauerbahn')],
    "Ludwigstraße, Partenkirchen": [('commons', 'Ludwigstraße Partenkirchen Lüftlmalerei')],
    "Karlštejn Castle": [("wiki", "en:Karlštejn")],
    "Alpsee": [("wiki", "en:Alpsee (Schwangau)"), ("commons", "Alpsee Schwangau")],
    "Hallstatt & Salzkammergut": [("site", "https://www.getyourguide.com/vienna-l7/from-vienna-full-day-tour-to-salzkammergut-hallstatt-t68367/"), ("wiki", "en:Hallstatt")],
    "Wachau Valley": [("wiki", "en:Dürnstein"), ("wiki", "en:Wachau")],
    "Kahlenberg & Vienna Woods": [("commons", "Kahlenberg Wien Aussicht"), ("commons", "Kahlenbergerdorf Weinberge"), ("commons", "Blick vom Kahlenberg Wien"), ("wiki", "de:Kahlenberg (Wien)"), ("wiki", "en:Kahlenberg")],
    "Lednice–Valtice": [("commons", "Lednice zámek park"), ("wiki", "cs:Lednicko-valtický areál"), ("wiki", "en:Lednice–Valtice Cultural Landscape")],
    "Karlštejn Castle": [("wiki", "en:Karlštejn")],
    "Kutná Hora": [("wiki", "en:Sedlec Ossuary"), ("wiki", "en:Kutná Hora")],
    "Bohemian Switzerland": [("wiki", "en:Pravčická brána"), ("wiki", "en:Bohemian Switzerland")],
    "Lake Starnberg": [("wiki", "de:Starnberger See"), ("commons", "Starnberger See Alpenpanorama"), ("wiki", "de:Starnberger See"), ("wiki", "en:Lake Starnberg")],
    "Neuschwanstein": [("wiki", "en:Neuschwanstein Castle")],
    "Partnach Gorge": [("wiki", "en:Partnach Gorge"), ("commons", "Partnachklamm")],
    "Salzburg": [("wiki", "en:Salzburg")],
    "Lednice Chateau": [("commons", "Zámek Lednice"), ("wiki", "cs:Lednice (zámek)"), ("wiki", "en:Lednice Castle")],
    "The Minaret": [("commons", "Minaret v Lednici"), ("commons", "Lednice minaret park"), ("wiki", "cs:Minaret (Lednice)"), ("wiki", "en:Minaret (Lednice)"), ("commons", "Minaret Lednice exterior")],
    "Valtice Chateau & wine salon": [("wiki", "en:Valtice Castle"), ("commons", "Zámek Valtice")],
    "Colonnade on Reistna": [("wiki", "cs:Kolonáda na Reistně"), ("commons", "Kolonáda na Reistně")],
    "Getreidegasse & Mozart's birthplace": [("commons", "Getreidegasse Salzburg"), ("commons", "Mozarts Geburtshaus Salzburg"), ("wiki", "de:Getreidegasse"), ("wiki", "en:Getreidegasse")],
    "Hohensalzburg Fortress": [("wiki", "en:Hohensalzburg Fortress")],
    "Mirabell Gardens": [("wiki", "en:Mirabell Palace")],
    "Augustiner Bräu Mülln": [("commons", "Augustiner Bräu Mülln"), ("wiki", "de:Augustiner Bräu Kloster Mülln")],
}


def api(url, params):
    """Wikimedia API call with polite pacing and retries (it rate-limits bursts)."""
    tries = 1 if os.environ.get("FAST") else 5  # FAST=1: one quick try, skip the long rate-limit backoff
    for attempt in range(tries):
        time.sleep(1.0 + attempt * 4)
        r = requests.get(url, headers=UA, timeout=30, params=params)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
    raise RuntimeError(f"API kept failing: HTTP {r.status_code}")


def commons_info(file_title):
    data = api("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "titles": file_title, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime", "iiurlwidth": 1000})
    pages = data["query"]["pages"]
    page = next(iter(pages.values()))
    if "imageinfo" not in page:
        return None  # not on Commons (e.g. a non-free local file)
    ii = page["imageinfo"][0]
    if ii.get("mime") not in ("image/jpeg", "image/png"):
        return None
    md = ii.get("extmetadata", {})
    artist = re.sub(r"<[^>]+>", "", html.unescape(md.get("Artist", {}).get("value", ""))).strip()
    lic = md.get("LicenseShortName", {}).get("value", "")
    return {"src": ii.get("thumburl") or ii["url"], "credit": f"{artist or 'Unknown'} · {lic} · Wikimedia Commons".strip(),
            "link": ii.get("descriptionurl")}


def from_wiki(spec):
    lang, title = spec.split(":", 1)
    data = api(f"https://{lang}.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "titles": title, "prop": "pageimages", "piprop": "name", "redirects": 1})
    page = next(iter(data["query"]["pages"].values()))
    name = page.get("pageimage")
    return commons_info(f"File:{name}") if name else None


def from_commons(q):
    data = api("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "list": "search", "srsearch": f"{q} filetype:bitmap", "srnamespace": 6, "srlimit": 5})
    for hit in data["query"]["search"]:
        info = commons_info(hit["title"])
        if info:
            return info
    return None


def from_site(url):
    r = requests.get(url, headers=BROWSER_UA, timeout=30)
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text)
    if not m:
        return None
    src = html.unescape(m.group(1))
    if src.startswith("/"):
        src = url.rstrip("/") + src
    domain = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return {"src": src, "credit": f"Photo: {domain}", "link": url}


def from_img(url):
    """A photo the user picked. DuckDuckGo image-proxy links carry the original in their u= parameter."""
    from urllib.parse import urlsplit, parse_qs
    q = parse_qs(urlsplit(url).query)
    original = q["u"][0] if "duckduckgo.com" in url and "u" in q else url
    domain = re.sub(r"^www\.", "", urlsplit(original).netloc)
    for src in (original, url):
        try:
            r = requests.get(src, headers=BROWSER_UA, timeout=60)
            r.raise_for_status()
            Image.open(io.BytesIO(r.content))  # make sure it really is an image
            return {"src": src, "credit": f"Photo: {domain}", "link": original, "data": r.content}
        except Exception as e:
            print("   ", src[:60], "failed:", e)
    return None


FINDERS = {"wiki": from_wiki, "commons": from_commons, "site": from_site, "img": from_img}

# photos the user supplied: always win over anything found automatically
USER = {
    "Stephansdom": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fimages-wixmp-ed30a86b8c4ca887773594c2.wixmp.com%2Ff%2F9968a872-fd9f-494c-8015-0b6525e73f18%2Fdh11fo3-79f28317-8337-41c5-8530-97e4b5c0c1e7.jpg%2Fv1%2Ffill%2Fw_1095%2Ch_730%2Cq_70%2Cstrp%2Fstephansdom_square_by_rikitza_dh11fo3-pre.jpg%3Ftoken%3DeyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1cm46YXBwOjdlMGQxODg5ODIyNjQzNzNhNWYwZDQxNWVhMGQyNmUwIiwiaXNzIjoidXJuOmFwcDo3ZTBkMTg4OTgyMjY0MzczYTVmMGQ0MTVlYTBkMjZlMCIsIm9iaiI6W1t7ImhlaWdodCI6Ijw9NjAwMCIsInBhdGgiOiJcL2ZcLzk5NjhhODcyLWZkOWYtNDk0Yy04MDE1LTBiNjUyNWU3M2YxOFwvZGgxMWZvMy03OWYyODMxNy04MzM3LTQxYzUtODUzMC05N2U0YjVjMGMxZTcuanBnIiwid2lkdGgiOiI8PTkwMDAifV1dLCJhdWQiOlsidXJuOnNlcnZpY2U6aW1hZ2Uub3BlcmF0aW9ucyJdfQ.spmJcM6grBJ5rRip3V1WN02qC6Zk_8pOL_n-DoyOB5g&f=1&nofb=1&ipt=590b201c808f3cfefec2fda951c07ac3be7e561f81a4bb93fe565b6eb19b2d25&ipo=images",
    "Prunksaal, National Library": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Finsideinside.org%2Fwp-content%2Fuploads%2F2014%2F03%2Fvienna-library-11.jpg&f=1&nofb=1&ipt=ef7378c4254eaad68c7fce0f0a6edf9e72217f11266eb7c011732e65201ef3d8&ipo=images",
    "Nymphenburg Palace": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fstorage.kempinski.com%2Fcdn-cgi%2Fimage%2Fw%3D1920%2Cf%3Dauto%2Cg%3Dauto%2Cfit%3Dscale-down%2Fki-cms-prod%2Fimages%2F5%2F1%2F3%2F6%2F18936315-1-eng-GB%2F3df5716d1599-MUC3_0000_Castle.jpg&f=1&nofb=1&ipt=c9e07e0d74c711a9eaa4ee80e01d1a61f9fbe628b73b62518a91cdf1d2488c45&ipo=images",
    "Marienplatz & Viktualienmarkt": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.ratgeber-wissen.com%2Fwp-content%2Fuploads%2F2024%2F02%2FViktualienmarkt-1024x585.jpg&f=1&nofb=1&ipt=9820e58a57c301fbf5fe02e339aeaebe64a163ab501895660c3837d560bc1abc&ipo=images",
    "Partnach Gorge": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.moonhoneytravel.com%2Fwp-content%2Fuploads%2F2022%2F07%2Fmoonhoneytravel_partnach-gorge-trail-2-1024x683.jpg&f=1&nofb=1&ipt=019fa594e3c34037c7a6882fde9a4f326b1a4622240d667c552a39adcad6b0be&ipo=images",
    "Lake Starnberg": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fi.pinimg.com%2Foriginals%2Fa5%2F8a%2Ff1%2Fa58af1d88a6a561b07ab34d674b762f3.jpg&f=1&nofb=1&ipt=2c37741ffc992e5bd08fb0d6e3f6620a841355192e2f8d2ba821f2fe1430a729&ipo=images",
    "Asamkirche": [
        "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Flive.staticflickr.com%2F3306%2F4612992672_154056f8e4_b.jpg&f=1&nofb=1&ipt=9517623f2ac2fec5e7cf04baa38b957f8814532c3fd954f3f04c28a8677ce349&ipo=images",
        "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Flive.staticflickr.com%2F4707%2F39606546434_d9f0d83be6_b.jpg&f=1&nofb=1&ipt=0fe9ffe22422150b5849f04b3d2ed72679634bcf9d0cac65c86038486b906615&ipo=images",
    ],
    "Augustiner Bräu Mülln": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.servus.com%2Fstorage%2Farticle%2Fausfluege-wirtshaus-augustinerbraeu-stockhammersaal.jpg%3Fimpolicy%3Darticle_short_header&f=1&nofb=1&ipt=88c68184b477f594bb09bac7dafdba6272d608307ae8341f684cfc83af8d3eb0&ipo=images",
    "Mirabell Gardens": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwindows10spotlight.com%2Fwp-content%2Fuploads%2F2023%2F02%2F1fa05b134ea6198a6ff44a5c53a147d3.jpg&f=1&nofb=1&ipt=a5ab623cc9d475c3a17f2284ed67b40d88bc347661b3fa6344fb838fc2120d7b&ipo=images",
    "Hohensalzburg Fortress": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.historyhit.com%2Fapp%2Fuploads%2F2020%2F11%2Fhohensalzburg-fortress.jpg&f=1&nofb=1&ipt=1d997da18364e072d126132dec7019e2bdb3c00a64376e0fd6bbeee9acbdec21&ipo=images",
    "Lokál Dlouhááá": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/08/ad/1b/4d/photo0jpg.jpg?w=2000&h=-1&s=1",
    "U Tří jelínků": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/1d/7b/a1/98/caption.jpg?w=1400&h=-1&s=1",
    "Oishi Praha": "https://oishipraha.com/wp-content/uploads/2026/10/catering-20261026-27.webp",
    "Lore Malastrana": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fstatic.wixstatic.com%2Fmedia%2F62a3ca_ab6fa02a2d8e4050a1ea286817ff7ec2~mv2.jpg%2Fv1%2Ffill%2Fw_980%2Ch_653%2Cal_c%2Cq_85%2Cusm_0.66_1.00_0.01%2Cenc_avif%2Cquality_auto%2F62a3ca_ab6fa02a2d8e4050a1ea286817ff7ec2~mv2.jpg&f=1&nofb=1&ipt=8387df08b4d432728d17464f922dccedd9a6301bf6e2eac926730f5333f233ab&ipo=images",
    "Pho Vietnam Tuan & Lan": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/2d/9f/0a/ae/pho-bo-tai.jpg?w=1200&h=1200&s=1",
    "Dom Schnitzel": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fdynamic-media-cdn.tripadvisor.com%2Fmedia%2Fphoto-o%2F2a%2Fb9%2F1c%2F43%2Fwiener-schnitzel.jpg%3Fw%3D800%26h%3D600%26s%3D1&f=1&nofb=1&ipt=4a73697b9da935edd1d47e05627764688346f72088cbe4748792359970cd93f1&ipo=images",
    "Ramen Makotoya": "https://static.wixstatic.com/media/3b7bfd_d6bc0fbca6054e44952f12d6d5236683~mv2.jpg/v1/fill/w_838,h_1682,al_c,q_85,usm_0.66_1.00_0.01,enc_avif,quality_auto/3b7bfd_d6bc0fbca6054e44952f12d6d5236683~mv2.jpg",
    "Lola": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/24/7c/46/1c/caption.jpg?w=1100&h=-1&s=1",
    "Wirtshaus in der Au": "https://wirtshausinderau.de/wp-content/uploads/2026/04/Knoedeltrio-e1727628982726.png",
    "Meraki": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/20/b1/e3/7d/meraki-restaurant.jpg?w=1100&h=-1&s=1",
    "Indian Village": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/2c/c3/7f/c1/food.jpg?w=1100&h=-1&s=1",
    "Belvedere Palace": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2F2gbzzxp7mkrqpgpz.public.blob.vercel-storage.com%2Fvienna-belvedere%2Flong_view_00.webp&f=1&nofb=1&ipt=a0a01c7b9f078754e608d91af4ac37fe58e0c2fce55b696948a7a8635af56e28&ipo=images",
}


def save(src, slug, width=900, data=None):
    raw = RAW / f"{slug}.bin"
    if data:
        raw.write_bytes(data)
    if not raw.exists():
        r = requests.get(src, headers=BROWSER_UA if "wikimedia" not in src else UA, timeout=60)
        r.raise_for_status()
        raw.write_bytes(r.content)
        time.sleep(0.5)
    im = Image.open(io.BytesIO(raw.read_bytes())).convert("RGB")
    im.thumbnail((width, width))
    out = OUT / f"{slug}.jpg"
    im.save(out, "JPEG", quality=74, optimize=True, progressive=True)
    return f"img/{slug}.jpg", im.size


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    places = json.loads((ROOT / "build" / "places.json").read_text())
    stays = json.loads((ROOT / "deck" / "data" / "stays.json").read_text())
    images_path = ROOT / "deck" / "data" / "images.json"
    result = json.loads(images_path.read_text()) if images_path.exists() else {}
    for city, d in places.items():
        items = [("f", i, p) for i, p in enumerate(d.get("food", []))] + ([("c", 0, d["cafe"])] if "cafe" in d else []) \
            + [("s", i, p) for i, p in enumerate(d["sights"])] + [("t", i, p) for i, p in enumerate(d.get("trips", []))]
        for kind, i, p in items:
            slug = f"{city}-{kind}{i}"
            user = USER.get(p["name"])
            user_list = user if isinstance(user, list) else [user] if user else []
            if p["name"] in result and not (user_list and result[p["name"]].get("user") != user_list[0]):
                continue
            if p["name"] in USER:
                (RAW / f"{slug}.bin").unlink() if (RAW / f"{slug}.bin").exists() else None
            info = None
            path = None
            sources = [("img", user_list[0])] if user_list else []
            sources += SOURCES.get(p["name"], [])
            for how, arg in sources:
                try:
                    info = FINDERS[how](arg)
                    if info:
                        path, size = save(info["src"], f"{slug}", data=info.pop("data", None))
                        break
                except Exception as e:  # keep going to the next source
                    print("  ", how, arg, "failed:", e)
                    (RAW / f"{slug}.bin").unlink() if (RAW / f"{slug}.bin").exists() else None
            if not path:
                print("NO IMAGE", p["name"])
                continue
            result[p["name"]] = {"img": path, "credit": info["credit"], "link": info["link"]}
            if how == "img":
                result[p["name"]]["user"] = arg
                extra = []
                for j, u in enumerate(user_list[1:], start=2):
                    more = from_img(u)
                    if more:
                        extra.append(save(more["src"], f"{slug}-{j}", data=more["data"])[0])
                if extra:
                    result[p["name"]]["imgs"] = [path] + extra
            print(f"{p['name']:<32} {how:<8} {size} {info['credit'][:70]}")
    for city, items in stays.items():
        for i, s in enumerate(items):
            room = s["url"].split("/rooms/")[1].split("?")[0]
            page = (ROOT / "data" / "raw" / "airbnb" / f"{room}.html").read_text()
            m = re.search(r'<meta property="og:image" content="([^"]+)"', page)
            if not m:
                print("NO AIRBNB IMAGE", room)
                continue
            path, size = save(html.unescape(m.group(1)), f"{city}-h{i}")
            result[f"airbnb:{room}"] = {"img": path, "credit": "Photo: Airbnb listing", "link": s["url"]}
            print(f"airbnb {city} {room} {size}")
    images_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
