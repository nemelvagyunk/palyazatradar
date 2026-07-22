# Pályázatradar

**Weboldal: https://nemelvagyunk.github.io/palyazatradar/** — kereshető, szűrhető lista
az összes találatból, naponta frissül.

Napi pályázatfigyelő közösségi-kulturális központ projekthez (kft + egyesület struktúra).
GitHub Actions futtatja minden reggel: letölti a forrásoldalakat, összeveti az előző
állapottal, és **új kiírás esetén GitHub Issue-t nyit** a találatokkal. A GitHub az
Issue-ról automatikusan e-mail értesítést küld (ha a repo-t figyeled / a tiéd).

## Figyelt források

| # | Forrás | Kinek |
|---|--------|-------|
| 1 | Norvég Civil Alap | egyesület |
| 2 | Hangfoglaló | klubtámogatás |
| 3 | NKA kollégiumi felhívások¹ | egyesület |
| 4 | NEA (Bethlen Gábor Alapkezelő) | egyesület |
| 5 | Erasmus+ / ESC | egyesület |
| 6 | Visegrádi Alap | egyesület |
| 7 | Kreatív Európa Kultúra | egyesület |
| 8 | KKV / energetika (palyazatok.org)² | kft |
| 9 | Budapest Főváros (civil + zöld) | egyesület |
| 10 | Józsefváros | mindkettő |
| 11 | PAFI (pafi.hu)³ | mindkettő |
| 12 | Civil (palyazatok.org)² | egyesület |
| 13 | Kulturális / művészeti (palyazatok.org)² | mindkettő |

¹ Az NKA oldala kollégiumonként jelzi a „(felhívás elérhető)" állapotot — ez akkor is
találat, ha nem új URL.
² A palyazat.gov.hu JS-alapú és geo-blokkolt (GitHub-runnerről nem elérhető), ezért ez
az aggregátor a proxy; a hivatalos részletek mindig a palyazat.gov.hu-n.
³ Aggregátor: az első 3 listaoldal, kulcsszó-szűrés nélkül (minden `/p/` link pályázat).

## Telepítés

1. Hozz létre egy (akár privát) GitHub repo-t, és töltsd fel ezeket a fájlokat.
2. A repo **Settings → Actions → General → Workflow permissions** alatt engedélyezd:
   *Read and write permissions*.
3. Kész. Az első futás (indítható kézzel is: **Actions → Pályázatradar → Run workflow**)
   felveszi az alapállapotot (`allapot.json`), utána már csak az új kiírásokról nyit Issue-t.

## Működés

- `radar.py` — letölti a 13 forrást, kigyűjti a linkeket, kulcsszavakra szűr
  (pályáz/felhív/kiírás/grant/call/támogat/ösztöndíj/funding — a domain nem számít),
  normalizálja az URL-eket (utm_*, fbclid, token, hash törlése). Az új találatok
  cikkoldaláról kinyeri a megjelenési dátumot és a beadási határidőt; csak a
  2026-07-20 után megjelent kiírás számít valódi újdonságnak (a régebbi csendben
  rögzül). Tömeges-álriasztás védelem és figyelt aloldalak (változás-diff) is van.
- `allapot.json` — melyik tételt mikor láttuk először (a bot commitolja).
- `docs/` — a weboldal: `index.html` + `adatok.json` (cím, forrás, első/utolsó észlelés,
  megjelenés + határidő; a bot frissíti naponta, GitHub Pages szolgálja ki).
- `oldalak.json` + `data/pages/` — a figyelt aloldalak változás-állapota (a bot kezeli).
- `report.md` — az adott futás riportja (nem kerül a repo-ba).
- `.github/workflows/radar.yml` — napi cron (05:30 UTC), állapot-commit, Issue-nyitás.

## Helyi futtatás

```bash
pip install -r requirements.txt
python radar.py --state allapot.json --report report.md
```

## Testreszabás

- **Források**: `radar.py` → `FORRASOK` lista. Új forrás felvétele biztonságos: az első
  sikeres beolvasás csendben csak alapállapotot vesz fel, nem árasztja el az Issue-t.
- **Kulcsszavak / kizárások**: `KULCSSZAVAK`, `KIZARAS` konstansok.
- **Időzítés**: `radar.yml` → `cron` sor (UTC-ben!).
- Ha egy oldal tartósan nem elérhető, a riport végén jelzi a futás.
