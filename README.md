# Kiszámoló – helyi cikkgyűjtemény

A [Kiszámoló blog](https://kiszamolo.hu/) tudásanyagának helyi gyűjteménye, a letöltéséhez és feldolgozásához készült eszközökkel.
> A kiszamolo.hu szerzője Csernok Miklós; az általa írt cikkek az ő szellemi termékei.

A cél elsőként az, hogy a cikkek könnyebben olvasható és feldolgozható formában legyenek meg. A cikkek témakörei alapján később témák szerinti katalógusok épülhetnek, például egy gyűjtemény a nyugdíjról szóló cikkekből. Hosszabb távon a projekt a blog szerzőjével közös tudástár kialakítását is segítheti.

A cikkek Markdown-változatát elsősorban sima szövegként és AI-eszközökkel használjuk; a szöveghűség mellett az olvashatóság is fontos.

## Mi működik már?

- A gyűjteménybe válogatott nyilvános cikkek és oldalak letöltése, az eredeti források megőrzésével.
- Olvasható Markdown-fájlok készítése, év és hónap szerinti mappákban.
- A blog saját tárhelyén elérhető képek és mellékletek letöltése, valamint a helyi cikkek közötti hivatkozások kialakítása.
- Tartalomjegyzék és géppel feldolgozható cikklista készítése.
- A helyi gyűjtemény, a témakörök és a tartalmi jelleg besorolásának közös, kézzel indítható frissítése.

## Mi hol található?

| Mappa vagy fájl | Tartalma |
| --- | --- |
| `articles/` | Az olvasható cikkek és oldalak. A kiindulópont az `INDEX.md`. |
| `articles/assets/` | A letöltött képek és mellékletek. |
| `raw_source/content-mirror/` | Az eredeti webes források és a frissítés folytatásához szükséges adatok. |
| `automation/content-mirror/` | A [letöltő és feldolgozó](automation/content-mirror/README.md), a tesztjeivel együtt. |
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

A program letölti a változásokat, majd besorolja a cikkeket témakör és tartalmi jelleg szerint. Legfeljebb öt módosult, már besorolt cikket automatikusan újra besorol. Többnél a hiányzó besorolások elkészítése után egyszer kér jóváhagyást az érintett cikkek együttes darabszámára. Ehhez interaktív terminálban kifejezetten `igen` választ kell adni; átirányított bemenetről nem fogad el jóváhagyást.

A végén összesítés és teendőlista jelenik meg az `articles/update-report.json` fájlban is. Ha maradt teendő, a következő futtatáskor folytatható a feldolgozás. Kilépési kód: `0` siker, `2` maradt teendő vagy hiányos letöltés, `1` végzetes hiba, `130` megszakítás. A frissítést kézzel indítjuk.

A csak letöltéshez és az újrageneráláshoz szükséges parancsokat a [letöltő leírása](automation/content-mirror/README.md), a besorolások működését és az emberi döntést igénylő eseteket a [besorolás leírása](classification/README.md) tartalmazza.

## Mit érdemes tudni a mentett tartalomról?

- Az eredeti webes forrásokat külön őrizzük meg. Az olvasható Markdown-fájlok ezekből készülnek, és újra előállíthatók. Kézi módosításaik egy későbbi futás során felülíródhatnak.
- A letöltő a jelenleg elérhető tartalmat követi; a cikkek korábbi változatairól nem készít történetet. Ha egy cikk eltűnik a blogról, a már mentett helyi példánya megmarad.
- A hozzászólásokat külön nem gyűjti. A teljes weboldal mentett HTML-forrásában azonban szerepelhetnek hozzászólások.
- Más webhelyek fájljai és a külső videólejátszók tartalmai hivatkozásként maradnak meg. A gyűjtemény ezért nem minden részében használható internet nélkül; a webes kalkulátorok működését sem másolja le.
- A letöltött adatok és az előállított cikkfájlok nincsenek Gitben. A repó a kódot, a dokumentációt és a közös besorolási nyilvántartást tartalmazza. A meglévő gyűjtemény megőrzéséhez az `articles/` és a `raw_source/` mappát külön is menteni kell.

## Tesztek

Az automata tesztek nem érhetnek el külső hálózatot és nem hívhatnak valódi AI-szolgáltatást. A külső szolgáltatásokat mockok helyettesítik; helyi HTTP-tesztkiszolgáló használható.

A teljes tesztkészlet futtatása:

```powershell
.\.venv\Scripts\python.exe -m pip install -r automation\content-mirror\requirements-test.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s automation\content-mirror -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s classification -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s automation -p "test_update.py"
```

## Dokumentáció és nyelvhasználat

- Minden szabályt és magyarázatot a hozzá tartozó dokumentumban tartunk fenn; máshonnan hivatkozunk rá, az ismétlés helyett.
- A tartós döntéseket és a használathoz szükséges működést dokumentáljuk. A kódból kiolvasható apró részleteket és a lezárult fejlesztések történetét kihagyjuk.
- Angolul írjuk a forráskódot, a kódkommenteket, a Githez kapcsolódó szövegeket és az `AGENTS.md` fájlt. A többi dokumentáció, a cikkek és a szöveges tartalom magyar nyelvű. A bevett technikai kifejezéseket megtartjuk, ha a fordításuk erőltetett lenne.
