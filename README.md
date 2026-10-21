# Kiszámoló – helyi cikkgyűjtemény

A [Kiszámoló blog](https://kiszamolo.hu/) tudásanyagának helyi gyűjteménye, a letöltéséhez és feldolgozásához készült eszközökkel.
> A kiszamolo.hu szerzője Csernok Miklós; az általa írt cikkek az ő szellemi termékei.

A cél elsőként az, hogy a cikkek könnyebben olvasható és feldolgozható formában legyenek meg. A cikk–tag összerendelésekre később témák szerinti katalógusok épülhetnek, például egy gyűjtemény a nyugdíjról szóló cikkekből. Hosszabb távon a projekt a blog szerzőjével közös tudástár kialakítását is segítheti.

A cikkek Markdown-változatát elsősorban sima szövegként és AI-eszközökkel használjuk; a szöveghűség mellett az olvashatóság is fontos.

## Mi működik már?

- A gyűjteménybe válogatott nyilvános cikkek és oldalak letöltése, az eredeti források megőrzésével.
- Olvasható Markdown-fájlok készítése, év és hónap szerinti mappákban. A Markdown egyszerű szöveges formátum, amely a címeket, listákat és hivatkozásokat is megőrzi.
- A blog saját tárhelyén elérhető képek és mellékletek letöltése, valamint a helyi cikkek közötti hivatkozások kialakítása.
- Tartalomjegyzék és géppel feldolgozható cikklista készítése.
- A helyi gyűjtemény kézi, igény szerinti frissítése.

A [cikk–tag összerendelések](tagging/README.md) a `tagging/assignments.json`
fájlban vannak, a tagok neve és jelentése a `tagging/tags.json` fájlban.
Az összerendelések Gitben követhető szöveges adatok. A blog saját kategóriáit
és címkéit a letöltő ettől függetlenül megőrzi.

## Mi hol található?

| Mappa vagy fájl | Tartalma |
| --- | --- |
| `articles/` | Az olvasható cikkek és oldalak. A kiindulópont az `INDEX.md`. |
| `articles/assets/` | A letöltött képek és mellékletek. |
| `raw_source/content-mirror/` | Az eredeti webes források és a frissítés folytatásához szükséges adatok. |
| `automation/content-mirror/` | A jelenleg használt letöltő és feldolgozó, a tesztjeivel együtt. |
| `tagging/` | Cikk–tag összerendelések, tagdefiníciók és a címkéző. |

## Telepítés és frissítés

Python 3.10 vagy újabb verzió szükséges. Az alábbi PowerShell-parancsokat a projekt gyökérmappájából futtasd.

Első alkalommal hozz létre egy külön Python-környezetet, és telepítsd a szükséges csomagokat:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r automation\content-mirror\requirements.txt
```

A letöltés vagy egy későbbi frissítés indítása:

```powershell
.\.venv\Scripts\python.exe -u -B automation\content-mirror\sync.py
```

A program elkészíti vagy frissíti az `articles/` és a `raw_source/content-mirror/` mappát. A letöltési hibákat az `articles/last-report.json` fájlban jelzi. Jelenleg igény szerint, kézzel indítjuk; nincs beállított automatikus frissítés.

A további kapcsolókat, a hibakódokat és a hálózat nélküli újragenerálás menetét a [letöltő részletes leírása](automation/content-mirror/README.md) tartalmazza.

## Mit érdemes tudni a mentett tartalomról?

- Az eredeti webes forrásokat külön őrizzük meg. Az olvasható Markdown-fájlok ezekből készülnek, és újra előállíthatók. Kézi módosításaik egy későbbi futás során felülíródhatnak.
- A letöltő a jelenleg elérhető tartalmat követi; a cikkek korábbi változatairól nem készít történetet. Ha egy cikk eltűnik a blogról, a már mentett helyi példánya megmarad.
- A hozzászólásokat külön nem gyűjti. A teljes weboldal mentett HTML-forrásában azonban szerepelhetnek hozzászólások.
- Más webhelyek fájljai és a külső videólejátszók tartalmai hivatkozásként maradnak meg. A gyűjtemény ezért nem minden részében használható internet nélkül; a webes kalkulátorok működését sem másolja le.
- A letöltött adatok és az előállított cikkfájlok nincsenek Gitben. A repó a kódot, a dokumentációt és a cikk–tag összerendeléseket tartalmazza. A meglévő gyűjtemény megőrzéséhez az `articles/` és a `raw_source/` mappát külön is menteni kell.

## Tesztek

A jelenlegi letöltő tesztjei:

```powershell
.\.venv\Scripts\python.exe -m pip install -r automation\content-mirror\requirements-test.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s automation\content-mirror -p "test_*.py"
```

A tesztek nem kapcsolódnak a bloghoz; egy részük helyi HTTP-kiszolgálót használ.

## Nyelvhasználat

- **Angol:** az `AGENTS.md`, a Githez kapcsolódó szövegek (például commitüzenetek, branch- és tagnevek), a forráskód és a kódkommentek.
- **Magyar:** a szöveges dokumentumok (például a README-k), a cikkek és a többi szöveges tartalom.

A bevett technikai kifejezéseket (például branch, commit, tag) magyar szövegben is megtartjuk; nem fordítjuk őket erőltetetten magyarra.
