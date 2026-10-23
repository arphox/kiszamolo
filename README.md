# Kiszámoló – helyi cikkgyűjtemény

A [Kiszámoló blog](https://kiszamolo.hu/) tudásanyagának helyi gyűjteménye, a letöltéséhez és feldolgozásához készült eszközökkel.
> A kiszamolo.hu szerzője Csernok Miklós; az általa írt cikkek az ő szellemi termékei.

A cél elsőként az, hogy a cikkek könnyebben olvasható és feldolgozható formában legyenek meg. A cikkek témakörei alapján később témák szerinti katalógusok épülhetnek, például egy gyűjtemény a nyugdíjról szóló cikkekből. Hosszabb távon a projekt a blog szerzőjével közös tudástár kialakítását is segítheti.

A cikkek Markdown-változatát elsősorban sima szövegként és AI-eszközökkel használjuk; a szöveghűség mellett az olvashatóság is fontos.

## Mi működik már?

- A gyűjteménybe válogatott nyilvános cikkek és oldalak letöltése, az eredeti források megőrzésével.
- Olvasható Markdown-fájlok készítése, év és hónap szerinti mappákban. A Markdown egyszerű szöveges formátum, amely a címeket, listákat és hivatkozásokat is megőrzi.
- A blog saját tárhelyén elérhető képek és mellékletek letöltése, valamint a helyi cikkek közötti hivatkozások kialakítása.
- Tartalomjegyzék és géppel feldolgozható cikklista készítése.
- A helyi gyűjtemény, a témakörök és a tartalmi jelleg besorolásának közös, kézzel indítható frissítése.

A [cikkek besorolásai](classification/README.md) a `classification/assignments.json`
fájlban vannak, a témakörök neve és jelentése a `classification/topics.json` fájlban.
Az összerendelések Gitben követhető szöveges adatok. A blog saját kategóriáit
és címkéit a letöltő ettől függetlenül megőrzi.

## Mi hol található?

| Mappa vagy fájl | Tartalma |
| --- | --- |
| `articles/` | Az olvasható cikkek és oldalak. A kiindulópont az `INDEX.md`. |
| `articles/assets/` | A letöltött képek és mellékletek. |
| `raw_source/content-mirror/` | Az eredeti webes források és a frissítés folytatásához szükséges adatok. |
| `automation/content-mirror/` | A jelenleg használt letöltő és feldolgozó, a tesztjeivel együtt. |
| `classification/` | [Témakörök és tartalmi jelleg](classification/README.md), közös nyilvántartás és besoroló. |
| `update.py` | A letöltést és a besorolást összefogó parancs. |

## Telepítés és frissítés

Python 3.10 vagy újabb verzió szükséges. Az alábbi PowerShell-parancsokat a projekt gyökérmappájából futtasd.

Első alkalommal hozz létre egy külön Python-környezetet, és telepítsd a szükséges csomagokat:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r automation\content-mirror\requirements.txt
```

A teljes frissítéshez a Codex CLI és ChatGPT-előfizetéssel történő bejelentkezés is szükséges a besorolások miatt. A letöltés és a későbbi frissítések indítása:

```powershell
.\.venv\Scripts\python.exe -u -B update.py
```

A program előbb frissíti az `articles/` és a `raw_source/content-mirror/` mappát, majd elvégzi a témakörök és a tartalmi jelleg besorolását. A közös besoroló és nyilvántartás két külön célú modellhívást használ, közös tartalom-előkészítéssel. Legfeljebb öt módosult, már besorolt cikket automatikusan újra besorol; többnél kiírja az érintett cikkek együttes darabszámát, és az új besorolások feldolgozása után egyszer kér emberi jóváhagyást. Ehhez interaktív terminálban kifejezetten `igen` választ kell adni; átirányított bemenetről nem fogad el jóváhagyást.

A besorolásra váró cikkeket a letöltő cikknyilvántartása külön tartja számon a két szempont szerint. Egyik besorolás hibája sem akadályozza a másik feldolgozását; a sikeres részt nem kell a hibás rész miatt megismételni.

A letöltő a WordPress módosítási időbélyege alapján frissít. A helyi feldolgozás az új és módosult cikkekre korlátozódik. Pótolja a hiányzó helyi fájlokat, és csak az átmeneti letöltési hibákat próbálja újra a következő futáskor. Átnevezéskor külön javítja a rá mutató helyi hivatkozásokat.

A hiányzó szükséges tartalom vagy hibás besorolás nem állítja le a többi cikk feldolgozását; az adott szempont korábbi eredménye megmarad, és a függő jelzés mutatja, hogy frissítésre vár. Az elutasított újrabesorolások és a hibák következő futáskor ismét sorra kerülnek. A közös tartalmi szabályokat és az emberi döntést igénylő eseteket a [besorolás leírása](classification/README.md#közös-szabályok) tartalmazza.

A végén összesítés és teendőlista jelenik meg. A teljes folyamat riportja az `articles/update-report.json`, a letöltésé az `articles/last-report.json`. Kilépési kód: `0` siker, `2` maradt teendő vagy hiányos letöltés, `1` végzetes hiba, `130` megszakítás. Jelenleg igény szerint, kézzel indítjuk; nincs beállított automatikus frissítés.

Csak letöltés: `python -u -B automation/content-mirror/sync.py`.

A további kapcsolókat, a hibakódokat és a hálózat nélküli újragenerálás menetét a [letöltő részletes leírása](automation/content-mirror/README.md) tartalmazza.

## Mit érdemes tudni a mentett tartalomról?

- Az eredeti webes forrásokat külön őrizzük meg. Az olvasható Markdown-fájlok ezekből készülnek, és újra előállíthatók. Kézi módosításaik egy későbbi futás során felülíródhatnak.
- A letöltő a jelenleg elérhető tartalmat követi; a cikkek korábbi változatairól nem készít történetet. Ha egy cikk eltűnik a blogról, a már mentett helyi példánya megmarad.
- A hozzászólásokat külön nem gyűjti. A teljes weboldal mentett HTML-forrásában azonban szerepelhetnek hozzászólások.
- Más webhelyek fájljai és a külső videólejátszók tartalmai hivatkozásként maradnak meg. A gyűjtemény ezért nem minden részében használható internet nélkül; a webes kalkulátorok működését sem másolja le.
- A letöltött adatok és az előállított cikkfájlok nincsenek Gitben. A repó a kódot, a dokumentációt és a közös besorolási nyilvántartást tartalmazza. A meglévő gyűjtemény megőrzéséhez az `articles/` és a `raw_source/` mappát külön is menteni kell.

## Tesztek

Az automata tesztek nem érhetnek el külső hálózatot és nem hívhatnak valódi AI-szolgáltatást. A külső szolgáltatásokat mockok helyettesítik; helyi HTTP-tesztkiszolgáló használható.

A projekt tesztjei:

```powershell
.\.venv\Scripts\python.exe -m pip install -r automation\content-mirror\requirements-test.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s automation\content-mirror -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s classification -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s automation -p "test_update.py"
```

## Dokumentáció és nyelvhasználat

Minden szabályt és információt egy helyen dokumentálunk. Más dokumentumokból hivatkozunk rá, az ismétlés helyett.

- **Angol:** az `AGENTS.md`, a Githez kapcsolódó szövegek (például commitüzenetek, branch- és Git-tag-nevek), a forráskód és a kódkommentek.
- **Magyar:** a szöveges dokumentumok (például a README-k), a cikkek és a többi szöveges tartalom.

A bevett technikai kifejezéseket (például branch, commit, Git-tag) magyar szövegben is megtartjuk; nem fordítjuk őket erőltetetten magyarra.
