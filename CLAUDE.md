# CLAUDE.md — Pályázatradar

## Mi ez

Napi pályázatfigyelő Attila közösségi-kulturális központ projektjéhez (kft + egyesület).
GitHub Actions futtatja naponta; új kiírásnál Issue-t nyit.

- **Repo:** https://github.com/nemelvagyunk/palyazatradar (PUBLIKUS, fiók: `nemelvagyunk`)
- **Lokális mappa:** `C:\Users\B650\Claude\Projects\Ovi kultúrális központ\palyazatradar-github` — ez a git repo gyökere, origin a fenti repo
- Élesítve: 2026-07-22. Első futás OK (alapállapot: 10 forrás, 2922 tétel, Issue #1).

## Architektúra

| Fájl | Szerep |
|------|--------|
| `radar.py` | Minden logika egyben: `FORRASOK` (13 forrás), letöltés (requests), linkkigyűjtés (BeautifulSoup), `KULCSSZAVAK`-szűrés, URL-normalizálás, NKA- és RSS-speciális kezelés, forrásonkénti csendes alapállapot-felvétel, allapot-diff, `report.md` írás, `GITHUB_OUTPUT`-ba `new_count` + `first_run` + `baselined` |
| `.github/workflows/radar.yml` | Cron: `30 5 * * *` (UTC!) + kézi `workflow_dispatch`; jogok: `contents: write`, `issues: write`; lépések: futtatás → allapot.json commit/push → Issue nyitás `gh issue create`-tel |
| `allapot.json` | Kulcs: normalizált URL, `nka-kollegium:<név>` vagy `forras-alap:<forrásnév>` (utóbbi: a forrás alapállapota már felvéve); érték: első észlelés dátuma. **A bot commitolja naponta** — kézzel ne szerkeszd, munka előtt mindig `git pull`! |
| `report.md` | Futásonkénti riport, gitignore-olva |

## Fejlesztési workflow

1. `git pull` (a bot naponta commitol az `allapot.json`-ba!)
2. Módosítás a `radar.py`-ban / `radar.yml`-ben
3. Helyi teszt: `python radar.py --state teszt_allapot.json --report teszt_report.md` — **SOHA ne az éles `allapot.json`-nal tesztelj** (elnyelné az új találatokat); a tesztfájlokat ne commitold
4. Commit + push → kézi ellenőrzés: GitHub → Actions → Pályázatradar → Run workflow
5. Push GitHub Desktopból is mehet (a "palyazatradar" repo be van állítva benne); CLI push először hitelesítést kérhet

## Fontos tudnivalók / gotchák

- **A repo publikus** — érzékeny adat (üzleti fájlok, kulcsok, tokenek) soha ne kerüljön bele.
- **A szülőmappa ("Ovi kultúrális központ") NEM git repo, és ne is legyen az.** 2026-07-22-én incidens volt: az egész projektmappa (könyvelés, pénzügyi modell) véletlenül felment egy publikus "radar1" repóba — törölve lett, a gyökér `.git` eltávolítva. Ne ismételjük meg.
- `palyazat.gov.hu`: a HTML JS-alapú, géppel olvashatatlan. Van hivatalos RSS-e (`/rss.xml`, statikus XML), **de az oldal geo-blokkolja a külföldi IP-ket** — GitHub-hostolt runnerről `Connection refused` (2026-07-22-én kétszer igazolva; magyar IP-ről HTTP 200). **Ne próbáld újra Actions-ből** — helyette a `palyazatok.org` kategóriák a proxy (kkv + civil + kulturális/művészeti). A `rss_tetelek()` feldolgozó + `RSS_KIZARAS` a kódban maradt (`special: "rss"`), más feedhez újrahasznosítható.
- `pafi.hu`: aggregátor, minden `/p/` útvonalú link pályázat, de a címben gyakran nincs kulcsszó → forrás-opciók: `utvonal_elotag: "/p/"` + `kulcsszo_nelkul: True` (a kulcsszó-szűrés kikapcsolva, az előtag szűr). Az első 3 listaoldalt figyeljük (legfrissebbek elöl).
- **Új forrás felvétele biztonságos:** az első sikeres beolvasás csendes — a tételek `allapot.json`-ba kerülnek, de nem jelennek meg találatként, és a forrás `forras-alap:<név>` markert kap. Így egy új forrás nem árasztja el az Issue-t. (Az Issue ilyenkor is nyílik, „alapállapot felvéve" címmel.)
- `kultura.kreativeuropa.hu` iso-8859-2 kódolású — a `fetch()` kezeli, ne "javítsd ki".
- NKA: az oldal kollégiumonként jelzi a "(felhívás elérhető)" állapotot — ez találat akkor is, ha nem új URL (`nka-kollegium:` kulcs).
- Kulcsszó-szűrés a címre + URL-útvonalra megy, a **domain szándékosan nem számít** (különben a palyazatok.org minden linkje találat lenne).
- Értesítés e-mailben csak akkor jön, ha Attila a repónál bekapcsolta: Watch → All activity.
- A régi Cowork-os ütemezett radar ("napi-palyazat-radar") kikapcsolva; a régi állapotfájlja (`..\palyazatradar_allapot.json`, 253 tétel) a szülőmappában maradt, már nem frissül.

## Tervezett fejlesztési irányok (prioritás még nincs eldöntve)

1. **Határidő + összeg kinyerése** — az új találatok oldalát letöltve beadási határidő / keret / jogosultak kiszedése az Issue-ba
2. **Relevancia-pontozás** — a projektprofilra (kultúrház, koncert, közösségi tér, civil, felújítás, ifjúsági) pontozni a találatokat, relevánsak előre
3. **GitHub Pages weboldal** — kereshető/szűrhető lista az összes találatból
4. **Heti összefoglaló** — hétfőnként rendezett digest a friss + közelgő határidejű pályázatokról

(Volt egy korábbi, azóta törölt prototípus — sitegen + RSS + SQLite ötletekkel; az irányok újrahasznosíthatók.)
