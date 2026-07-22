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
import json
import os
import re
import sys
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
    "cookie", "adatved", "adatvédel", "sütik", "sutik", "bejelentkez",
    "impresszum", "login", "wp-login", "regisztracio", "regisztráció",
    "hirlevel-feliratkozas", "subscribepage",
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
        if len(cim) < 5:                   # üres/ikonlink → slug a cím helyett
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
# Fő futás
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Pályázatradar")
    ap.add_argument("--state", default="allapot.json")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--adatok", default=os.path.join("docs", "adatok.json"),
                    help="a weboldal adatfájlja (cím+forrás+dátumok); "
                         "teszthez adj meg tesztfájlt, pl. teszt_adatok.json!")
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

    lista_urlek = {normalizal(u) for f_ in FORRASOK for u in f_["urls"]}

    ujak: list[tuple[str, str, str]] = []   # (forrás, cím, kulcs/url)
    alapozott: list[tuple[str, int]] = []   # (forrás, tételszám) – új forrás csendes alapfelvétele
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
        uj_tetelszam = 0
        for kulcs, cim in talalatok.items():
            if kulcs not in allapot:
                allapot[kulcs] = MA
                uj_tetelszam += 1
                if not elso_futas and not forras_uj:
                    ujak.append((forras["nev"], cim, kulcs))
        if forras_uj:
            allapot[alap_kulcs] = MA
            if not elso_futas:
                alapozott.append((forras["nev"], uj_tetelszam))
        # Weboldal-adatok frissítése (minden látott tételre, nem csak az újakra)
        for kulcs, cim in talalatok.items():
            t = adatok["tetelek"].setdefault(kulcs, {})
            if not t.get("cim"):
                t["cim"] = cim
            t["forras"] = forras["nev"]
            t["kinek"] = forras["kinek"]
            t["elso"] = allapot.get(kulcs, MA)
            t["utolso"] = MA

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
        for forras_nev, cim, kulcs in ujak:
            if forras_nev != aktualis_forras:
                sorok += ["", f"## {forras_nev}", ""]
                aktualis_forras = forras_nev
            hatarido = hatarido_a_cimben(cim)
            extra = f" — ⚠️ **{hatarido}**" if hatarido else ""
            if kulcs.startswith("nka-kollegium:"):
                sorok.append(f"- **{cim}**{extra} (nka.hu → Kollégiumok felhívásai)")
            else:
                sorok.append(f"- [{cim}]({kulcs}){extra}")
        if any("palyazatok.org" in k for _, _, k in ujak):
            sorok += ["", "_A KKV-találatok hivatalos részletei a palyazat.gov.hu oldalon._"]
    else:
        uj_szam = 0
        sorok.append("Nincs új kiírás.")

    if alapozott:
        sorok += ["", "## Forrás-alapállapot felvéve", ""]
        for nev, db in alapozott:
            sorok.append(f"- {nev}: {db} tétel csendben rögzítve — mostantól csak az újakat jelezzük")

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
