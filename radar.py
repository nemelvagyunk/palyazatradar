#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pályázatradar – napi pályázatfigyelő
Forrásoldalakat tölt le, kigyűjti a pályázati linkeket, összeveti az
állapotfájllal (allapot.json), és jelenti az új kiírásokat (report.md).

Használat:
    python radar.py [--state allapot.json] [--report report.md]
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfiguráció
# ---------------------------------------------------------------------------

# palyazat.gov.hu közlemények: a /kozlemenyek oldal Next.js-es (a lista nincs
# benne a nyers HTML-ben), de mögötte nyílt JSON API van MÁSIK hoszton — ezért
# van esély rá, hogy a palyazat.gov.hu-ra érvényes geo-blokk ezt nem érinti.
KOZLEMENY_API = "https://ginapp-api.fair.gov.hu/api/announcements"
KOZLEMENY_TIPUS = "megjelenes"      # csak új kiírás; a módosulás/karbantartás nem
KOZLEMENY_HATAR = "2026-04-12"      # ennél régebbi közleményt nem veszünk be
KOZLEMENY_OLDAL = 100               # tételszám laponként (limit)
KOZLEMENY_MAX_OLDAL = 30            # biztonsági fék a lapozásra
KOZLEMENY_LINK = "https://www.palyazat.gov.hu/kozlemenyek/"

FORRASOK = [
    {
        # A palyazat.norvegcivilalap.hu főoldala helyett (2026-08-12) a hivatalos
        # felhívás-lista. Drupal-lista: <li><article class="node--type-call">
        # <h3><a href="/hu/civil-society-fund-hungary/calls/<slug>">…</a></h3>,
        # a kártyán a státusz is ott van („Nyitott" / „Lezárt").
        # Az útvonal-előtag már garantálja, hogy felhívás → kulcsszó-szűrés ki.
        "nev": "Norvég Civil Alap",
        "urls": [
            "https://eeagrants.org/hu/civil-society-fund-hungary/calls",
            "https://eeagrants.org/hu/civil-society-fund-hungary/news",
            "https://palyazat.norvegcivilalap.hu/",
        ],
        "kinek": "egyesület",
        # Csak az eeagrants-oldalakra van útvonal-szabály (a felhívás- és a
        # híraloldalak útvonala egyértelmű); a pályázati portál linkjeire
        # marad a kulcsszó-szűrés, ott nincs ilyen tiszta útvonal.
        "utvonal_elotag": {"eeagrants.org": (
            "/hu/civil-society-fund-hungary/calls/",
            "/hu/civil-society-fund-hungary/news/",
        )},
    },
    {
        "nev": "Hangfoglaló",
        "urls": [
            "https://hangfoglalo.hu/aktualis-felhivasok",
            "https://hangfoglalo.hu/aktualis",
        ],
        "kinek": "klubtámogatás",
    },
    {
        # 2026-08-12: a gyűjtő listaoldal helyett a hírkategória (csak a
        # pályázati hírek) + a 10 kollégiumi aloldal külön-külön. Saját
        # parser mindkét oldaltípusra, lásd nka_tetelek().
        "nev": "NKA kollégiumi felhívások",
        "urls": [
            "https://nka.hu/kategoria/kiemelt-kategoriak/hirek/",
        ] + [
            "https://nka.hu/kiemelt-kategoriak/palyaztatas/kollegiumok-felhivasai/"
            f"{k}/" for k in (
                "anyanyelvi-kultura-kollegiuma",
                "eloado-muveszetek-kollegiuma",
                "epitett-orokseg-kollegiuma",
                "hagyomany-es-ismeretatadas-kollegiuma",
                "kozossegi-programok-es-fesztivalok-kollegiuma",
                "kozgyujtemenyek-kollegiuma-2",
                "vizualis-muveszetek-kollegiuma-2",
                "halmos-bela-program-kollegium",
                "hangfoglalo-konnyuzene-tamogato-program-kollegiuma",
                "kiemelt-kulturalis-programok-ideiglenes-kollegiuma",
            )
        ],
        "kinek": "egyesület",
        "special": "nka",
    },
    {
        # 2026-08-12: két alapoldal, semmi más. A hírek a fő tartalomban ÉS a
        # jobb oldali „AKTUÁLIS" dobozban (sidebar-right) is megjelennek —
        # ugyanabban a HTML-ben vannak, ezért külön kezelés nélkül bejönnek.
        # csak_gyoker: a valódi cikkek egy szegmensű, kötőjeles slugon ülnek
        # (/megjelent-a-varosi-civil-alap-2026-evi-palyazati-kiirasa/), míg az
        # ÁSZF/GYIK/útmutató-navigáció /tamogatasok/… alatt — az kiesik.
        "nev": "Bethlen Gábor Alapkezelő (NEA, Városi Civil Alap)",
        "urls": [
            "https://bgazrt.hu/tamogatasok/nemzeti-egyuttmukodesi-alap/",
            "https://bgazrt.hu/tamogatasok/varosi-civil-alap/",
        ],
        "kinek": "egyesület",
        "csak_gyoker": True,
    },
    {
        # 2026-08-12: a hírfolyam is (a beadási határidőket ott hirdetik meg).
        # Útvonal-szabály nem kell: a hírek /erasmus_hirek/<slug> alatt vannak,
        # a menü „Pályázati lehetőségek" linkjei pedig épp a másik forrás-URL-re
        # mutatnak, amit a lista_urlek amúgy is kizár.
        "nev": "Erasmus+ / ESC",
        "urls": [
            "https://erasmusplusz.hu/palyazati-lehetosegek-az-erasmus-programban",
            "https://erasmusplusz.hu/erasmus_hirek",
        ],
        "kinek": "egyesület",
    },
    {
        "nev": "Visegrádi Alap",
        "urls": [
            "https://www.visegradfund.org/grants",
            "https://www.visegradfund.org/",
        ],
        "kinek": "egyesület",
    },
    {
        # iso-8859-2 kódolású oldal – a fetch() kezeli.
        # 2026-08-12: a 45 tételből 42 rendben volt (jó cím, kiolvasott
        # határidő), a 3 zavaró mind KÜLSŐ hivatkozás volt (ec.europa.eu
        # árfolyamtábla, CulturEU útmutató) → elég a saját domainre szűrni.
        # Szándékosan nem útvonal-előtaggal (/palyazatok/), mert az élő
        # oldalt most nem tudtam ellenőrizni, és az némán elnyelné az esetleg
        # más útvonalon megjelenő kiírásokat is.
        "nev": "Kreatív Európa Kultúra",
        "urls": ["https://kultura.kreativeuropa.hu/kategoria/palyazatok"],
        "kinek": "egyesület",
        "csak_sajat_domain": True,
    },
    {
        # A palyazat.gov.hu JS-alapú, géppel nem olvasható – ez az aggregátor a
        # proxy. A hivatalos részletek mindig a palyazat.gov.hu-n!
        # 2026-08-12: a korábbi három külön palyazatok.org-forrás (KKV/energetika,
        # Civil, Kulturális/művészeti) EGYBE olvasztva — ugyanaz az oldal, ugyanaz
        # a szerkezet, csak más kategória-aloldalak. A „kinek" ezért vegyes.
        "nev": "palyazatok.org",
        "urls": ["https://palyazatok.org/"],
        "kinek": "kft + egyesület",
        "special": "palyazatok_org",
    },
    {
        # A /kozlemenyek oldal Next.js-es (a lista nincs a nyers HTML-ben),
        # de a mögötte lévő JSON API kulcs nélkül olvasható, és MÁSIK hoszton
        # van, mint a geo-blokkolt palyazat.gov.hu. Saját parser + lapozás:
        # lásd kozlemeny_tetelek(). Ha a runner mégsem éri el, a forrás a
        # riport „Nem elérhető forrás(ok)" sorába kerül, egyéb kára nincs.
        "nev": "palyazat.gov.hu közlemények",
        "urls": [f"{KOZLEMENY_API}?limit={KOZLEMENY_OLDAL}&skip=0"],
        "kinek": "kft + egyesület",
        "special": "kozlemeny",
    },
    {
        # 2026-08-12: a hírfolyam. A cikkek /hirek/ÉÉÉÉ/HH/NN/<slug> alatt
        # vannak; a link szövege sablonos („(új ablakban nyílik meg)"), a
        # valódi cím az aria-label-ben — ezt a linkek_kigyujtese kezeli.
        # tartalom_ellenorzes: a hírek nagy része NEM pályázat, és a címből
        # ez nem mindig derül ki, ezért a cikk szövegét is megnézzük.
        "nev": "Budapest Főváros",
        "urls": [
            "https://budapest.hu/hirek",
            "https://einfoszab.budapest.hu/applicationForm"
            "?key=palyazat-tamogatas-view&type=7",
        ],
        "kinek": "egyesület",
        # PONTOS hoszt-kulcs: az einfoszab.budapest.hu-ra ez NEM vonatkozik,
        # azt a budapest_tetelek() táblázat-parsere viszi.
        "utvonal_elotag": {"budapest.hu": "/hirek/20"},
        "kulcsszo_nelkul": True,
        "tartalom_ellenorzes": True,
        "special": "budapest",
    },
    {
        # A régi /palyazatok listaoldal a teljes hirdetőtáblát adta vissza
        # (2681 tétel: állásajánlatok, garázsbérlet, ingatlan) → 2026-08-12-én
        # lecserélve erre a szűkebb kategóriára. Saját parser: a listáról
        # jön a cím és a határidő is (lásd jozsefvaros_tetelek()).
        "nev": "Józsefváros",
        "urls": ["https://jozsefvaros.hu/otthon/hirdetotabla/palyazatok/"
                 "palyazatok-szervezeteknek-tarsashazaknak-maganszemelyeknek/"],
        "kinek": "kft + egyesület",
        "special": "jozsefvaros",
    },
    {
        # Aggregátor: minden /p/ útvonalú link pályázat, de a címben gyakran
        # nincs kulcsszó ("Gyurós Tibor-díj 2026") → kulcsszó-szűrés kikapcsolva,
        # helyette az útvonal-előtag szűr. Az első 3 oldalt figyeljük (~60 tétel,
        # legfrissebbek elöl).
        "nev": "PAFI (Pályázatfigyelő)",
        "urls": [
            "https://pafi.hu/palyazatok",
            "https://pafi.hu/palyazatok?page=2",
            "https://pafi.hu/palyazatok?page=3",
        ],
        "kinek": "kft + egyesület",
        "utvonal_elotag": "/p/",
        "kulcsszo_nelkul": True,
    },
]

# A cím vagy az URL útvonala tartalmazza valamelyiket (a domain NEM számít!)
KULCSSZAVAK = [
    "pályáz", "palyaz", "felhív", "felhiv", "kiírás", "kiiras",
    "grant", "call", "támogat", "tamogat", "ösztöndíj", "osztondij", "funding",
]

# Kizárandó linkek (URL-ben vagy címben)
KIZARAS = [
    "facebook", "instagram", "youtube", "tiktok", "linkedin", "mailto:",
    "cookie", "adatved", "adatvédel", "adatkezel", "sütik", "sutik", "bejelentkez",
    "impresszum", "login", "wp-login", "regisztracio", "regisztráció",
    "hirlevel", "hírlevél", "subscribepage",
    "pályázatírás", "palyazatiras",   # szolgáltatás-hirdetések (palyazatok.org)
    "kategoria/", "/tema/", "/page/", # lista-/kategória-/lapozó-oldalak
    "lezarult-felhivas", "lezárult felhívás",   # archívum-gyűjtőoldalak
]

# Csak az RSS-forrásokra: technikai közlemények kiszűrése (a cím a
# "palyazat.gov.hu" szót tartalmazza, így a sima kulcsszó-szűrőn átcsúszna)
RSS_KIZARAS = ["karbantartás", "karbantartas", "üzemszünet", "uzemszunet"]

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) palyazatradar/1.0 "
                  "(+https://github.com/) requests"
}

TIMEOUT = 25
MA = datetime.date.today().isoformat()

# Csak az ez UTÁN meghirdetett (megjelent) kiírás számít valódi újdonságnak;
# a korábbi megjelenésű = "most felfedezett régi tartalom" → csendes rögzítés.
# Dátum nélküli tétel csak élő (mai vagy jövőbeli) határidővel lehet új.
UJ_HATAR = "2026-07-20"

# Futásonként legfeljebb ennyi új tétel cikkoldalát töltjük le dúsításhoz;
# ami e fölött marad, azt kétség esetén újnak tekintjük (nem nyeljük le).
DUSITAS_LIMIT = 25

# Háttér-dúsítás: futásonként ennyi RÉGEBBI (még nem dúsított) tétel oldalát
# nézzük meg jogosultság/határidő ügyben — a teljes állomány kb. egy hónap
# alatt ér be. 10 egymást követő letöltési hiba után leállunk (hálózati gond).
HATTER_DUSITAS_LIMIT = 100
HATTER_HIBA_STOP = 10
# Udvariassági szünet a háttér-letöltések között (a palyazatok.org 415-tel
# rate-limitel burst-nél); teszthez RADAR_SLEEP=0 környezeti változó.
DUSITAS_SZUNET = float(os.environ.get("RADAR_SLEEP", "0.7"))

# Józsefváros: a hirdetőtábla listaoldala 2014-ig visszamenő teljes archívum,
# ezért csak az élő és a legfeljebb ennyi napja lejárt kiírásokat vesszük be.
JOZSEFVAROS_VISSZA_NAP = 90
JOZSEFVAROS_UTVONAL = "/otthon/hirdetotabla/palyazat/"

# palyazat.gov.hu közlemények: a /kozlemenyek oldal Next.js-es (a lista nincs
# benne a nyers HTML-ben), de mögötte nyílt JSON API van MÁSIK hoszton — ezért
# van esély rá, hogy a palyazat.gov.hu-ra érvényes geo-blokk ezt nem érinti.
# Tömeges-álriasztás védelem: ha egy MÁR ALAPOZOTT forrásnál egyszerre ennél
# több "új" jönne ÉS ez a forrás találatainak több mint 60%-a, az
# oldalszerkezet-változás / archívum-előbukkanás → csendes rögzítés.
BULK_HATAR_DB = 12
BULK_HATAR_ARANY = 0.6

# Watch-oldalak: csak VÁLTOZÁST figyelünk rajtuk (hash + diff-kivonat).
WATCH_OLDALAK = [
    # A Városi Civil Alap 2026-08-12 óta rendes forrás (lásd FORRASOK),
    # a változásfigyelés ott már fölösleges lenne.
    {"nev": "NKA miniszteri támogatások",
     "url": "https://nka.hu/kategoria/kiemelt-kategoriak/palyaztatas/miniszteri-tamogatasok-palyaztatas/"},
    {"nev": "Norvég Civil Alap főoldal",
     "url": "https://www.norvegcivilalap.hu/"},
    {"nev": "Józsefváros hirdetőtábla",
     "url": "https://jozsefvaros.hu/otthon/hirdetotabla/"},
]
OLDAL_CACHE_DIR = "data/pages"      # a watch-oldalak szövegcache-e (diff-hez)


# ---------------------------------------------------------------------------
# Segédfüggvények
# ---------------------------------------------------------------------------

def normalizal(url: str) -> str:
    """Hash, utm_*, fbclid, token= eltávolítása; egységesítés."""
    p = urlparse(url)
    params = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in ("fbclid", "token", "gclid"))
    ]
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", urlencode(params), ""))


def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        if "kreativeuropa" in url:
            r.encoding = "iso-8859-2"
        elif not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:
        print(f"  ! Lekérés sikertelen: {url} ({e})", file=sys.stderr)
        return None


def linkek_kigyujtese(
    html: str,
    base_url: str,
    lista_urlek: set[str],
    utvonal_elotag: str | None = None,
    kulcsszo_kell: bool = True,
    csak_gyoker: bool = False,
    csak_sajat_domain: bool = False,
) -> dict[str, str]:
    """Visszaad: {normalizált_url: cím}.

    utvonal_elotag: ha meg van adva, csak az ezzel kezdődő útvonalú linkek
    számítanak (pl. pafi.hu → "/p/").
    kulcsszo_kell: False esetén a KULCSSZAVAK-szűrés kimarad (aggregátoroknál,
    ahol az előtag már garantálja, hogy a link pályázat).
    csak_gyoker: csak az EGY szegmensű, kötőjeles útvonalú linkek (pl. a
    bgazrt.hu-n /megjelent-a-varosi-civil-alap-2026-evi-palyazati-kiirasa/).
    Így a menü- és aloldal-navigáció (/tamogatasok/altalanos/gyik/) és a
    szekció-nyitólapok (/tamogatasok/, /adattar/ – egyszavas slug) kiesnek.
    csak_sajat_domain: csak a listaoldallal AZONOS hoszton lévő linkek. Erre
    ott van szükség, ahol a kiírások rendben vannak, csak külső hivatkozások
    (útmutatók, árfolyamtáblák) szivárognak be — pl. a kreativeuropa.hu-n az
    ec.europa.eu-s segédanyagok."""
    soup = BeautifulSoup(html, "html.parser")
    talalatok: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "tel:", "#")) or not href:
            continue
        teljes = urljoin(base_url, href)
        if not teljes.startswith("http"):
            continue
        norm = normalizal(teljes)
        if norm in lista_urlek:            # maga a listaoldal nem találat
            continue
        cim = a.get_text(" ", strip=True)
        # Sok oldalon a link szövege sablonos („(új ablakban nyílik meg)",
        # „Tovább", „Bővebben"), a valódi cím pedig az aria-label/title
        # attribútumban vagy a kép alt-jában van (pl. budapest.hu/hirek).
        if len(cim) < 15 or GENERIKUS_LINKSZOVEG.match(cim):
            kep = a.find("img")
            for jelolt in (a.get("aria-label"), a.get("title"),
                           kep.get("alt") if kep else None):
                jelolt = (jelolt or "").strip()
                if len(jelolt) > len(cim) and not GENERIKUS_LINKSZOVEG.match(jelolt):
                    cim = jelolt
                    break
        p = urlparse(norm)
        # Kulcsszó a címben VAGY az URL útvonalában (domain nélkül!)
        kereses = (cim + " " + p.path + "?" + p.query).lower()
        # Útvonal-előtag: sztring → minden linkre érvényes; dict → hosztonként
        # ({"eeagrants.org": "/…/calls/"}), ha a forrás több oldalt figyel és
        # csak az egyiken van jól elkülönülő útvonal. Ahol VAN előtag-szabály,
        # ott az dönt (a kulcsszó-szűrés kimarad, mert az útvonal már garantál);
        # ahol nincs, ott marad a kulcsszavas szűrés.
        if csak_sajat_domain and p.netloc != urlparse(base_url).netloc:
            continue
        if csak_gyoker:
            reszek = [x for x in p.path.split("/") if x]
            if len(reszek) != 1 or "-" not in reszek[0]:
                continue
        elotag = utvonal_elotag
        if isinstance(utvonal_elotag, dict):
            # PONTOS hoszt-egyezés (a www. előtag megengedve). Nem elég a
            # végződés-vizsgálat: a „budapest.hu" kulcs különben ráülne az
            # einfoszab.budapest.hu-ra is, aminek más a szerkezete.
            elotag = next((v for h, v in utvonal_elotag.items()
                           if p.netloc in (h, "www." + h)), None)
        if elotag is not None:
            if not p.path.startswith(elotag):
                continue
        elif kulcsszo_kell and not any(k in kereses for k in KULCSSZAVAK):
            continue
        if any(x in norm.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if munkaajanlat_cim(cim, norm):    # álláshirdetés, nem pályázat
            continue
        if len(cim) < 12:                  # üres/ikon/"Tovább" link → slug a cím helyett
            cim = p.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        # az első (általában legbeszédesebb) címet tartjuk meg
        talalatok.setdefault(norm, cim[:200])
    return talalatok


NKA_HIREK_UTVONAL = "/kiemelt-kategoriak/hirek/"
NKA_FELHIVAS_UTVONAL = "/kollegiumok-felhivasai/"
NKA_NINCS_FELHIVAS = re.compile(
    r"jelenleg\s+nincs\s+el[éeÉE]rhet[őoÖO]\s+p[áaÁA]ly[áaÁA]zati\s+felh[íiÍI]v[áaÁA]s",
    re.IGNORECASE)


def _nka_kanonikus(url: str) -> str:
    """A kollégiumi aloldalon lévő RELATÍV felhívás-link feloldva
    .../kollegiumok-felhivasai/<kollégium>/<felhívás> alakot ad, de az oldal
    a .../kollegiumok-felhivasai/<felhívás> címre irányít át. Egységesítjük,
    hogy ugyanaz a felhívás ne kerüljön be kétszer, más kulccsal."""
    p = urlparse(url)
    reszek = [x for x in p.path.split("/") if x]
    try:
        i = reszek.index("kollegiumok-felhivasai")
    except ValueError:
        return url
    if len(reszek) - i == 3:                      # kollégium + felhívás
        reszek.pop(i + 1)
        return urlunparse((p.scheme, p.netloc, "/" + "/".join(reszek) + "/",
                           "", p.query, ""))
    return url


def nka_tetelek(html: str, url: str) -> dict[str, str]:
    """NKA: kétféle oldal, a bejövő URL dönti el, melyik.

    1. Kollégiumi aloldal (…/kollegiumok-felhivasai/<kollégium>/): a felhívás
       magán az oldalon van. Ha ott a „Jelenleg nincs elérhető pályázati
       felhívás." mondat, nincs találat. Különben a tartalmi blokkból
       kiszedjük a felhívás-oldalra mutató linke(ke)t, a cím pedig
       „<Kollégium> – <a blokk első érdemi félkövér sora>".
    2. Hírkategória (…/kategoria/kiemelt-kategoriak/hirek/): sok a nem
       pályázati hír (kitüntetés, nekrológ, kiállítás), ezért itt ÚTVONAL
       (/kiemelt-kategoriak/hirek/) ÉS kulcsszó is kell — a 24 hírből így
       marad a 3 pályázati."""
    soup = BeautifulSoup(html, "html.parser")
    talalatok: dict[str, str] = {}

    if NKA_FELHIVAS_UTVONAL in urlparse(url).path:
        blokk = soup.select_one(".single-post__content") or soup.find("main")
        if blokk is None or NKA_NINCS_FELHIVAS.search(blokk.get_text(" ", strip=True)):
            return talalatok
        cimtag = soup.find("title")
        kollegium = cimtag.get_text(" ", strip=True).split(" - ")[0].strip() if cimtag else ""
        if not kollegium:
            kollegium = (urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
                         .replace("-", " ").title())
        felhivas_cim = next(
            (sz for sz in (s.get_text(" ", strip=True) for s in blokk.find_all("strong"))
             if len(sz) >= 8), "")
        cim = f"{kollegium} – {felhivas_cim}" if felhivas_cim else f"{kollegium} – felhívás elérhető"
        sajat = normalizal(url)
        for a in blokk.find_all("a", href=True):
            cel = _nka_kanonikus(normalizal(urljoin(url, a["href"])))
            if (NKA_FELHIVAS_UTVONAL in urlparse(cel).path
                    and cel != sajat and not cel.lower().endswith(".pdf")):
                talalatok.setdefault(cel, cim[:200])
        if not talalatok:                 # nincs külön felhívás-oldal → maga a lap
            talalatok[sajat] = cim[:200]
        return talalatok

    for a in soup.find_all("a", href=True):
        cel = normalizal(urljoin(url, a["href"]))
        p = urlparse(cel)
        if not p.path.startswith(NKA_HIREK_UTVONAL):
            continue
        cim = a.get_text(" ", strip=True)
        kereses = (cim + " " + p.path).lower()
        if not any(k in kereses for k in KULCSSZAVAK):
            continue
        if any(x in cel.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if munkaajanlat_cim(cim, cel):
            continue
        if len(cim) < 12:
            cim = p.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        talalatok.setdefault(cel, cim[:200])
    return talalatok


EINFOSZAB_HOSZT = "einfoszab.budapest.hu"


def budapest_tetelek(html: str, url: str) -> dict[str, str]:
    """Budapest Főváros: kétféle oldal, a hoszt dönti el, melyik.

    1. einfoszab.budapest.hu — közzétételi TÁBLÁZAT (#tblData) az alábbi
       oszlopokkal: pályázati kiírás címe | benyújtás helye | benyújtás
       határideje | közzététel dátuma | Részletek. Vagyis a címet, a
       határidőt ÉS a megjelenést is készen kapjuk, dúsítás nélkül.
       Kulcsnak a „Részletek" sorazonosítós linkje kell: a „benyújtás helye"
       több sornál ugyanarra a gyűjtőoldalra mutat, az ütközne.
    2. budapest.hu/hirek — sima hírfolyam, a linkek_kigyujtese viszi
       (útvonal-előtag + aria-label-ből vett cím), a „tényleg pályázat-e"
       vizsgálat pedig a cikk szövegén fut le a dúsításkor."""
    if urlparse(url).netloc != EINFOSZAB_HOSZT:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    tabla = soup.find("table", id="tblData") or soup.find("table")
    if tabla is None:
        print("  ! einfoszab: nincs meg a táblázat", file=sys.stderr)
        return {}
    talalatok: dict[str, str] = {}
    for sor in tabla.select("tbody tr"):
        cellak = sor.find_all("td")
        if len(cellak) < 4:
            continue
        cim = cellak[0].get_text(" ", strip=True)
        if not cim:
            continue
        reszletek = cellak[-1].find("a", href=True)
        hely = cellak[1].find("a", href=True)
        cel = reszletek or hely
        if not cel:
            continue
        kulcs = normalizal(urljoin(url, cel["href"]))
        if munkaajanlat_cim(cim, kulcs):
            continue
        hatarido = _elso_datum(cellak[2].get_text(" ", strip=True))
        megjelent = _elso_datum(cellak[3].get_text(" ", strip=True))
        if hatarido:
            LISTA_HATARIDOK[kulcs] = hatarido
        if megjelent:
            LISTA_MEGJELENES[kulcs] = megjelent
        talalatok.setdefault(kulcs, cim[:200])
    return talalatok


PALYAZATOK_ORG_KARUSSZEL = ".af-banner-carousel-1"


def palyazatok_org_tetelek(html: str, base_url: str) -> dict[str, str]:
    """palyazatok.org főoldal, „Friss pályázatok" karusszel.

    A karusszel jobbra-balra lapozható, de kattintgatni NEM kell: a slick
    slider mind a 10 diát beleírja a kiszolgált HTML-be (a látható 3 csak
    CSS-kérdés), a `slick-cloned` másolatokat pedig a böngésző teszi hozzá,
    a nyers HTML-ben nincsenek. Minden dia egy `.slick-item`: a kép egy üres
    szövegű linkben, a cím pedig a `<h4>`-ben — ezért a linkek szövegéből
    dolgozó általános gyűjtő itt üres címeket adna.

    A főoldal második karusszelje (`.posts-slider`) referenciacikkeket
    tartalmaz („Munkába állt a Bobcat…"), azt szándékosan nem nézzük."""
    soup = BeautifulSoup(html, "html.parser")
    karusszel = soup.select_one(PALYAZATOK_ORG_KARUSSZEL)
    if karusszel is None:
        print("  ! palyazatok.org: nincs meg a 'Friss pályázatok' karusszel",
              file=sys.stderr)
        return {}
    talalatok: dict[str, str] = {}
    for dia in karusszel.select(".slick-item"):
        if "slick-cloned" in (dia.get("class") or []):
            continue
        a = dia.find("a", href=True)
        if not a:
            continue
        fejlec = dia.find(["h4", "h3", "h2"])
        cim = (fejlec.get_text(" ", strip=True) if fejlec else "").strip()
        norm = normalizal(urljoin(base_url, a["href"]))
        if not cim:                       # tartalék: a leghosszabb linkszöveg
            cim = max((x.get_text(" ", strip=True) for x in dia.find_all("a")),
                      key=len, default="")
        if not cim:
            cim = urlparse(norm).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        if any(x in norm.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if munkaajanlat_cim(cim, norm):
            continue
        talalatok.setdefault(norm, cim[:200])
    return talalatok


def nka_kollegiumok(html: str) -> dict[str, str]:
    """NKA: 'Xy Kollégiuma (felhívás elérhető)' párosok kulcsolása."""
    szoveg = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    minta = re.compile(
        r"([A-ZÁÉÍÓÖŐÚÜŰ][\w\sáéíóöőúüűÁÉÍÓÖŐÚÜŰ,.\-]{2,90}?Kollégium\w*)\s*"
        r"\(\s*felhívás\s+elérhető\s*\)",
        re.UNICODE,
    )
    talalatok = {}
    for m in minta.finditer(szoveg):
        nev = re.sub(r"\s+", " ", m.group(1)).strip()
        talalatok[f"nka-kollegium:{nev}"] = f"{nev} – felhívás elérhető"
    return talalatok


def rss_tetelek(xml_szoveg: str) -> dict[str, str]:
    """RSS feed tételei: {normalizált_link: cím}.

    A kulcsszó-szűrés itt CSAK a címre megy (a link útvonala mindig tartalmazza
    a 'palyazat' szót, tehát nem szelektálna), plusz az RSS_KIZARAS kiszedi a
    karbantartási közleményeket."""
    talalatok: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_szoveg.encode("utf-8", "ignore"))
    except ET.ParseError as e:
        print(f"  ! RSS-feldolgozási hiba: {e}", file=sys.stderr)
        return talalatok
    for item in root.iter("item"):
        cim = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link.startswith("http") or len(cim) < 5:
            continue
        if not any(k in cim.lower() for k in KULCSSZAVAK):
            continue
        if any(x in link.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if any(x in cim.lower() for x in RSS_KIZARAS):
            continue
        talalatok.setdefault(normalizal(link), cim[:200])
    return talalatok


# A listaoldalról/API-ból közvetlenül megkapott adatok — a main() innen írja
# az adatok.json-ba, dúsítás (cikkoldal-letöltés) nélkül.
LISTA_HATARIDOK: dict[str, str] = {}    # Józsefváros
LISTA_MEGJELENES: dict[str, str] = {}   # közlemények: dateOfPublication
LISTA_LEAD: dict[str, str] = {}         # közlemények: a szöveg (HTML) az API-ból


def kozlemeny_tetelek(elso_oldal: str) -> dict[str, str]:
    """palyazat.gov.hu közlemények a JSON API-ról: {cikk_url: cím}.

    Csak a KOZLEMENY_TIPUS típusú (új kiírás megjelenése) és a
    KOZLEMENY_HATAR utáni közlemények kellenek. Az API dátum szerint
    csökkenő sorrendben ad vissza, ezért a lapozást leállítjuk, amint egy
    oldal legrégebbi tétele már a határ alatt van.

    A megjelenési dátumot és a közlemény szövegét (`lead`) is eltesszük:
    így a dúsításhoz nem kell letölteni a cikkoldalt — ami külföldi IP-ről
    amúgy sem menne (geo-blokk)."""
    talalatok: dict[str, str] = {}
    oldal = elso_oldal
    for i in range(KOZLEMENY_MAX_OLDAL):
        if oldal is None:
            break
        try:
            adat = json.loads(oldal)
        except json.JSONDecodeError as e:
            print(f"  ! Közlemény-API: hibás JSON ({e})", file=sys.stderr)
            break
        tetelek = adat.get("result") or []
        if not tetelek:
            break
        legregebbi = "9999"
        for x in tetelek:
            datum = (x.get("dateOfPublication") or "")[:10]
            legregebbi = min(legregebbi, datum or "9999")
            if x.get("announcementTypeCode") != KOZLEMENY_TIPUS:
                continue
            if not datum or datum < KOZLEMENY_HATAR:
                continue
            alias, cim = x.get("urlAlias"), (x.get("title") or "").strip()
            if not alias or not cim:
                continue
            kulcs = normalizal(KOZLEMENY_LINK + alias.lstrip("/"))
            LISTA_MEGJELENES[kulcs] = datum
            if x.get("lead"):
                LISTA_LEAD[kulcs] = x["lead"]
            talalatok.setdefault(kulcs, cim[:200])
        meta = adat.get("meta") or {}
        kovetkezo = meta.get("skip", 0) + len(tetelek)
        if legregebbi < KOZLEMENY_HATAR or kovetkezo >= meta.get("totalCount", 0):
            break
        oldal = fetch(f"{KOZLEMENY_API}?limit={KOZLEMENY_OLDAL}&skip={kovetkezo}")
    return talalatok


def jozsefvaros_tetelek(html: str, base_url: str) -> dict[str, str]:
    """A józsefvárosi hirdetőtábla accordion-listája: {normalizált_url: cím}.

    Minden `div.accordion-item` fejlécében a `.s_title` három közvetlen
    gyerekeleme: [sorszám, határidő, cím]; a testében a „További részletek"
    link mutat a kiírás oldalára. Így a címet és a határidőt is megkapjuk a
    listáról — nem kell hozzá tétel-dúsítás.

    Az oldal 2014-ig visszamenő TELJES archívum (~780 tétel), ezért a
    JOZSEFVAROS_VISSZA_NAP-nál régebben lejárt tételeket eldobjuk. Határidő
    nélküli („folyamatos") tétel mindig bekerül."""
    soup = BeautifulSoup(html, "html.parser")
    also_hatar = (datetime.date.today()
                  - datetime.timedelta(days=JOZSEFVAROS_VISSZA_NAP)).isoformat()
    talalatok: dict[str, str] = {}
    for item in soup.select("div.accordion-item"):
        a = item.select_one(f'a[href*="{JOZSEFVAROS_UTVONAL}"]')
        if not a or not a.get("href"):
            continue
        st = item.select_one(".s_title")
        mezok = ([d.get_text(" ", strip=True) for d in st.find_all(recursive=False)]
                 if st else [])
        cim = mezok[2] if len(mezok) > 2 else ""
        hatarido = None
        if len(mezok) > 1:
            m = DATUM_TELJES.search(mezok[1])
            if m:
                hatarido = _iso(int(m[1]), HONAPOK[m[2].lower()], int(m[3]))
        if hatarido and hatarido < also_hatar:      # régen lejárt archívum
            continue
        norm = normalizal(urljoin(base_url, a["href"]))
        if any(x in norm.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if not cim:                                  # üres fejléc → slug a címből
            cim = (urlparse(norm).path.rstrip("/").rsplit("/", 1)[-1]
                   .replace("-", " ").replace("_", " "))
        if munkaajanlat_cim(cim, norm):              # álláshirdetés, nem pályázat
            continue
        if hatarido:
            LISTA_HATARIDOK[norm] = hatarido
        talalatok.setdefault(norm, cim[:200])
    return talalatok


def hatarido_a_cimben(cim: str) -> str | None:
    m = re.search(r"(20\d{2}[.\-/ ]\s*\w+\.?\s*\d{1,2}|határidő[:\s]+[^,;]{4,30})", cim, re.I)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Tétel-dúsítás: megjelenési dátum + határidő a cikkoldalról
# (a régi palyazat-radar prototípus enrich.py-ának átdolgozása)
# ---------------------------------------------------------------------------

HONAPOK = {
    "január": 1, "januar": 1, "február": 2, "februar": 2, "március": 3,
    "marcius": 3, "április": 4, "aprilis": 4, "május": 5, "majus": 5,
    "június": 6, "junius": 6, "július": 7, "julius": 7, "augusztus": 8,
    "szeptember": 9, "október": 10, "oktober": 10, "november": 11,
    "december": 12,
}
HONAP_RE = "|".join(sorted(HONAPOK, key=len, reverse=True))

ANGOL_HONAPOK = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
ANGOL_HONAP_RE = "|".join(sorted(ANGOL_HONAPOK, key=len, reverse=True))
# Angol dátum, hónap elöl: „Oct 1, 2026", „October 1 2026" (visegradfund.org)
DATUM_ANGOL = re.compile(
    rf"\b({ANGOL_HONAP_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b",
    re.IGNORECASE)

DATUM_TELJES = re.compile(rf"(20\d{{2}})\.?\s*({HONAP_RE})\s*(\d{{1,2}})\b", re.IGNORECASE)
DATUM_SZAMOS = re.compile(r"\b(20\d{2})[.\-](?:\s*)(\d{1,2})[.\-](?:\s*)(\d{1,2})\b")
DATUM_HONAPNAP = re.compile(rf"\b({HONAP_RE})\s*(\d{{1,2}})\b", re.IGNORECASE)

HATARIDO_KULCS = re.compile(
    # tő-alakok, hogy a ragozás ne számítson ("beadni", "beadási", "leadható")
    r"(határid|hatarid|pályázni|palyazni|benyújt|benyujt|bead|"
    r"jelentkez|éjfél|ejfel|leadás|leadas|leadni|leadhat|deadline|"
    # A határidőt sokszor nem a "határidő" szó jelöli, hanem a mondat
    # ragozása: "…2026. augusztus 25. kedd 13 óráig szíveskedjenek
    # eljuttatni" (lásd a FITT-díj kiírását). Ezek a toldalékok/igék a
    # dátum közvetlen szomszédjai, ezért pontosabbak, mint az ablak tágítása.
    r"eljuttat|beérkez|beerkez|megküld|megkuld|"
    r"óráig|oraig|napjáig|napjaig|óráig|hatarnap|határnap)", re.IGNORECASE)

META_DATUM_SELECTOROK = (
    ("meta[property='article:published_time']", "content"),
    ("meta[name='article:published_time']", "content"),
    ("meta[property='og:published_time']", "content"),
    ("meta[itemprop='datePublished']", "content"),
    ("time[datetime]", "datetime"),
)


def _iso(ev: int, ho: int, nap: int) -> str | None:
    try:
        return datetime.date(ev, ho, nap).isoformat()
    except ValueError:
        return None


def megjelenes_kinyerese(soup: BeautifulSoup) -> str | None:
    """A cikk megjelenési dátuma: meta tagek, majd látható magyar dátum."""
    for sel, attr in META_DATUM_SELECTOROK:
        el = soup.select_one(sel)
        if el and el.get(attr):
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", el[attr])
            if m:
                return _iso(int(m[1]), int(m[2]), int(m[3]))
    szoveg = soup.get_text(" ", strip=True)[:4000]
    m = DATUM_TELJES.search(szoveg)
    if m:
        return _iso(int(m[1]), HONAPOK[m[2].lower()], int(m[3]))
    m = DATUM_SZAMOS.search(szoveg)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    return None


def _datum_jeloltek(ablak: str, megj: datetime.date | None) -> list[tuple[int, str]]:
    """(pozíció, ISO-dátum) párok az ablakban talált dátumokból."""
    ki: list[tuple[int, str]] = []
    for m in DATUM_TELJES.finditer(ablak):
        d = _iso(int(m[1]), HONAPOK[m[2].lower()], int(m[3]))
        if d:
            ki.append((m.start(), d))
    for m in DATUM_SZAMOS.finditer(ablak):
        d = _iso(int(m[1]), int(m[2]), int(m[3]))
        if d:
            ki.append((m.start(), d))
    for m in DATUM_ANGOL.finditer(ablak):
        d = _iso(int(m[3]), ANGOL_HONAPOK[m[1].lower()], int(m[2]))
        if d:
            ki.append((m.start(), d))
    if megj:  # év nélküli dátum ("október 22."): a megjelenés évéből következtetünk
        lefedve = {p for p, _ in ki}
        for m in DATUM_HONAPNAP.finditer(ablak):
            if any(abs(m.start() - p) < 12 for p in lefedve):
                continue  # egy évszámos dátum hónap-nap része
            ho, nap = HONAPOK[m[1].lower()], int(m[2])
            ev = megj.year + (1 if (ho, nap) < (megj.month, megj.day) else 0)
            d = _iso(ev, ho, nap)
            if d:
                ki.append((m.start(), d))
    return ki


# Címkézett határidő-mező: sok oldal külön adatsorban közli a határidőt
# (pafi.hu: <dt>Határidő</dt><dd>2026. 08. 25. (13 nap)</dd>). Ez sokkal
# megbízhatóbb, mint a folyószövegből találgatni — ELŐSZÖR ezt nézzük.
HATARIDO_CIMKE = re.compile(
    r"^\s*(érvényes|ervenyes|határidő|hatarido|deadline|"
    r"(beadási|beadasi|benyújtási|benyujtasi|jelentkezési|jelentkezesi|"
    r"pályázati|palyazati|leadási|leadasi|beérkezési|beerkezesi)\s+határidő\w*|"
    r"benyújtás\s+határideje|benyujtas\s+hataridej\w*)\s*:?\s*$", re.IGNORECASE)


def _elso_datum(szoveg: str) -> str | None:
    """Az első értelmes dátum a szövegdarabban (ISO), vagy None."""
    m = DATUM_TELJES.search(szoveg)
    if m:
        return _iso(int(m[1]), HONAPOK[m[2].lower()], int(m[3]))
    m = DATUM_SZAMOS.search(szoveg)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    m = DATUM_ANGOL.search(szoveg)
    if m:
        return _iso(int(m[3]), ANGOL_HONAPOK[m[1].lower()], int(m[2]))
    return None


def cimkezett_hatarido(soup: BeautifulSoup) -> str | None:
    """Határidő a címkézett adatmezőkből (dt/dd, th/td, „Határidő: …").

    FONTOS: ezt a TELJES DOM-on kell futtatni, még a fejléc/lábléc kidobása
    előtt — a pafi.hu épp a <header>-ben közli a határidőt, tehát a
    tetel_dusitas() takarítása pont a legjobb adatot dobná ki."""
    for cimke in soup.find_all(["dt", "th", "strong", "b", "span", "label", "div", "p"]):
        szoveg = cimke.get_text(" ", strip=True)
        if not szoveg or len(szoveg) > 40 or not HATARIDO_CIMKE.match(szoveg):
            continue
        kovetkezo = cimke.find_next_sibling()
        if kovetkezo is not None:
            d = _elso_datum(kovetkezo.get_text(" ", strip=True)[:120])
            if d:
                return d
        szulo = cimke.parent
        if szulo is not None:                    # „Határidő: 2026. 08. 25."
            maradek = szulo.get_text(" ", strip=True).replace(szoveg, "", 1)
            d = _elso_datum(maradek[:120])
            if d:
                return d
    return None


# Határidő-kulcsszavak SZINTEKBEN. Ahol egy oldal több dátumot sorol fel
# (visegradfund.org „Timeline": Call opens / Draft submission deadline /
# Final application deadline / Results announced), ott a puszta „legközelebbi
# dátum" rossz sorra ülhet. Ezért előbb a legbeszédesebb megfogalmazást
# keressük, és csak ha az nincs, lépünk az általánosabb szintre.
HATARIDO_SZINT1 = re.compile(
    r"(final application deadline|final deadline|"
    r"beadási határidő|beadasi hatarido|benyújtási határidő|benyujtasi hatarido|"
    r"benyújtás határideje|benyujtas hatarideje)", re.IGNORECASE)
HATARIDO_SZINT2 = re.compile(
    r"(application deadline|submission deadline|closing date|"
    r"jelentkezési határidő|jelentkezesi hatarido|pályázati határidő|"
    r"palyazati hatarido|beérkezési határidő|beerkezesi hatarido)", re.IGNORECASE)


def _legkozelebbi_datum(szoveg: str, kulcs_re: re.Pattern,
                        megj: datetime.date | None) -> str | None:
    legjobb: tuple[int, str] | None = None  # (távolság, ISO-dátum)
    for km in kulcs_re.finditer(szoveg):
        lo, hi = max(0, km.start() - 120), min(len(szoveg), km.end() + 120)
        kulcs_poz = km.start() - lo
        for poz, d in _datum_jeloltek(szoveg[lo:hi], megj):
            if megj and datetime.date.fromisoformat(d) < megj:
                continue
            tav = abs(poz - kulcs_poz)
            if legjobb is None or tav < legjobb[0]:
                legjobb = (tav, d)
    return legjobb[1] if legjobb else None


def hatarido_kinyerese(szoveg: str, megjelent: str | None) -> str | None:
    """Határidő: a kulcsszóhoz legközelebbi dátum a ±120 karakteres környezetben;
    a megjelenésnél korábbi dátum nem lehet határidő. A kulcsszavakat három
    szinten próbáljuk (a legkonkrétabbtól az általánosig) — így a „Final
    application deadline" veri a „Call opens" és a „Results announced" sorát."""
    megj: datetime.date | None = None
    if megjelent:
        try:
            megj = datetime.date.fromisoformat(megjelent)
        except ValueError:
            pass
    for szint in (HATARIDO_SZINT1, HATARIDO_SZINT2, HATARIDO_KULCS):
        d = _legkozelebbi_datum(szoveg, szint, megj)
        if d:
            return d
    return None


# Jogosultság-felismerés: a "pályázók köre / pályázhat / jogosult" kontextus
# környezetében említett szervezettípusok. Csak kontextusban keresünk, mert
# pl. az "egyesület" szó bárhol előfordulhat (szervezetnevekben is).
JOGOSULT_KULCS = re.compile(
    r"(pályázók köre|palyazok kore|pályázhat|palyazhat|pályázatot nyújthat|"
    r"jogosult|nyújthatnak be|nyujthatnak be|benyújtására|benyujtasara|"
    r"kedvezményezett|kedvezmenyezett|célcsoport|celcsoport|"
    r"igényelhet|igenyelhet|jelentkezhet)", re.IGNORECASE)

JOGOSULT_KATEGORIAK = {
    "civil": re.compile(
        r"(civil szervezet|egyesület|egyesulet|alapítvány|alapitvany|"
        r"nonprofit|non-profit|közhasznú|kozhasznu|\bNGO\b)", re.IGNORECASE),
    "vallalkozas": re.compile(
        r"(vállalkoz|vallalkoz|gazdasági társaság|gazdasagi tarsasag|"
        r"\bkft\b|\bzrt\b|\bkkv\b|mikro-?\s*,?\s*kis|\bcég\w*|\bceg\w*)",
        re.IGNORECASE),
    # csak többes számban: az egyes számú "a Fővárosi Önkormányzat" jellemzően
    # a KIÍRÓ, nem a pályázó (sablon-műtermék veszély, lásd CLAUDE.md)
    "onkormanyzat": re.compile(r"(önkormányzatok|onkormanyzatok)", re.IGNORECASE),
    "maganszemely": re.compile(
        r"(magánszemély|maganszemely|természetes személy|termeszetes szemely|"
        r"\bhallgató|\bhallgato|\bdiák\w*|\bdiak\w*)", re.IGNORECASE),
}


JOGOSULT_CIMKEK = {"civil": "civil/egyesület", "vallalkozas": "vállalkozás",
                   "onkormanyzat": "önkormányzat", "maganszemely": "magánszemély"}


def jogosultsag_kinyerese(szoveg: str) -> list[str]:
    """A jogosultsági kulcsszavak környezetében (−80/+300 karakter) említett
    szervezettípusok, rendezve. Üres lista = nem felismerhető."""
    talalt: set[str] = set()
    for km in JOGOSULT_KULCS.finditer(szoveg):
        lo, hi = max(0, km.start() - 80), min(len(szoveg), km.end() + 300)
        ablak = szoveg[lo:hi]
        for nev, minta in JOGOSULT_KATEGORIAK.items():
            if minta.search(ablak):
                talalt.add(nev)
    return sorted(talalt)


# ---------------------------------------------------------------------------
# Munkaajánlat-szűrés
# ---------------------------------------------------------------------------
# Az álláshirdetés nem pályázat, de sokszor "pályázat" néven fut (kivált a
# közszférában: "pályázati felhívás ... munkakör betöltésére"), és a kiíró
# oldalán ugyanabban a hírfolyamban jelenik meg — így simán átcsúszna.
#
# Két rétegben szűrünk:
#   1. cím/URL: jellemző munkakör-megnevezés (szóhatárral, hogy a
#      "kertészkedés"-féle összetételek ne akadjanak fenn);
#   2. a kiírás SZÖVEGE: ha legalább MUNKA_JEL_KELL erős jel megvan.
# A szöveges réteg a menü/lábléc nélküli tartalomra fut (lásd tetel_dusitas),
# különben a bgazrt.hu menüjében lévő "Álláshirdetés" minden oldalt megfogna.
MUNKA_PREFIX = "munkaajanlat:"      # allapot.json-beli tiltólista-kulcs előtag

# Sablonos linkszövegek: ilyenkor a cím máshonnan (aria-label, title, alt) jön.
GENERIKUS_LINKSZOVEG = re.compile(
    r"^\s*\(?\s*(új ablakban nyílik meg|uj ablakban nyilik meg|tovább\w*|"
    r"tovabb\w*|bővebben|bovebben|részletek|reszletek|további részletek|"
    r"tovabbi reszletek|olvass tovább|read more|learn more|more|kattints\w*|"
    r"ide kattintva|link|megnyitás|megnyitas)\s*»?\s*\)?\s*$", re.IGNORECASE)

# „Tényleg pályázatról szól?" — olyan forrásoknál kell (budapest.hu/hirek),
# ahol a hírfolyamban elvétve van csak kiírás, és a cím nem mindig árulkodó.
# ERŐS jel: egy is elég. GYENGE jel: kettő kell.
PALYAZAT_JEL_EROS = (
    "pályázati felhívás", "palyazati felhivas", "pályázati kiírás",
    "palyazati kiiras", "pályázók köre", "palyazok kore",
    "benyújtási határidő", "benyujtasi hatarido", "beadási határidő",
    "beadasi hatarido", "pályázati adatlap", "palyazati adatlap",
    "pályázatot hirdet", "palyazatot hirdet", "pályázatot ír ki",
    "palyazatot ir ki", "pályázati kategóri", "palyazati kategori",
)
PALYAZAT_JEL_GYENGE = (
    "pályázhat", "palyazhat", "pályázni", "palyazni", "keretösszeg",
    "keretosszeg", "támogatási kérelem", "tamogatasi kerelem",
    "elnyerhető", "elnyerheto", "vissza nem térítendő", "vissza nem teritendo",
    "pályázat benyújt", "palyazat benyujt", "felhívás", "felhivas",
)
NEM_PALYAZAT_PREFIX = "nem-palyazat:"   # tiltólista: megnéztük, nem kiírás


def palyazat_e_szoveg(szoveg: str) -> bool:
    """Tényleg pályázati kiírásról szól-e a cikk a (menü nélküli) szövege
    alapján. Egy erős jel elég, gyengéből kettő kell — így a „a főváros
    pályázatot nyújtott be" típusú hír nem minősül kiírásnak."""
    kis = szoveg.lower()
    if any(j in kis for j in PALYAZAT_JEL_EROS):
        return True
    return sum(1 for j in PALYAZAT_JEL_GYENGE if j in kis) >= 2

MUNKA_CIM_RE = re.compile(
    r"\b(referens|ügyintéző|ugyintezo|asszisztens|munkatárs|munkatars|"
    r"osztályvezető|osztalyvezeto|főosztályvezető|foosztalyvezeto|"
    r"csoportvezető|csoportvezeto|irodavezető|irodavezeto|gyakornok|"
    r"titkárnő|titkarno|recepciós|recepcios|takarító|takarito|sofőr|sofor|"
    r"portás|portas|gondnok|karbantartó|karbantarto|szakmunkás|szakmunkas|"
    r"lakatos|hegesztő|hegeszto|kőműves|komuves|villanyszerelő|villanyszerelo|"
    r"könyvelő|konyvelo|jogász|jogasz|rendszergazda|raktáros|raktaros|"
    r"szakács|szakacs|dajka|ápoló|apolo|biztonsági őr|biztonsagi or|"
    r"parkolási ellenőr|parkolasi ellenor|álláshirdetés|allashirdetes|"
    r"állásajánlat|allasajanlat|álláspályázat|allaspalyazat|"
    r"munkakör betöltésére|munkakor betoltesere)\b", re.IGNORECASE)

MUNKA_SZOVEG_JELEK = (
    "önéletrajz", "oneletrajz", "munkavégzés helye", "munkavegzes helye",
    "foglalkoztatás jellege", "foglalkoztatas jellege",
    "amit kínálunk", "amit kinalunk", "próbaidő", "probaido",
    "motivációs levél", "motivacios level", "teljes munkaidő", "teljes munkaido",
    "munkatársat keres", "munkatarsat keres",
    "munkatársunkat keres", "munkatarsunkat keres",
    "milyen feladatok várnak", "milyen feladatok varnak",
    "munkakör betöltésére", "munkakor betoltesere",
    "álláspályázat", "allaspalyazat", "bérezés", "berezes",
    "közalkalmazotti jogviszony", "kozalkalmazotti jogviszony",
)
MUNKA_JEL_KELL = 2                  # ennyi erős jel kell a szövegben


def munkaajanlat_cim(cim: str, url: str = "") -> bool:
    """Munkaajánlat-e pusztán a cím/URL alapján (olcsó előszűrés)."""
    return bool(MUNKA_CIM_RE.search(f"{cim} {urlparse(url).path}"))


def munkaajanlat_szoveg(szoveg: str) -> bool:
    """Munkaajánlat-e a kiírás (menü nélküli) szövege alapján."""
    kis = szoveg.lower()
    return sum(1 for j in MUNKA_SZOVEG_JELEK if j in kis) >= MUNKA_JEL_KELL


# ---------------------------------------------------------------------------
# Kategóriák: a kiírás címéből + szövegéből
# ---------------------------------------------------------------------------
# Egy tétel több kategóriába is eshet (pl. queer színházi pályázat). Ha egyik
# sem talál, az „Egyéb". A minták szándékosan szűkek ott, ahol egy tág szó
# rengeteg téves találatot adna: a „meleg" (melegvíz, melegedő), a „trans"
# (transzparens, transzformáció) és a „szivárvány" (Szivárvány Óvoda) ezért
# NEM szerepel; helyettük egyértelmű kifejezések állnak.
KATEGORIAK: dict[str, re.Pattern] = {
    "LMBTQ": re.compile(
        r"(lmbtq?i?a?\+?|leszbikus|\bqueer\b|\bgay\b|\bpride\b|transznem|"
        r"transzszexu|biszexu|homoszexu|azonos nemű|azonos nemu|"
        r"szexuális irányultság|szexualis iranyultsag|nemi identitás|"
        r"nemi identitas|szivárványcsalád|szivarvanycsalad)", re.IGNORECASE),
    "Női jogok": re.compile(
        r"(\bnők\b|\bnok\b|\bnői\b|\bnoi\b|\bnőket\b|\bnoket\b|\bnőknek\b|"
        r"\bnoknek\b|nőjog|nojog|feminis|\bgender\b|nemek közötti egyenlőség|"
        r"nemek kozotti egyenloseg|nőszervezet|noszervezet|\blányok\b|"
        r"\blanyok\b|anyaság|anyasag|bántalmazott nő|bantalmazott no)",
        re.IGNORECASE),
    "Színház": re.compile(
        r"(színház|szinhaz|színpad|szinpad|\bdráma|\bdrama\b|dramatur|"
        r"előadó-művészet|eloado-muveszet|előadóművészet|eloadomuveszet|"
        r"\belőadás|\beloadas|báb(színház|szinhaz|művész|muvesz)|"
        r"kortárs tánc|kortars tanc)", re.IGNORECASE),
    "Zene": re.compile(
        r"(\bzene|zenei|zenekar|zenész|zenesz|koncert|\bklub\b|klubtámogat|"
        r"klubtamogat|fesztivál|fesztival|szórakoz|szorakoz|közösségi tér|"
        r"kozossegi ter|könnyűzene|konnyuzene|\brock\b|\bjazz\b|\btechno\b|"
        r"\bhouse\b|\bpopzene|hanglemez|hangfelvétel|hangfelvetel|\bdalszerz|"
        r"élőzene|elozene|\bzenekari\b)", re.IGNORECASE),
    "Vállalkozás": re.compile(
        r"(vállalkoz|vallalkoz|\bkkv\b|kis- és középvállal|kis- es kozepvallal|"
        r"mikrovállalkoz|mikrovallalkoz|\bhitel\b|hitelprogram|hitelkonstrukció|"
        r"beruház|beruhaz|\bstartup\b|start-up|üzleti terv|uzleti terv|"
        r"gazdaságfejleszt|gazdasagfejleszt|eszközbeszerz|eszkozbeszerz|"
        r"cégfejleszt|cegfejleszt|\bcégek\b|\bcegek\b)", re.IGNORECASE),
}
KATEGORIA_EGYEB = "Egyéb"


def kategoriak_kinyerese(szoveg: str) -> list[str]:
    """A kiírásra illő kategóriák. Ha egyik minta sem talál: [„Egyéb"]."""
    talalt = [nev for nev, minta in KATEGORIAK.items() if minta.search(szoveg)]
    return talalt or [KATEGORIA_EGYEB]


def oldal_szovege(html: str) -> str:
    """A cikk szövege menü/fejléc/lábléc nélkül – ugyanaz a tisztítás, amit a
    tetel_dusitas() használ (a sablonszöveg hamis találatot adna)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                     "aside", "form"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:15000]


def tetel_dusitas(html: str) -> tuple[str | None, str | None, list[str], bool]:
    """(megjelent, hatarido, palyazhat, munkaajanlat) a cikkoldal HTML-jéből.

    A menü/fejléc/lábléc kidobásra kerül, hogy az oldalsablon ismétlődő
    szövege (pl. bgazrt.hu menüje) ne adjon hamis jogosultság-találatot.
    Ez a munkaajánlat-felismerésnél is kulcsfontosságú: a bgazrt.hu MINDEN
    oldalának menüjében ott az „Álláshirdetés" szó."""
    soup = BeautifulSoup(html, "html.parser")
    megjelent = megjelenes_kinyerese(soup)   # meta tagek még a teljes DOM-ból
    if megjelent and megjelent > MA:
        megjelent = None  # jövőbeli "megjelenés" = félreértelmezett dátum
    # A címkézett határidő-mező MÉG a takarítás előtt kell: a pafi.hu a
    # <header>-ben közli, amit a decompose() különben kidobna.
    cimkezett = cimkezett_hatarido(soup)
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                     "aside", "form"]):
        tag.decompose()
    szoveg = soup.get_text(" ", strip=True)[:15000]
    hatarido = cimkezett or hatarido_kinyerese(szoveg, megjelent)
    palyazhat = jogosultsag_kinyerese(szoveg)
    return megjelent, hatarido, palyazhat, munkaajanlat_szoveg(szoveg)


# ---------------------------------------------------------------------------
# Watch-oldalak: normalizált szöveg + hash + diff-kivonat
# (a régi prototípus changewatch.py-ának átdolgozása)
# ---------------------------------------------------------------------------

OLDAL_ZAJ_RE = re.compile(
    r"(20\d\d\.\s*\w+\s*\d+\.|\d{4}-\d{2}-\d{2})|©.*|cookie|süti", re.I)


def _oldal_slug(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def oldal_normalizalas(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()
    sorok = []
    for nyers in soup.get_text("\n").splitlines():
        sor = re.sub(r"\s+", " ", nyers).strip()
        if len(sor) < 4 or OLDAL_ZAJ_RE.fullmatch(sor):
            continue
        sorok.append(sor)
    return "\n".join(sorok)[:60_000]


def oldal_valtozas(url: str, html: str, oldal_allapot: dict, cache_dir: str) -> dict | None:
    """Ha változott az oldal: {'url','uj_sorok','torolt_sorok'}; egyébként None.
    Az oldal_allapot dictet helyben frissíti, a szövegcache-t lemezre írja.
    Első látáskor (nincs korábbi hash) csendben rögzít."""
    szoveg = oldal_normalizalas(html)
    ujj = hashlib.sha256(szoveg.encode()).hexdigest()
    elozo = oldal_allapot.get(url)
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"{_oldal_slug(url)}.txt")

    valtozas = None
    if elozo and elozo.get("hash") != ujj:
        regi = ""
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                regi = f.read()
        diff = [
            ln for ln in difflib.unified_diff(
                regi.splitlines(), szoveg.splitlines(), lineterm="", n=0)
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
        ]
        valtozas = {
            "url": url,
            "uj_sorok": [ln[1:].strip() for ln in diff if ln.startswith("+")][:12],
            "torolt_sorok": [ln[1:].strip() for ln in diff if ln.startswith("-")][:6],
        }

    oldal_allapot[url] = {"hash": ujj}
    with open(cache, "w", encoding="utf-8") as f:
        f.write(szoveg)
    return valtozas


# ---------------------------------------------------------------------------
# Fő futás
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Pályázatradar")
    ap.add_argument("--state", default="allapot.json")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--adatok", default=os.path.join("docs", "adatok.json"),
                    help="a weboldal adatfájlja (cím+forrás+dátumok); "
                         "teszthez adj meg tesztfájlt, pl. teszt_adatok.json!")
    ap.add_argument("--oldalak", default="oldalak.json",
                    help="a watch-oldalak hash-állapota; teszthez tesztfájlt adj meg!")
    ap.add_argument("--cache", default=OLDAL_CACHE_DIR,
                    help="a watch-oldalak szövegcache mappája")
    args = ap.parse_args()

    elso_futas = not os.path.exists(args.state)
    allapot: dict[str, str] = {}
    if not elso_futas:
        with open(args.state, encoding="utf-8") as f:
            allapot = json.load(f)

    # A weboldal adatfájlja: {kulcs: {cim, forras, kinek, elso, utolso}}
    adatok: dict = {"frissitve": MA, "tetelek": {}}
    if os.path.exists(args.adatok):
        try:
            with open(args.adatok, encoding="utf-8") as f:
                adatok["tetelek"] = json.load(f).get("tetelek", {})
        except Exception as e:
            print(f"  ! adatok.json nem olvasható, újrakezdem: {e}", file=sys.stderr)

    oldal_allapot: dict = {}
    if os.path.exists(args.oldalak):
        try:
            with open(args.oldalak, encoding="utf-8") as f:
                oldal_allapot = json.load(f)
        except Exception as e:
            print(f"  ! oldalak.json nem olvasható, újrakezdem: {e}", file=sys.stderr)

    lista_urlek = {normalizal(u) for f_ in FORRASOK for u in f_["urls"]}

    # Munkaajánlat-tiltólista: egyszer felismertük, többé nem foglalkozunk vele
    # (különben minden futáskor újra letöltenénk és újra kiszűrnénk).
    munkaajanlatok = {k[len(MUNKA_PREFIX):] for k in allapot
                      if k.startswith(MUNKA_PREFIX)}
    munka_szurve = 0
    # Ugyanez a logika a „megnéztük, mégsem pályázat" tételekre (budapest.hu
    # hírfolyam): egyszer eldöntjük, aztán nem töltjük le újra.
    nem_palyazatok = {k[len(NEM_PALYAZAT_PREFIX):] for k in allapot
                      if k.startswith(NEM_PALYAZAT_PREFIX)}
    nem_palyazat_szurve = 0
    ellenorzendo = {f["nev"] for f in FORRASOK if f.get("tartalom_ellenorzes")}

    jeloltek: list[dict] = []               # új tételek dúsítás/cutoff-döntés előtt
    alapozott: list[tuple[str, int]] = []   # (forrás, tételszám) – új forrás csendes alapfelvétele
    tomeges: list[tuple[str, int]] = []     # (forrás, tételszám) – bulk-guard által elnyelve
    hibas_forrasok: list[str] = []
    osszes_latott = 0

    for forras in FORRASOK:
        print(f"» {forras['nev']}")
        talalatok: dict[str, str] = {}
        sikeres = False
        for url in forras["urls"]:
            html = fetch(url)
            if html is None:
                continue
            sikeres = True
            spec = forras.get("special")
            if spec == "nka":
                talalatok.update(nka_tetelek(html, url))
            elif spec == "rss":
                talalatok.update(rss_tetelek(html))
            elif spec == "jozsefvaros":
                talalatok.update(jozsefvaros_tetelek(html, url))
            elif spec == "kozlemeny":
                talalatok.update(kozlemeny_tetelek(html))
            elif spec == "palyazatok_org":
                talalatok.update(palyazatok_org_tetelek(html, url))
            elif spec == "budapest" and urlparse(url).netloc == EINFOSZAB_HOSZT:
                talalatok.update(budapest_tetelek(html, url))
            else:
                talalatok.update(linkek_kigyujtese(
                    html, url, lista_urlek,
                    utvonal_elotag=forras.get("utvonal_elotag"),
                    kulcsszo_kell=not forras.get("kulcsszo_nelkul", False),
                    csak_gyoker=forras.get("csak_gyoker", False),
                    csak_sajat_domain=forras.get("csak_sajat_domain", False),
                ))
        if not sikeres:
            hibas_forrasok.append(forras["nev"])
            continue
        # Korábban felismert álláshirdetések kiejtése (se találat, se weboldal)
        talalatok = {k: c for k, c in talalatok.items()
                     if k not in munkaajanlatok and k not in nem_palyazatok}
        osszes_latott += len(talalatok)
        # Új (még alapállapot nélküli) forrás első sikeres beolvasása CSENDES:
        # a tételek bekerülnek az állapotba, de nem jelennek meg találatként —
        # különben pl. a PAFI ~60 tétele egyszerre árasztaná el az Issue-t.
        alap_kulcs = f"forras-alap:{forras['nev']}"
        forras_uj = alap_kulcs not in allapot
        friss = [(k, c) for k, c in talalatok.items() if k not in allapot]
        for kulcs, _cim in friss:
            allapot[kulcs] = MA
        # Bulk-guard: már alapozott forrásnál a hirtelen tömeges "új" nem
        # újdonság, hanem oldalszerkezet-változás / archívum-előbukkanás.
        bulk = (not forras_uj and len(friss) > BULK_HATAR_DB
                and talalatok and len(friss) / len(talalatok) > BULK_HATAR_ARANY)
        if forras_uj:
            allapot[alap_kulcs] = MA
            if not elso_futas:
                alapozott.append((forras["nev"], len(friss)))
        elif bulk:
            tomeges.append((forras["nev"], len(friss)))
        elif not elso_futas:
            jeloltek.extend(
                {"forras": forras["nev"], "cim": c, "kulcs": k} for k, c in friss)
        # Weboldal-adatok frissítése (minden látott tételre, nem csak az újakra)
        for kulcs, cim in talalatok.items():
            t = adatok["tetelek"].setdefault(kulcs, {})
            if not t.get("cim"):
                t["cim"] = cim
            t["forras"] = forras["nev"]
            t["kinek"] = forras["kinek"]
            # Kategória a CÍMBŐL – csak ha még nincs. A dúsítás később a
            # teljes szövegből számolja újra, az a mérvadó.
            t.setdefault("kategoriak", kategoriak_kinyerese(t.get("cim") or cim))
            t["elso"] = allapot.get(kulcs, MA)
            t["utolso"] = MA
            # Ha a listaoldal maga közölte a határidőt (Józsefváros), az a
            # mérvadó — pontosabb, mint amit a cikkoldalról kaparnánk össze.
            if kulcs in LISTA_HATARIDOK:
                t["hatarido"] = LISTA_HATARIDOK[kulcs]
            # Közlemények: a dátum és a szöveg az API-ból jön, a cikkoldalt
            # nem töltjük le (geo-blokk) — ezért itt, azonnal dúsítunk, és
            # `dusitva`-val megjelöljük, hogy a háttér-dúsítás se próbálkozzon.
            if kulcs in LISTA_MEGJELENES:
                t["megjelent"] = LISTA_MEGJELENES[kulcs]
                if not t.get("dusitva"):
                    t["dusitva"] = MA
                    try:
                        _m, hat, pal, _munka = tetel_dusitas(LISTA_LEAD.get(kulcs, ""))
                    except Exception as e:      # noqa: BLE001
                        print(f"  ! Lead-dúsítási hiba: {kulcs} ({e})", file=sys.stderr)
                        hat, pal = None, []
                    if hat:
                        t["hatarido"] = hat
                    if pal:
                        t["palyazhat"] = pal
                    t["kategoriak"] = kategoriak_kinyerese(
                        f"{t.get('cim', '')} {oldal_szovege(LISTA_LEAD.get(kulcs, ''))}")

    # ---- dúsítás + "valódi újdonság" döntés (UJ_HATAR cutoff) ----
    ujak: list[dict] = []
    regi_tartalom = 0
    dusitas_szam = 0
    for j in jeloltek:
        kulcs = j["kulcs"]
        megjelent = hatarido = None
        palyazhat: list[str] = []
        kategoriak: list[str] = []
        letoltes_ok = None                 # None: nem próbáltuk / nem URL
        elore = adatok["tetelek"].get(kulcs)
        if kulcs in LISTA_MEGJELENES and elore is not None:
            # Közlemény: mindent tudunk az API-ból, nincs mit letölteni.
            megjelent = elore.get("megjelent")
            hatarido = elore.get("hatarido")
            palyazhat = elore.get("palyazhat") or []
            letoltes_ok = True
        elif kulcs.startswith("http") and dusitas_szam < DUSITAS_LIMIT:
            dusitas_szam += 1
            html = fetch(kulcs)
            letoltes_ok = html is not None
            if html:
                try:
                    megjelent, hatarido, palyazhat, munka = tetel_dusitas(html)
                except Exception as e:      # noqa: BLE001
                    print(f"  ! Dúsítási hiba: {kulcs} ({e})", file=sys.stderr)
                else:
                    szoveg = oldal_szovege(html)
                    if munka:               # álláshirdetés, nem pályázat
                        allapot[MUNKA_PREFIX + kulcs] = MA
                        munkaajanlatok.add(kulcs)
                        adatok["tetelek"].pop(kulcs, None)
                        munka_szurve += 1
                        print(f"  – munkaajánlat kiszűrve: {j['cim'][:60]}")
                        continue
                    # Hírfolyam-forrásoknál a cím nem árulkodó: a cikk
                    # szövegéből döntjük el, tényleg kiírásról szól-e.
                    if (j["forras"] in ellenorzendo
                            and not palyazat_e_szoveg(szoveg)):
                        allapot[NEM_PALYAZAT_PREFIX + kulcs] = MA
                        nem_palyazatok.add(kulcs)
                        adatok["tetelek"].pop(kulcs, None)
                        nem_palyazat_szurve += 1
                        print(f"  – nem pályázati hír: {j['cim'][:60]}")
                        continue
                    kategoriak = kategoriak_kinyerese(f"{j['cim']} {szoveg}")
        j["megjelent"], j["hatarido"], j["palyazhat"] = megjelent, hatarido, palyazhat
        t = adatok["tetelek"].get(kulcs)
        if t is not None:
            if letoltes_ok is not None:
                t["dusitva"] = MA
            if kategoriak:
                t["kategoriak"] = kategoriak
            if megjelent:
                t["megjelent"] = megjelent
            if hatarido:
                t["hatarido"] = hatarido
            if palyazhat:
                t["palyazhat"] = palyazhat
        # Döntés: ha nem tudtuk megnézni az oldalt (limit/hiba/nem URL),
        # kétség esetén ÚJ; ha megnéztük: megjelent >= UJ_HATAR, vagy
        # dátum nélkül élő határidő kell.
        if letoltes_ok:
            valodi = ((megjelent and megjelent >= UJ_HATAR)
                      or (not megjelent and hatarido and hatarido >= MA))
        else:
            valodi = True
        if valodi:
            ujak.append(j)
        else:
            regi_tartalom += 1

    # ---- háttér-dúsítás: régebbi, még nem dúsított tételek fokozatosan ----
    varo = [k for k, t in adatok["tetelek"].items()
            if not t.get("dusitva") and k.startswith("http")]
    varo.sort(key=lambda k: adatok["tetelek"][k].get("elso") or "", reverse=True)
    varo.sort(key=lambda k: adatok["tetelek"][k].get("utolso") != MA)  # most listázottak elöl
    # Domain-váltogatás (round-robin): egy-egy oldalt nem terhelünk sorozatban,
    # és a dúsítás forrásonként egyenletesen halad.
    domain_sorok: dict[str, list[str]] = {}
    for k in varo:
        domain_sorok.setdefault(urlparse(k).netloc, []).append(k)
    varo = []
    while domain_sorok:
        for d in list(domain_sorok):
            varo.append(domain_sorok[d].pop(0))
            if not domain_sorok[d]:
                del domain_sorok[d]
    hatter_szam = hatter_hiba = 0
    for kulcs in varo[:HATTER_DUSITAS_LIMIT]:
        if hatter_szam:
            time.sleep(DUSITAS_SZUNET)
        if hatter_hiba >= HATTER_HIBA_STOP:
            print(f"  ! Háttér-dúsítás leállítva ({hatter_hiba} egymást követő hiba)",
                  file=sys.stderr)
            break
        t = adatok["tetelek"][kulcs]
        html = fetch(kulcs)
        t["dusitva"] = MA
        hatter_szam += 1
        if html is None:
            hatter_hiba += 1
            continue
        hatter_hiba = 0
        try:
            megjelent, hatarido, palyazhat, munka = tetel_dusitas(html)
        except Exception as e:              # noqa: BLE001
            print(f"  ! Dúsítási hiba: {kulcs} ({e})", file=sys.stderr)
            continue
        if munka:                           # utólag felismert álláshirdetés
            allapot[MUNKA_PREFIX + kulcs] = MA
            munkaajanlatok.add(kulcs)
            adatok["tetelek"].pop(kulcs, None)
            munka_szurve += 1
            print(f"  – munkaajánlat kiszűrve: {(t.get('cim') or kulcs)[:60]}")
            continue
        if megjelent and "megjelent" not in t:
            t["megjelent"] = megjelent
        if hatarido and "hatarido" not in t:
            t["hatarido"] = hatarido
        if palyazhat:
            t["palyazhat"] = palyazhat
        t["kategoriak"] = kategoriak_kinyerese(
            f"{t.get('cim', '')} {oldal_szovege(html)}")
    if hatter_szam:
        hatra = max(0, len(varo) - hatter_szam)
        print(f"» Háttér-dúsítás: {hatter_szam} tétel feldolgozva, {hatra} van hátra")

    # ---- watch-oldalak: csak változásfigyelés ----
    valtozasok: list[dict] = []
    for w in WATCH_OLDALAK:
        print(f"» [watch] {w['nev']}")
        html = fetch(w["url"])
        if html is None:
            hibas_forrasok.append(f"{w['nev']} (watch)")
            continue
        ch = oldal_valtozas(w["url"], html, oldal_allapot, args.cache)
        if ch:
            ch["nev"] = w["nev"]
            valtozasok.append(ch)

    with open(args.oldalak, "w", encoding="utf-8") as f:
        json.dump(oldal_allapot, f, ensure_ascii=False, indent=1)

    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(allapot, f, ensure_ascii=False, indent=2)

    adatok_dir = os.path.dirname(args.adatok)
    if adatok_dir:
        os.makedirs(adatok_dir, exist_ok=True)
    with open(args.adatok, "w", encoding="utf-8") as f:
        json.dump(adatok, f, ensure_ascii=False, indent=1)

    # ---- riport ----
    sorok = [f"# Pályázatradar – {MA}", ""]
    if elso_futas:
        sorok.append(
            f"**Alapállapot felvéve:** {len(FORRASOK)} forrás, {len(allapot)} tétel. "
            "Mostantól csak az új kiírásokról lesz jelzés."
        )
        uj_szam = 0
    elif ujak:
        uj_szam = len(ujak)
        sorok.append(f"**{uj_szam} új találat**")
        aktualis_forras = None
        for j in ujak:
            if j["forras"] != aktualis_forras:
                sorok += ["", f"## {j['forras']}", ""]
                aktualis_forras = j["forras"]
            resz = []
            if j.get("hatarido"):
                resz.append(f"⚠️ **határidő: {j['hatarido']}**")
            elif hatarido_a_cimben(j["cim"]):
                resz.append(f"⚠️ **{hatarido_a_cimben(j['cim'])}**")
            if j.get("megjelent"):
                resz.append(f"megjelent: {j['megjelent']}")
            if j.get("palyazhat"):
                resz.append("pályázhat: " + ", ".join(
                    JOGOSULT_CIMKEK.get(p, p) for p in j["palyazhat"]))
            extra = (" — " + ", ".join(resz)) if resz else ""
            if j["kulcs"].startswith("nka-kollegium:"):
                sorok.append(f"- **{j['cim']}**{extra} (nka.hu → Kollégiumok felhívásai)")
            else:
                sorok.append(f"- [{j['cim']}]({j['kulcs']}){extra}")
        if any("palyazatok.org" in j["kulcs"] for j in ujak):
            sorok += ["", "_A KKV-találatok hivatalos részletei a palyazat.gov.hu oldalon._"]
    else:
        uj_szam = 0
        sorok.append("Nincs új kiírás.")

    if valtozasok:
        sorok += ["", "## Megváltozott figyelt aloldalak", ""]
        for ch in valtozasok:
            sorok.append(f"- [{ch['nev']}]({ch['url']})")
            for s in ch.get("uj_sorok", [])[:5]:
                sorok.append(f"  - + {s}")

    if alapozott:
        sorok += ["", "## Forrás-alapállapot felvéve", ""]
        for nev, db in alapozott:
            sorok.append(f"- {nev}: {db} tétel csendben rögzítve — mostantól csak az újakat jelezzük")

    megjegyzesek = []
    megjegyzesek += [f"{nev}: {db} tétel egyszerre jött volna (oldalszerkezet-változás gyanú) "
                     "— csendben rögzítve, nem riasztunk" for nev, db in tomeges]
    if regi_tartalom:
        megjegyzesek.append(
            f"{regi_tartalom} tétel {UJ_HATAR} előtti megjelenésű vagy lejárt/dátum "
            "nélküli (régi tartalom) — csendben rögzítve, a weboldalon látható")
    if munka_szurve:
        megjegyzesek.append(
            f"{munka_szurve} álláshirdetés kiszűrve (nem pályázat) — a weboldalra "
            "sem kerül fel, és többé nem foglalkozunk vele")
    if nem_palyazat_szurve:
        megjegyzesek.append(
            f"{nem_palyazat_szurve} hír a cikk szövege alapján mégsem pályázati "
            "kiírás — kiszűrve, többé nem nézzük")
    if megjegyzesek:
        sorok += ["", "## Megjegyzések", ""]
        sorok += [f"- {m}" for m in megjegyzesek]

    if hibas_forrasok:
        sorok += ["", f"_Nem elérhető forrás(ok): {', '.join(hibas_forrasok)}_"]

    riport = "\n".join(sorok) + "\n"
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(riport)
    print("\n" + riport)

    # GitHub Actions kimenet
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"new_count={uj_szam}\n")
            f.write(f"first_run={'true' if elso_futas else 'false'}\n")
            f.write(f"baselined={len(alapozott)}\n")
            f.write(f"changes={len(valtozasok)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
