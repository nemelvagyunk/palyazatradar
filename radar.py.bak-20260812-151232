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

FORRASOK = [
    {
        "nev": "Norvég Civil Alap",
        "urls": ["https://palyazat.norvegcivilalap.hu/"],
        "kinek": "egyesület",
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
        # Speciális kezelés: kollégiumonként "(felhívás elérhető)" jelzés
        "nev": "NKA kollégiumi felhívások",
        "urls": [
            "https://nka.hu/kategoria/kiemelt-kategoriak/palyaztatas/kollegiumok-felhivasai/"
        ],
        "kinek": "egyesület",
        "special": "nka",
    },
    {
        "nev": "NEA (Bethlen Gábor Alapkezelő)",
        "urls": ["https://bgazrt.hu/tamogatasok/nemzeti-egyuttmukodesi-alap/"],
        "kinek": "egyesület",
    },
    {
        "nev": "Erasmus+ / ESC",
        "urls": ["https://erasmusplusz.hu/palyazati-lehetosegek-az-erasmus-programban"],
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
        # iso-8859-2 kódolású oldal – a fetch() kezeli
        "nev": "Kreatív Európa Kultúra",
        "urls": ["https://kultura.kreativeuropa.hu/kategoria/palyazatok"],
        "kinek": "egyesület",
    },
    {
        # A palyazat.gov.hu JS-alapú, géppel nem olvasható – ez az aggregátor a proxy.
        # A hivatalos részletek mindig a palyazat.gov.hu-n!
        "nev": "KKV / energetika (palyazatok.org)",
        "urls": ["https://palyazatok.org/kkv-palyazatok/"],
        "kinek": "kft",
    },
    {
        "nev": "Budapest Főváros",
        "urls": [
            "https://budapest.hu/nyitott-budapest/civil-szervezeteknek",
            "https://budapest.hu/zold-budapest/zold-palyazatok",
        ],
        "kinek": "egyesület",
    },
    {
        "nev": "Józsefváros",
        "urls": ["https://jozsefvaros.hu/palyazatok"],
        "kinek": "kft + egyesület",
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
    {
        # A palyazat.gov.hu-t GitHub-runnerről nem lehet elérni (geo-blokk,
        # lásd CLAUDE.md) → a palyazatok.org kategóriái a proxy ide is.
        "nev": "Civil (palyazatok.org)",
        "urls": ["https://palyazatok.org/palyazatok-civil-szervezeteknek/"],
        "kinek": "egyesület",
    },
    {
        "nev": "Kulturális / művészeti (palyazatok.org)",
        "urls": [
            "https://palyazatok.org/kulturalis-palyazatok/",
            "https://palyazatok.org/muveszeti-palyazatok/",
        ],
        "kinek": "kft + egyesület",
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

# Tömeges-álriasztás védelem: ha egy MÁR ALAPOZOTT forrásnál egyszerre ennél
# több "új" jönne ÉS ez a forrás találatainak több mint 60%-a, az
# oldalszerkezet-változás / archívum-előbukkanás → csendes rögzítés.
BULK_HATAR_DB = 12
BULK_HATAR_ARANY = 0.6

# Watch-oldalak: csak VÁLTOZÁST figyelünk rajtuk (hash + diff-kivonat).
WATCH_OLDALAK = [
    {"nev": "BGA Városi Civil Alap",
     "url": "https://bgazrt.hu/tamogatasok/varosi-civil-alap/"},
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
) -> dict[str, str]:
    """Visszaad: {normalizált_url: cím}.

    utvonal_elotag: ha meg van adva, csak az ezzel kezdődő útvonalú linkek
    számítanak (pl. pafi.hu → "/p/").
    kulcsszo_kell: False esetén a KULCSSZAVAK-szűrés kimarad (aggregátoroknál,
    ahol az előtag már garantálja, hogy a link pályázat)."""
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
        p = urlparse(norm)
        if utvonal_elotag and not p.path.startswith(utvonal_elotag):
            continue
        # Kulcsszó a címben VAGY az URL útvonalában (domain nélkül!)
        kereses = (cim + " " + p.path + "?" + p.query).lower()
        if kulcsszo_kell and not any(k in kereses for k in KULCSSZAVAK):
            continue
        if any(x in norm.lower() or x in cim.lower() for x in KIZARAS):
            continue
        if len(cim) < 12:                  # üres/ikon/"Tovább" link → slug a cím helyett
            cim = p.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        # az első (általában legbeszédesebb) címet tartjuk meg
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

DATUM_TELJES = re.compile(rf"(20\d{{2}})\.?\s*({HONAP_RE})\s*(\d{{1,2}})\b", re.IGNORECASE)
DATUM_SZAMOS = re.compile(r"\b(20\d{2})[.\-](?:\s*)(\d{1,2})[.\-](?:\s*)(\d{1,2})\b")
DATUM_HONAPNAP = re.compile(rf"\b({HONAP_RE})\s*(\d{{1,2}})\b", re.IGNORECASE)

HATARIDO_KULCS = re.compile(
    r"(határid|hatarid|pályázni|palyazni|benyújt|benyujt|beadás|beadas|"
    r"jelentkez|éjfél|ejfel|leadás|leadas|deadline)", re.IGNORECASE)

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


def hatarido_kinyerese(szoveg: str, megjelent: str | None) -> str | None:
    """Határidő: a kulcsszóhoz legközelebbi dátum a ±120 karakteres környezetben;
    a megjelenésnél korábbi dátum nem lehet határidő."""
    megj: datetime.date | None = None
    if megjelent:
        try:
            megj = datetime.date.fromisoformat(megjelent)
        except ValueError:
            pass
    legjobb: tuple[int, str] | None = None  # (távolság, ISO-dátum)
    for km in HATARIDO_KULCS.finditer(szoveg):
        lo, hi = max(0, km.start() - 120), min(len(szoveg), km.end() + 120)
        kulcs_poz = km.start() - lo
        for poz, d in _datum_jeloltek(szoveg[lo:hi], megj):
            if megj and datetime.date.fromisoformat(d) < megj:
                continue
            tav = abs(poz - kulcs_poz)
            if legjobb is None or tav < legjobb[0]:
                legjobb = (tav, d)
    return legjobb[1] if legjobb else None


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


def tetel_dusitas(html: str) -> tuple[str | None, str | None, list[str]]:
    """(megjelent, hatarido, palyazhat) a cikkoldal HTML-jéből.

    A menü/fejléc/lábléc kidobásra kerül, hogy az oldalsablon ismétlődő
    szövege (pl. bgazrt.hu menüje) ne adjon hamis jogosultság-találatot."""
    soup = BeautifulSoup(html, "html.parser")
    megjelent = megjelenes_kinyerese(soup)   # meta tagek még a teljes DOM-ból
    if megjelent and megjelent > MA:
        megjelent = None  # jövőbeli "megjelenés" = félreértelmezett dátum
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                     "aside", "form"]):
        tag.decompose()
    szoveg = soup.get_text(" ", strip=True)[:15000]
    hatarido = hatarido_kinyerese(szoveg, megjelent)
    palyazhat = jogosultsag_kinyerese(szoveg)
    return megjelent, hatarido, palyazhat


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
                talalatok.update(nka_kollegiumok(html))
            if spec == "rss":
                talalatok.update(rss_tetelek(html))
            else:
                talalatok.update(linkek_kigyujtese(
                    html, url, lista_urlek,
                    utvonal_elotag=forras.get("utvonal_elotag"),
                    kulcsszo_kell=not forras.get("kulcsszo_nelkul", False),
                ))
        if not sikeres:
            hibas_forrasok.append(forras["nev"])
            continue
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
            t["elso"] = allapot.get(kulcs, MA)
            t["utolso"] = MA

    # ---- dúsítás + "valódi újdonság" döntés (UJ_HATAR cutoff) ----
    ujak: list[dict] = []
    regi_tartalom = 0
    dusitas_szam = 0
    for j in jeloltek:
        kulcs = j["kulcs"]
        megjelent = hatarido = None
        palyazhat: list[str] = []
        letoltes_ok = None                 # None: nem próbáltuk / nem URL
        if kulcs.startswith("http") and dusitas_szam < DUSITAS_LIMIT:
            dusitas_szam += 1
            html = fetch(kulcs)
            letoltes_ok = html is not None
            if html:
                try:
                    megjelent, hatarido, palyazhat = tetel_dusitas(html)
                except Exception as e:      # noqa: BLE001
                    print(f"  ! Dúsítási hiba: {kulcs} ({e})", file=sys.stderr)
        j["megjelent"], j["hatarido"], j["palyazhat"] = megjelent, hatarido, palyazhat
        t = adatok["tetelek"].get(kulcs)
        if t is not None:
            if letoltes_ok is not None:
                t["dusitva"] = MA
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
            megjelent, hatarido, palyazhat = tetel_dusitas(html)
        except Exception as e:              # noqa: BLE001
            print(f"  ! Dúsítási hiba: {kulcs} ({e})", file=sys.stderr)
            continue
        if megjelent and "megjelent" not in t:
            t["megjelent"] = megjelent
        if hatarido and "hatarido" not in t:
            t["hatarido"] = hatarido
        if palyazhat:
            t["palyazhat"] = palyazhat
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
