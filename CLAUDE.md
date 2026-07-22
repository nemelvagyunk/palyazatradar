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
| `radar.py` | Minden logika egyben: `FORRASOK` (13 forrás), letöltés (requests), linkkigyűjtés (BeautifulSoup), `KULCSSZAVAK`-szűrés, URL-normalizálás, NKA- és RSS-speciális kezelés, forrásonkénti csendes alapállapot-felvétel, bulk-guard, tétel-dúsítás (megjelenés + határidő), `UJ_HATAR` cutoff, watch-oldalak (hash+diff), allapot-diff, `report.md` írás, `GITHUB_OUTPUT`-ba `new_count` + `first_run` + `baselined` + `changes` |
| `.github/workflows/radar.yml` | Cron: `30 5 * * *` (UTC!) + kézi `workflow_dispatch`; jogok: `contents: write`, `issues: write`; lépések: futtatás → allapot.json commit/push → Issue nyitás `gh issue create`-tel |
| `allapot.json` | Kulcs: normalizált URL, `nka-kollegium:<név>` vagy `forras-alap:<forrásnév>` (utóbbi: a forrás alapállapota már felvéve); érték: első észlelés dátuma. **A bot commitolja naponta** — kézzel ne szerkeszd, munka előtt mindig `git pull`! |
| `docs/adatok.json` | A weboldal adatfájlja: `{frissitve, tetelek: {kulcs: {cim, forras, kinek, elso, utolso, megjelent?, hatarido?}}}`. **Ezt is a bot commitolja naponta.** |
| `oldalak.json` + `data/pages/` | A watch-oldalak hash-állapota + normalizált szövegcache (a diff-kivonathoz). **A bot commitolja.** |
| `docs/index.html` | Publikus weboldal (GitHub Pages, main ág `/docs`): kereshető/szűrhető lista — https://nemelvagyunk.github.io/palyazatradar/ |
| `report.md` | Futásonkénti riport, gitignore-olva |

## Fejlesztési workflow

1. `git pull` (a bot naponta commitol az `allapot.json`-ba!)
2. Módosítás a `radar.py`-ban / `radar.yml`-ben
3. Helyi teszt: `python radar.py --state teszt_allapot.json --report teszt_report.md --adatok teszt_adatok.json --oldalak teszt_oldalak.json --cache teszt_pages` — **SOHA ne az éles állapotfájlokkal tesztelj** (elnyelné az új találatokat / felülírná az éles cache-t); a tesztfájlokat ne commitold
4. Commit + push → kézi ellenőrzés: GitHub → Actions → Pályázatradar → Run workflow
5. Push GitHub Desktopból is mehet (a "palyazatradar" repo be van állítva benne); CLI push először hitelesítést kérhet

## Fontos tudnivalók / gotchák

- **A repo publikus** — érzékeny adat (üzleti fájlok, kulcsok, tokenek) soha ne kerüljön bele.
- **A szülőmappa ("Ovi kultúrális központ") NEM git repo, és ne is legyen az.** 2026-07-22-én incidens volt: az egész projektmappa (könyvelés, pénzügyi modell) véletlenül felment egy publikus "radar1" repóba — törölve lett, a gyökér `.git` eltávolítva. Ne ismételjük meg.
- `palyazat.gov.hu`: a HTML JS-alapú, géppel olvashatatlan. Van hivatalos RSS-e (`/rss.xml`, statikus XML), **de az oldal geo-blokkolja a külföldi IP-ket** — GitHub-hostolt runnerről `Connection refused` (2026-07-22-én kétszer igazolva; magyar IP-ről HTTP 200). **Ne próbáld újra Actions-ből** — helyette a `palyazatok.org` kategóriák a proxy (kkv + civil + kulturális/művészeti). A `rss_tetelek()` feldolgozó + `RSS_KIZARAS` a kódban maradt (`special: "rss"`), más feedhez újrahasznosítható.
- `pafi.hu`: aggregátor, minden `/p/` útvonalú link pályázat, de a címben gyakran nincs kulcsszó → forrás-opciók: `utvonal_elotag: "/p/"` + `kulcsszo_nelkul: True` (a kulcsszó-szűrés kikapcsolva, az előtag szűr). Az első 3 listaoldalt figyeljük (legfrissebbek elöl).
- **Új forrás felvétele biztonságos:** az első sikeres beolvasás csendes — a tételek `allapot.json`-ba kerülnek, de nem jelennek meg találatként, és a forrás `forras-alap:<név>` markert kap. Így egy új forrás nem árasztja el az Issue-t. (Az Issue ilyenkor is nyílik, „alapállapot felvéve" címmel.)
- **Bulk-guard:** már alapozott forrásnál ha egyszerre >12 „új" jön ÉS ez a forrás találatainak >60%-a, az oldalszerkezet-változás → csendes rögzítés, riasztás helyett Megjegyzés a riportban.
- **`UJ_HATAR` cutoff (2026-07-20):** csak az ez után MEGJELENT kiírás számít valódi újdonságnak. Az új tételek cikkoldalát a radar letölti (max `DUSITAS_LIMIT`/futás), kinyeri a megjelenési dátumot + határidőt (magyar dátumminták, „határidő" kulcsszó-környezet). Régi megjelenésű / lejárt határidejű tétel csendben rögzül (a weboldalon látszik). Ha az oldal nem tölthető le → kétség esetén ÚJ. A kinyert `megjelent`/`hatarido` a `docs/adatok.json`-ba kerül, a weboldal mutatja (≤14 nap: piros).
- **Watch-oldalak (`WATCH_OLDALAK`):** csak változást figyelünk (normalizált szöveg hash + diff-kivonat: mi került fel). Első látás csendes. Változásnál Issue nyílik („változás a figyelt aloldalakon"), a riportban a friss sorok. Jelenleg: BGA Városi Civil Alap, NKA miniszteri, norvegcivilalap.hu, Józsefváros hirdetőtábla.
- **A régi prototípus** (`..\palyazat-radar` mappa — sources.yaml, parsers, SQLite, sitegen) ötletforrásként megmaradt; a zajszűrés, bulk-guard, dúsítás, cutoff és watch innen lett átemelve 2026-07-22-én.
- `kultura.kreativeuropa.hu` iso-8859-2 kódolású — a `fetch()` kezeli, ne "javítsd ki".
- NKA: az oldal kollégiumonként jelzi a "(felhívás elérhető)" állapotot — ez találat akkor is, ha nem új URL (`nka-kollegium:` kulcs).
- Kulcsszó-szűrés a címre + URL-útvonalra megy, a **domain szándékosan nem számít** (különben a palyazatok.org minden linkje találat lenne).
- Értesítés e-mailben csak akkor jön, ha Attila a repónál bekapcsolta: Watch → All activity.
- A régi Cowork-os ütemezett radar ("napi-palyazat-radar") kikapcsolva; a régi állapotfájlja (`..\palyazatradar_allapot.json`, 253 tétel) a szülőmappában maradt, már nem frissül.

## Weboldal (2026-07-22 óta él)

- https://nemelvagyunk.github.io/palyazatradar/ — GitHub Pages, main ág `/docs` mappából, minden pushnál újraépül.
- Egyetlen statikus `docs/index.html` (vanilla JS, függőség nélkül), a `docs/adatok.json`-t olvassa.
- Az `adatok.json`-t a `radar.py` vezeti minden futáskor: minden LÁTOTT tételt frissít (`utolso`=ma), az `elso` az allapot.json-beli első észlelés. Cím: az első nem üres marad.
- „ÚJ" jelölés: `elso` legfeljebb 7 nappal a `frissitve` előtt. „Már nem listázott": `utolso` < `frissitve`.
- NKA-kulcsú (`nka-kollegium:`) tételek linkje a NKA kollégiumi felhívások oldalára mutat.

## Tervezett fejlesztési irányok (prioritás még nincs eldöntve)

1. ~~**Határidő kinyerése**~~ — ✅ kész (dúsítás; az összeg/keret kinyerése még nyitott)
2. **Relevancia-pontozás** — a projektprofilra (kultúrház, koncert, közösségi tér, civil, felújítás, ifjúsági) pontozni a találatokat, relevánsak előre
3. ~~**GitHub Pages weboldal**~~ — ✅ kész (lásd fent)
4. **Heti összefoglaló** — hétfőnként rendezett digest a friss + közelgő határidejű pályázatokról
5. **Napi e-mail + RSS** — a régi prototípusban kész recept van rá (Gmail app-jelszó + secrets, feed.xml), Attila egyelőre nem kérte

(Volt egy korábbi, azóta törölt prototípus — sitegen + RSS + SQLite ötletekkel; az irányok újrahasznosíthatók.)
