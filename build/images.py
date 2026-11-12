"""Find, download and resize a photo for every place and Airbnb listing.

Sources, tried in order per place: freely licensed Wikimedia Commons images (via a Wikipedia
article or a Commons search), then the venue's own website share image (og:image).
Airbnb listings use the listing's cover photo. Writes deck/data/images.json and, per photo,
three WebP files in deck/img: the popup size (640 px), a small card thumbnail (-t) and a
full-resolution copy for the photo viewer (full/).

Run with: .venv/bin/python -I build/images.py
          .venv/bin/python -I build/images.py --convert   (rebuild the files from the download cache;
                                                           only fetches the large Commons originals)
"""
import html
import io
import os
import json
import pathlib
import re
import sys
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
    # attractions and activities added to each city's Sights slide
    "Vyšehrad": [("wiki", "en:Vyšehrad"), ("commons", "Vyšehrad Praha")],
    "Pedal boats on the Vltava": [("commons", "Slovanský ostrov šlapadla"), ("commons", "Pedal boats Vltava Prague"), ("wiki", "en:Slavonic Island")],
    "Petřín lookout tower": [("wiki", "en:Petřín Lookout Tower"), ("commons", "Petřínská rozhledna")],
    "Náplavka riverbank": [("commons", "Farmářské trhy Náplavka"), ("commons", "Hausbóty Rašínovo nábřeží"), ("commons", "Prague embankment Vltava boats Vyšehrad"), ("commons", "Náplavka Praha")],
    "Prater & the Riesenrad": [("wiki", "en:Wiener Riesenrad")],
    "Naschmarkt": [("wiki", "en:Naschmarkt"), ("commons", "Naschmarkt Wien")],
    "Gänsehäufel swim": [("wiki", "de:Gänsehäufel"), ("commons", "Gänsehäufel")],
    "Film Festival on Rathausplatz": [("commons", "Film Festival Rathausplatz Wien"), ("wiki", "en:Vienna City Hall")],
    "St. Domnius bell tower": [("wiki", "en:Cathedral of Saint Domnius"), ("commons", "Split cathedral bell tower")],
    "Meštrović Gallery": [("commons", "Meštrović Gallery Split villa"), ("commons", "Galerija Meštrović zgrada"), ("wiki", "en:Meštrović Gallery"), ("commons", "Galerija Meštrović Split")],
    "Kayak around Marjan": [("commons", "Kayak Split Marjan"), ("commons", "Kašjuni beach Split"), ("wiki", "hr:Kašjuni")],
    "Figlmüller Wollzeile": [("wiki", "de:Figlmüller"), ("site", "https://figlmueller.at"), ("commons", "Figlmüller Wien")],
    "Café Sperl": [("wiki", "en:Café Sperl"), ("commons", "Café Sperl Wien")],
    "Schönbrunn Palace": [("wiki", "en:Schönbrunn Palace")],
    "Stephansdom": [("wiki", "en:St. Stephen's Cathedral, Vienna")],
    "Café Savoy": [("commons", "Café Savoy Praha"), ("site", "https://cafesavoy.ambi.cz")],
    "Charles Bridge": [("wiki", "en:Charles Bridge")],
    "Prague Castle": [("wiki", "en:Prague Castle")],
    "Old Town Square": [("wiki", "en:Old Town Square")],
    "Letná Beer Garden": [("commons", "Letná beer garden"), ("wiki", "en:Letná Park")],
    "Konoba Fetivi": [("commons", "Varoš Split street"), ("commons", "Split Varoš"), ("wiki", "hr:Varoš (Split)")],
    "Konoba Matejuška": [("commons", "Matejuška Split")],
    "Bokeria Kitchen & Wine": [("commons", "Split old town restaurant"), ("commons", "Dalmatian food octopus salad")],
    "Riva promenade": [("commons", "Riva Split promenade"), ("wiki", "hr:Riva (Split)")],
    "Marjan Hill": [("commons", "Marjan Split view"), ("wiki", "hr:Marjan"), ("wiki", "en:Marjan (Split)")],
    "Bačvice beach": [("wiki", "en:Bačvice"), ("commons", "Bačvice beach Split")],
    "Trogir": [("wiki", "en:Trogir")],
    "Hvar": [("wiki", "en:Hvar (town)")],
    "Krka National Park": [("wiki", "en:Krka National Park")],
    "Cathedral of St. Lawrence": [("wiki", "en:Trogir Cathedral")],
    "Kamerlengo Fortress": [("wiki", "en:Kamerlengo Castle"), ("commons", "Kamerlengo Trogir")],
    "Trogir waterfront": [("commons", "Trogir riva"), ("commons", "Trogir waterfront")],
    "Fortica fortress": [("wiki", "en:Fortica (Hvar)"), ("commons", "Fortica Hvar")],
    "St. Stephen's Square": [("commons", "Trg sv. Stjepana Hvar"), ("commons", "Hvar main square")],
    "Pakleni islands": [("commons", "Pakleni otoci"), ("wiki", "en:Pakleni Islands")],
    "Blue Cave": [("commons", "Modra špilja Biševo"), ("commons", "Blue Cave Biševo"), ("wiki", "en:Biševo")],
    "Stiniva cove": [("commons", "Stiniva Vis"), ("commons", "Stiniva beach")],
    "Komiža": [("wiki", "en:Komiža"), ("commons", "Komiža harbour")],
    "Veliki slap": [("commons", "Veliki slap Plitvice"), ("commons", "Plitvice Big Waterfall")],
    "Lower Lakes boardwalks": [("commons", "Plitvice boardwalk Donja jezera"), ("commons", "Plitvice Lakes boardwalk")],
    "Lake Kozjak boat": [("commons", "Kozjak lake Plitvice boat"), ("commons", "Jezero Kozjak")],
    "Upper Lakes": [("commons", "Plitvice Upper Lakes"), ("commons", "Gornja jezera Plitvice")],
    "Stari Most": [("wiki", "en:Stari Most")],
    "Kujundžiluk": [("commons", "Kujundžiluk Mostar"), ("commons", "Mostar old bazaar")],
    "Koski Mehmed Pasha Mosque": [("wiki", "en:Koski Mehmed Pasha Mosque"), ("commons", "Koski Mehmed Pasha Mosque Mostar")],
    "Crooked Bridge": [("wiki", "en:Crooked Bridge (Mostar)"), ("commons", "Kriva ćuprija Mostar")],
    "Skradinski buk": [("wiki", "en:Skradinski buk"), ("commons", "Skradinski buk")],
    "Skradin": [("wiki", "en:Skradin")],
    "Visovac monastery": [("commons", "Visovac"), ("wiki", "en:Visovac")],
    "Upper Town & St. Mark's Church": [("wiki", "en:St. Mark's Church, Zagreb")],
    "Museum of Broken Relationships": [("commons", "Muzej prekinutih veza"), ("wiki", "en:Museum of Broken Relationships")],
    "Tkalčićeva street": [("commons", "Tkalčićeva Zagreb"), ("wiki", "hr:Tkalčićeva ulica")],
    "La Štruk": [("wiki", "en:Štrukli"), ("commons", "Zagorski štrukli")],
    "Dürnstein Abbey": [('commons', 'Stift Dürnstein Turm'), ('wiki', 'de:Stift Dürnstein'), ('wiki', 'en:Dürnstein Abbey')],
    "Kuenringer Castle ruin": [('wiki', 'de:Burgruine Dürnstein'), ('commons', 'Burgruine Dürnstein')],
    "Weißenkirchen": [('wiki', 'en:Weißenkirchen in der Wachau')],
    "Krems old town": [('wiki', 'en:Krems an der Donau')],
    "Kahlenberg viewpoint": [('commons', 'Blick vom Kahlenberg auf Wien')],
    "Leopoldsberg": [('wiki', 'en:Leopoldsberg')],
    "Kahlenbergerdorf": [('commons', 'Kahlenbergerdorf Wien'), ('wiki', 'de:Kahlenbergerdorf')],
    "Grinzing": [('commons', 'Grinzing Heuriger'), ('commons', 'Grinzing Himmelstraße'), ('wiki', 'en:Grinzing')],
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
    "Karlštejn Castle": [("wiki", "en:Karlštejn")],
    "Hallstatt & Salzkammergut": [("site", "https://www.getyourguide.com/vienna-l7/from-vienna-full-day-tour-to-salzkammergut-hallstatt-t68367/"), ("wiki", "en:Hallstatt")],
    "Wachau Valley": [("wiki", "en:Dürnstein"), ("wiki", "en:Wachau")],
    "Kahlenberg & Vienna Woods": [("commons", "Kahlenberg Wien Aussicht"), ("commons", "Kahlenbergerdorf Weinberge"), ("commons", "Blick vom Kahlenberg Wien"), ("wiki", "de:Kahlenberg (Wien)"), ("wiki", "en:Kahlenberg")],
    "Lednice–Valtice": [("commons", "Lednice zámek park"), ("wiki", "cs:Lednicko-valtický areál"), ("wiki", "en:Lednice–Valtice Cultural Landscape")],
    "Karlštejn Castle": [("wiki", "en:Karlštejn")],
    "Kutná Hora": [("wiki", "en:Sedlec Ossuary"), ("wiki", "en:Kutná Hora")],
    "Bohemian Switzerland": [("wiki", "en:Pravčická brána"), ("wiki", "en:Bohemian Switzerland")],
    "Lednice Chateau": [("commons", "Zámek Lednice"), ("wiki", "cs:Lednice (zámek)"), ("wiki", "en:Lednice Castle")],
    "The Minaret": [("commons", "Minaret v Lednici"), ("commons", "Lednice minaret park"), ("wiki", "cs:Minaret (Lednice)"), ("wiki", "en:Minaret (Lednice)"), ("commons", "Minaret Lednice exterior")],
    "Valtice Chateau & wine salon": [("wiki", "en:Valtice Castle"), ("commons", "Zámek Valtice")],
    "Colonnade on Reistna": [("wiki", "cs:Kolonáda na Reistně"), ("commons", "Kolonáda na Reistně")],
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
    # activities, from data/adtivities2
    "Petřín lookout tower": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.avantgarde-prague.com%2Fmedia%2Ffiler_public_thumbnails%2Ffiler_public%2F65%2Fa4%2F65a4c602-f568-4b42-b3c1-7cc63033f170%2F9_petrin_observation_tower_prague.jpg__1280x1024_q85_subsampling-2.jpg&f=1&nofb=1&ipt=8114b31797bad040871caa1029b164d258ce3dc9c89470c02b0c5cc8558f7276&ipo=images",
    "Náplavka riverbank": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.discoverwalks.com%2Fblog%2Fwp-content%2Fuploads%2F2024%2F01%2Fpexels-helena-jankovicova-kovacova-9460557.jpeg&f=1&nofb=1&ipt=f63e4ed1254d51c48ec9957348ec5f21f2163cffdad7ae1355844ba0e3b9fd18&ipo=images",
    "Prater & the Riesenrad": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.reisroutes.be%2Fuserfiles%2Ffotos%2Friesenrad-in-praterpark_2878_xl.jpg&f=1&nofb=1&ipt=39ef4ea1c737203dae2d7290dc95aa5f2605a52dd03c4dadf70700b962aa4cc7&ipo=images",
    "Gänsehäufel swim": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fviennainsider.com%2Fwp-content%2Fuploads%2F2018%2F08%2Fbad-gan.jpg&f=1&nofb=1&ipt=003ba8eaadf524ed663f6f1432b8954ff4902264350e327e36facda52e3577f5&ipo=images",
    "Film Festival on Rathausplatz": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.vienna.at%2F2019%2F06%2FFilm-Festival-2018_040_cstadtwienmarketing-4-3-180037362802-3736x2802.jpg&f=1&nofb=1&ipt=75071b17fc3a3b85bf07510c8ee0d68bc10883bac625a9ab7a9a693d279a434b&ipo=images",
    "St. Domnius bell tower": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.somtoseeks.com%2Fwp-content%2Fuploads%2F2023%2F01%2FBell-Tower-at-Saint-Domnius-Cathedral-split-croatia-1.jpg&f=1&nofb=1&ipt=90c546464934cb969edf569647b536449dfeece60faaaf1ca62f6731e71a2fc6&ipo=images",
    "Meštrović Gallery": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fgkd.hr%2Fwp-content%2Fuploads%2F2023%2F11%2FIMG_2660-1024x683.jpg&f=1&nofb=1&ipt=e58a65fbc137a99cfc69135027b56df7e5b91c2b585314985b9c9ccc4859b279&ipo=images",
    "Kayak around Marjan": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fcheckyeti.imgix.net%2Fimages%2Fprod%2Fproducts%2F27521%2Fgiven2fly-adventures-split-gallery-1-9&f=1&nofb=1&ipt=7553e01cc13cdb98549c7f5e08bc0c34044182e6733b01abdc2d4797da6cea84&ipo=images",
    "Stephansdom": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fimages-wixmp-ed30a86b8c4ca887773594c2.wixmp.com%2Ff%2F9968a872-fd9f-494c-8015-0b6525e73f18%2Fdh11fo3-79f28317-8337-41c5-8530-97e4b5c0c1e7.jpg%2Fv1%2Ffill%2Fw_1095%2Ch_730%2Cq_70%2Cstrp%2Fstephansdom_square_by_rikitza_dh11fo3-pre.jpg%3Ftoken%3DeyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1cm46YXBwOjdlMGQxODg5ODIyNjQzNzNhNWYwZDQxNWVhMGQyNmUwIiwiaXNzIjoidXJuOmFwcDo3ZTBkMTg4OTgyMjY0MzczYTVmMGQ0MTVlYTBkMjZlMCIsIm9iaiI6W1t7ImhlaWdodCI6Ijw9NjAwMCIsInBhdGgiOiJcL2ZcLzk5NjhhODcyLWZkOWYtNDk0Yy04MDE1LTBiNjUyNWU3M2YxOFwvZGgxMWZvMy03OWYyODMxNy04MzM3LTQxYzUtODUzMC05N2U0YjVjMGMxZTcuanBnIiwid2lkdGgiOiI8PTkwMDAifV1dLCJhdWQiOlsidXJuOnNlcnZpY2U6aW1hZ2Uub3BlcmF0aW9ucyJdfQ.spmJcM6grBJ5rRip3V1WN02qC6Zk_8pOL_n-DoyOB5g&f=1&nofb=1&ipt=590b201c808f3cfefec2fda951c07ac3be7e561f81a4bb93fe565b6eb19b2d25&ipo=images",
    "Prunksaal, National Library": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Finsideinside.org%2Fwp-content%2Fuploads%2F2014%2F03%2Fvienna-library-11.jpg&f=1&nofb=1&ipt=ef7378c4254eaad68c7fce0f0a6edf9e72217f11266eb7c011732e65201ef3d8&ipo=images",
    "Lokál Dlouhááá": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/08/ad/1b/4d/photo0jpg.jpg?w=2000&h=-1&s=1",
    "U Tří jelínků": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/1d/7b/a1/98/caption.jpg?w=1400&h=-1&s=1",
    "Oishi Praha": "https://oishipraha.com/wp-content/uploads/2026/10/catering-20261026-27.webp",
    "Lore Malastrana": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fstatic.wixstatic.com%2Fmedia%2F62a3ca_ab6fa02a2d8e4050a1ea286817ff7ec2~mv2.jpg%2Fv1%2Ffill%2Fw_980%2Ch_653%2Cal_c%2Cq_85%2Cusm_0.66_1.00_0.01%2Cenc_avif%2Cquality_auto%2F62a3ca_ab6fa02a2d8e4050a1ea286817ff7ec2~mv2.jpg&f=1&nofb=1&ipt=8387df08b4d432728d17464f922dccedd9a6301bf6e2eac926730f5333f233ab&ipo=images",
    "Pho Vietnam Tuan & Lan": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/2d/9f/0a/ae/pho-bo-tai.jpg?w=1200&h=1200&s=1",
    "Dom Schnitzel": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fdynamic-media-cdn.tripadvisor.com%2Fmedia%2Fphoto-o%2F2a%2Fb9%2F1c%2F43%2Fwiener-schnitzel.jpg%3Fw%3D800%26h%3D600%26s%3D1&f=1&nofb=1&ipt=4a73697b9da935edd1d47e05627764688346f72088cbe4748792359970cd93f1&ipo=images",
    "Ramen Makotoya": "https://static.wixstatic.com/media/3b7bfd_d6bc0fbca6054e44952f12d6d5236683~mv2.jpg/v1/fill/w_838,h_1682,al_c,q_85,usm_0.66_1.00_0.01,enc_avif,quality_auto/3b7bfd_d6bc0fbca6054e44952f12d6d5236683~mv2.jpg",
    "Lola": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/24/7c/46/1c/caption.jpg?w=1100&h=-1&s=1",
    "Belvedere Palace": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2F2gbzzxp7mkrqpgpz.public.blob.vercel-storage.com%2Fvienna-belvedere%2Flong_view_00.webp&f=1&nofb=1&ipt=a0a01c7b9f078754e608d91af4ac37fe58e0c2fce55b696948a7a8635af56e28&ipo=images",
    # Split, from data/split
    "Dvor": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.frankaboutcroatia.com%2Fwp-content%2Fuploads%2F2021%2F11%2Frestaurant-dvor-split-terrace-900x495.jpg&f=1&nofb=1&ipt=4c0f7b17116146e1c0d7ac5df66cd2689dfb65bcf90eb2effe2094b1dcc89bc3&ipo=images",
    "Zinfandel": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fs.inyourpocket.com%2Fgallery%2F246915.jpg&f=1&nofb=1&ipt=58d453059b7712ba4c73f140a8714180a5c2de24bd851c5638bed794e55a72a8&ipo=images",
    "Konoba Matejuška": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/13/09/7d/0f/grilled-octopus.jpg?w=1600&h=-1&s=1",
    "Konoba Fetivi": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/14/d0/c8/fb/seafood-for-two.jpg?w=1000&h=-1&s=1",
    "Artičok": "https://cdn.website.dish.co/media/1d/9a/8524671/ARTiCOK-441520000-1180562729582764-6602224282065818664-n-jpg.jpg",
    "Marjan Hill": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.roadaffair.com%2Fwp-content%2Fuploads%2F2017%2F09%2Fmarjan-hill-split-croatia-shutterstock_495329557.jpg&f=1&nofb=1&ipt=d70cc30d6ce4be7409456d81d8eda7e5a8f355b3c99af0aa499071654e368192&ipo=images",
    "Peristyle": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.earthtrekkers.com%2Fwp-content%2Fuploads%2F2017%2F06%2FPeristyle-Split.jpg.optimal.jpg&f=1&nofb=1&ipt=014eab9b02d214b5a55689bcf96429be258f64f9d195a16be978b36cdba0d29e&ipo=images",
    "Podrum": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fthepurposelylost.com%2Fwp-content%2Fuploads%2F2021%2F08%2F20210725-dsc_0821_optimized-1024x683.jpg&f=1&nofb=1&ipt=12f355aebb25e64920a70ae462cc5992f69c748df9bd0e9bc677a723fa0927f3&ipo=images",
    "Riva promenade": "https://www.roadaffair.com/wp-content/uploads/2019/02/riva-promenade-split-croatia-shutterstock_1223005273.jpg",
    "Blue Cave, Biševo": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.travelersuniverse.com%2Fwp-content%2Fuploads%2F2025%2F08%2Fsplit-trogir-blue-cave-mamma-mia-hvar-and-5-island-tour.jpg&f=1&nofb=1&ipt=ffa441acd02ccd4870712e0da3cfa2fc20c7d89279f2a858a3f2dc8c8fdb7b95&ipo=images",
    "Plitvice Lakes": "https://www.roadaffair.com/wp-content/uploads/2017/09/plitvice-lakes-national-park-croatia-shutterstock_528172540.jpg",
    "Mostar": "https://www.roadaffair.com/wp-content/uploads/2017/09/mostar-bosnia-shutterstock_291941039.jpg",
    "Pakleni islands": "https://trogir-excursions.com/wp-content/uploads/2023/11/Pakleni-Islands-eplit-excursions-1.webp",
    # Zagreb
    "Sljeme by gondola": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fsljeme360.hr%2Fwp-content%2Fuploads%2F2023%2F04%2FSljeme360_Naslovna.jpg&f=1&nofb=1&ipt=ae862866e7cb04a2ac8a56c6d062c4ef1fb70d3b6d287324d1e9449eacc2a201&ipo=images",
    "Museum of Broken Relationships": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.benediktwyss.com%2Fsite%2Fassets%2Ffiles%2F1150%2Fmobr_banner_header_2000x0.jpg&f=1&nofb=1&ipt=8224473db64b81df81ece0128665bb641665b2ec95b8fa30dccdfd40ee9144e7&ipo=images",
    "Lanterna na Dolcu": [
        "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Ftse1.mm.bing.net%2Fth%2Fid%2FOIP.1LcmduGRngrF9ZpCTHQbSQHaE8%3Fpid%3DApi&f=1&ipt=5b52170b5245e914a674ea3ec3be9255e88a1a41a05df568d5ec1a7b8a5cbd97&ipo=images",
        "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/1b/ed/d2/0e/img-20200902-wa0000-largejpg.jpg?w=1400&h=-1&s=1",
    ],
    # day-trip zoom-ins, from data/new images
    "Kuenringer Castle ruin": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fc.pxhere.com%2Fphotos%2F07%2F4e%2Fcastle_ruins_mountains_snow_forest_trees_woods_architecture-1252291.jpg!d&f=1&nofb=1&ipt=f08cb8b1eb0f0791fda52e27b46c342ffd2c321bf6ef081053f3eef1d288674a&ipo=images",
    "Dürnstein Abbey": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fassets.artworkarchive.com%2Fimage%2Fupload%2Ft_jpg_large%2Fv1%2Fuser_119824%2FDay_13_Wachau_River_Valley_on_the_Danube50-Edit_pla2ji&f=1&nofb=1&ipt=9856c94b21fdef3bdec77cf2c7d2932b0c205db5a97a33b1e75f0d087492482e&ipo=images",
    "Kahlenberg viewpoint": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fthumbs.dreamstime.com%2Fb%2Fview-kahlenberg-hill-vienna-cityscape-tourist-spot-view-kahlenberg-hill-vienna-cityscape-tourist-spot-137503811.jpg&f=1&nofb=1&ipt=22b939ee7d43385dd70ec575644488be1f0499ed494f3aa865068fa0d900d8d4&ipo=images",
    "Kahlenberg & Vienna Woods": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fthumbs.dreamstime.com%2Fb%2Fview-kahlenberg-hill-vienna-cityscape-tourist-spot-view-kahlenberg-hill-vienna-cityscape-tourist-spot-137503811.jpg&f=1&nofb=1&ipt=22b939ee7d43385dd70ec575644488be1f0499ed494f3aa865068fa0d900d8d4&ipo=images",
    "Leopoldsberg": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Flive.staticflickr.com%2F65535%2F52338723319_e18452871f_b.jpg&f=1&nofb=1&ipt=3e1f5da9a22714f58d465ec0df8b5c8c06c56af4aebdd61e85f472688e723333&ipo=images",
    "Kahlenbergerdorf": "https://media04.meinbezirk.at/article/2016/08/13/9/9029979_XXL.jpg",
    "Grinzing": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.gorealtravel.com%2Fblog%2Fwp-content%2Fuploads%2F2024%2F09%2FGrinzing-blog.jpeg&f=1&nofb=1&ipt=3621a379019530aee0907a6cc839a520c78afa8e9d57e7b24bc9cb2b891b2f62&ipo=images",
    "Skywalk World Heritage View": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fwww.hallstattaustria.eu%2Fwp-content%2Fuploads%2F2025%2F04%2FHallstatt-funicular-tickets-World-Heritage-view-Skywalk-1.jpeg&f=1&nofb=1&ipt=aafda10a800313f49c3e255307f4051cb62be27a69cebd55b54eb62d70686806&ipo=images",
    "Salt mine": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2F1.bp.blogspot.com%2F-XUZFHbg8PY4%2FXi3z3x5rlBI%2FAAAAAAAApxs%2FbTqYKOfVZU8cIcdEM4NK1fsk3E49duYuwCLcBGAsYHQ%2Fs1600%2Fhallstatt_salt.jpg&f=1&nofb=1&ipt=e0f2bf6295717ab1d61db6058e72e7ded270b103a68e559bf1f4887408caf0e4&ipo=images",
    "Bone House": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fcdn.bubblyliving.com%2Fwp-content%2Fuploads%2F2026%2F04%2Fhallstatt-lake-view-from-cemetery.webp&f=1&nofb=1&ipt=4753ccaa5e739d5082bea13576caff47f5aba495726c64904e967e38c715abf3&ipo=images",
    "Italian Court": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F8%2F82%2FKutn%25C3%25A1_Hora%252C_Italian_Court_-_panoramio_(3).jpg%2F960px-Kutn%25C3%25A1_Hora%252C_Italian_Court_-_panoramio_(3).jpg&f=1&nofb=1&ipt=eeaee0bb606b5c22f470bdaf0df28d76045baf504ba31d0c0da2c739abf5b6dd&ipo=images",
    "Visovac monastery": "https://www.visovac.com/media/Destinacije/visovac1.jpg",
    "Fortica fortress": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTlsQBeDg2RgnKDk68k4mL1mIfh8WFrVhFQH0yMAj8avVF5MEyDPhpbEJOW&s=10",
    "St. Stephen's Square": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fimages.pexels.com%2Fphotos%2F18191218%2Fpexels-photo-18191218%2Ffree-photo-of-square-near-st-stephens-basilica-in-budapest.jpeg%3Fauto%3Dcompress%26w%3D1260%26h%3D750%26dpr%3D1&f=1&nofb=1&ipt=246afe714bf45c9f57e6e736260ef03d5a7f59961cbde9a7725b20a0761a9175&ipo=images",
    "Cathedral of St. Lawrence": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQLjdVMrWQVuIMGVAacUAqA3UdrLfsP2t8f3ksCqnB9kFh4BT7dtQwfoaI&s=10",
    "Karlštejn village": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Flive.staticflickr.com%2F4335%2F35957867040_c6160e6929_h.jpg&f=1&nofb=1&ipt=96343cd51d2881c1bd8431184477d5f44e3f757bfa2862c2443f63ca47d6ca59&ipo=images",
    "Trogir waterfront": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fmedia02.stockfood.com%2Flargepreviews%2FMjIwMjA3OTE0MQ%3D%3D%2F71034811-Trogir-waterfront-Obala-Bana-Berislavica-Trogir-UNESCO-World-Heritage-Site-Dalmatian-Coast-Adriatic-Croatia.jpg&f=1&nofb=1&ipt=dcae57306303ea28dfcdb6d9a318f4208724fe6638e10c8b17bd4618b5cb19f6&ipo=images",
}
# Airbnb listings with a chosen photo instead of the listing's cover (by room id)
AIRBNB_USER = {
    "914891070797797327": "https://a0.muscache.com/im/pictures/prohost-api/Hosting-914891070797797327/original/1e1b2e72-68fc-4289-b04c-64b200bac512.jpeg?im_w=2560",
    "873190681059415934": "https://a0.muscache.com/im/pictures/miso/Hosting-592348080858430344/original/da95389d-8d2d-40cd-b816-e447bd34789c.jpeg?im_w=2560",
    "877181797527454382": "https://a0.muscache.com/im/pictures/hosting/Hosting-877181797527454382/original/0db73e6d-0ac7-43ea-bf43-45e910171cdb.jpeg?im_w=2560",
}


FULL_W, VIEW_W, THUMB_SHORT = 1920, 640, 200  # 1920: a Commons standard thumbnail size; 640 fills the popup at 2x


def full_raw(slug, link):
    """The download cache holds Commons photos at 1000 px; the viewer wants the larger original."""
    big = RAW / f"{slug}.full.bin"
    if not big.exists() and link and "commons.wikimedia.org/wiki/File:" in link:
        from urllib.parse import unquote
        title = unquote(link.split("/wiki/", 1)[1])
        data = api("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "format": "json", "titles": title, "prop": "imageinfo", "iiprop": "url", "iiurlwidth": FULL_W})
        ii = next(iter(data["query"]["pages"].values()))["imageinfo"][0]
        for attempt in range(4):  # the image servers rate-limit bursts: back off and retry
            time.sleep(2 + attempt * 10)
            r = requests.get(ii.get("thumburl") or ii["url"], headers=UA, timeout=60)
            if r.status_code != 429:
                break
        r.raise_for_status()
        big.write_bytes(r.content)
    return big if big.exists() else RAW / f"{slug}.bin"


def derive(slug, link=None):
    """Write the popup, thumbnail and viewer copies of one cached photo; returns their deck paths."""
    im = Image.open(io.BytesIO((RAW / f"{slug}.bin").read_bytes())).convert("RGB")
    view = im.copy(); view.thumbnail((VIEW_W, VIEW_W))
    view.save(OUT / f"{slug}.webp", "WEBP", quality=78, method=6)
    k = THUMB_SHORT / min(im.size)
    thumb = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS) if k < 1 else im.copy()
    thumb.save(OUT / f"{slug}-t.webp", "WEBP", quality=74, method=6)
    try:
        src = full_raw(slug, link)
    except Exception as e:  # keep the cached copy if the large original won't download
        print("   full-res", slug, "failed:", e)
        src = RAW / f"{slug}.bin"
    full = Image.open(io.BytesIO(src.read_bytes())).convert("RGB")
    full.thumbnail((FULL_W, FULL_W))
    (OUT / "full").mkdir(exist_ok=True)
    full.save(OUT / "full" / f"{slug}.webp", "WEBP", quality=74, method=6)
    return {"img": f"img/{slug}.webp", "thumb": f"img/{slug}-t.webp", "full": f"img/full/{slug}.webp"}, view.size


def save(src, slug, data=None, link=None):
    raw = RAW / f"{slug}.bin"
    if data:
        raw.write_bytes(data)
    if not raw.exists():
        r = requests.get(src, headers=BROWSER_UA if "wikimedia" not in src else UA, timeout=60)
        r.raise_for_status()
        raw.write_bytes(r.content)
        time.sleep(0.5)
    return derive(slug, link)


def slug_of(path):
    return path.split("/")[-1].rsplit(".", 1)[0]


def convert():
    """Rebuild every photo file from the download cache and drop the old JPEGs."""
    images_path = ROOT / "deck" / "data" / "images.json"
    result = json.loads(images_path.read_text())
    for name, e in result.items():
        paths = e.get("imgs") or [e["img"]]
        files = [derive(slug_of(u), e.get("link") if j == 0 else None)[0] for j, u in enumerate(paths)]
        e.update(files[0])
        if len(files) > 1:
            e["imgs"] = [f["img"] for f in files]
            e["fulls"] = [f["full"] for f in files]
        print(f"{name:<32} ok")
    images_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    for old in OUT.glob("*.jpg"):
        old.unlink()


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
            if p["name"] in USER:  # a chosen photo replaces both cached copies, the large original too
                for old in (RAW / f"{slug}.bin", RAW / f"{slug}.full.bin"):
                    old.unlink() if old.exists() else None
            info = None
            path = None
            sources = [("img", user_list[0])] if user_list else []
            sources += SOURCES.get(p["name"], [])
            for how, arg in sources:
                try:
                    info = FINDERS[how](arg)
                    if info:
                        files, size = save(info["src"], f"{slug}", data=info.pop("data", None), link=info["link"])
                        path = files["img"]
                        break
                except Exception as e:  # keep going to the next source
                    print("  ", how, arg, "failed:", e)
                    (RAW / f"{slug}.bin").unlink() if (RAW / f"{slug}.bin").exists() else None
            if not path:
                print("NO IMAGE", p["name"])
                continue
            result[p["name"]] = {**files, "credit": info["credit"], "link": info["link"]}
            if how == "img":
                result[p["name"]]["user"] = arg
                extra = []
                for j, u in enumerate(user_list[1:], start=2):
                    more = from_img(u)
                    if more:
                        extra.append(save(more["src"], f"{slug}-{j}", data=more["data"])[0])
                if extra:
                    result[p["name"]]["imgs"] = [path] + [f["img"] for f in extra]
                    result[p["name"]]["fulls"] = [files["full"]] + [f["full"] for f in extra]
            print(f"{p['name']:<32} {how:<8} {size} {info['credit'][:70]}")
    for city, items in stays.items():
        if city not in places:  # a city not on this trip needs no photos
            continue
        for i, s in enumerate(items):
            room = s["url"].split("/rooms/")[1].split("?")[0]
            page = (ROOT / "data" / "raw" / "airbnb" / f"{room}.html").read_text()
            m = re.search(r'<meta property="og:image" content="([^"]+)"', page)
            src = AIRBNB_USER.get(room) or (html.unescape(m.group(1)) if m else None)
            if not src:
                print("NO AIRBNB IMAGE", room)
                continue
            files, size = save(src, f"{city}-h{i}")
            result[f"airbnb:{room}"] = {**files, "credit": "Photo: Airbnb listing", "link": s["url"]}
            print(f"airbnb {city} {room} {size}")
    images_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    convert() if "--convert" in sys.argv else main()
