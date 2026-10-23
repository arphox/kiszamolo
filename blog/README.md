# A Kiszámoló blog cikkei és metaadatai

A [Kiszámoló blog](https://kiszamolo.hu/) tudásanyagának helyi gyűjteménye, a letöltéséhez és feldolgozásához készült eszközökkel.
> A kiszamolo.hu szerzője Csernok Miklós; az általa írt cikkek az ő szellemi termékei.

A cél elsőként az, hogy a cikkek könnyebben olvasható és feldolgozható formában legyenek meg. A cikkek témakörei alapján később témák szerinti katalógusok épülhetnek, például egy gyűjtemény a nyugdíjról szóló cikkekből. Hosszabb távon nyílt forrású, közösségi tudásbázist építünk a blog kiegészítéseként.

A cikkek Markdown-változatát elsősorban sima szövegként és AI-eszközökkel használjuk; a szöveghűség mellett az olvashatóság is fontos.

## A GitHubra szánt tudásbázis alapelvei

- A repó tartalmazza a cikkek Markdown-változatát és a használatukhoz szükséges nyilvántartásokat. A nyers HTML a `.local-cache/` mappába kerül. A letöltő pótolja a hiányzó forrásokat, ha azok még elérhetők a blogon.
- A cikkek a kiszamolo.hu tartalmát követik. Szövegüket közvetlenül nem szerkesztjük; a blog változásai és az átalakító javításai alapján frissülnek.
- Az asseteket nem tároljuk helyben vagy Gitben: a képek és mellékletek az eredeti webes URL-jükön szerepelnek. Elérésük internetet és a forrás elérhetőségét igényli.
- A közösségi tudásanyag külön, még nem létező részbe kerül majd; nem a forráscikkeket módosítja.

Ezek az átállás célját rögzítik. Az alábbi használati leírás a jelenlegi működést mutatja be, amely még a cache-ben tárolt állapotot használ; a cikkgyűjtemény még nincs Gitben.

## Feladatok és adatok

A `content-mirror/` tölti le és tárolja a blog tartalmát. Az `enrichment/` a mentett cikkekhez saját metaadatokat ad. Mindkettőnek saját automatizációja és tesztjei vannak; a `blog/update.py` együtt indítja őket.

A cím, dátum, forrás-URL, WordPress-azonosító és a blog saját kategóriái a cikkgyűjteményhez tartoznak. A saját témakörök és a tartalmi jelleg szerinti besorolások az `enrichment/` adatai; WordPress-azonosítóval kapcsolódnak a cikkekhez.

A letöltési cache helye `.local-cache/blog/content-mirror/`, a repó gyökeréhez képest. A cél az, hogy a teljes `.local-cache/` törölhető legyen adatvesztés nélkül. Jelenleg az offline újragenerálás és a besorolás még használja az itt tárolt forrásokat és állapotot; ezeket egyelőre meg kell őrizni. Az enrichment saját feldolgozási állapota és a cache-független működés további átalakítást igényel.

## Mi működik már?

- A gyűjteménybe válogatott nyilvános cikkek és oldalak letöltése, az eredeti források megőrzésével.
- Olvasható Markdown-fájlok készítése, év és hónap szerinti mappákban.
- A képek és mellékletek eredeti URL-jeinek megőrzése, valamint a helyi cikkek közötti hivatkozások kialakítása.
- Tartalomjegyzék és géppel feldolgozható cikklista készítése.
- A helyi gyűjtemény, a témakörök és a tartalmi jelleg besorolásának közös, kézzel indítható frissítése.

## Mi hol található?

| Mappa vagy fájl | Tartalma |
| --- | --- |
| `blog/content-mirror/articles/` | Az olvasható cikkek és oldalak. A kiindulópont az `INDEX.md`. |
| `.local-cache/blog/content-mirror/` | Az eredeti webes források és a frissítés folytatásához szükséges adatok. |
| `blog/content-mirror/automation/` | A [letöltő és feldolgozó](content-mirror/README.md), a tesztjeivel együtt. |
| `blog/enrichment/` | [Témakörök és tartalmi jelleg](enrichment/README.md), közös nyilvántartás és besoroló. |
| `blog/update.py` | A letöltést és a besorolást összefogó parancs. |

## Telepítés és frissítés

Python 3.10 vagy újabb verzió szükséges. Az alábbi PowerShell-parancsokat a projekt gyökérmappájából futtasd.

Első alkalommal hozz létre egy külön Python-környezetet, és telepítsd a szükséges csomagokat:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r blog\content-mirror\automation\requirements.txt
```

A teljes frissítéshez a Codex CLI és ChatGPT-előfizetéssel történő bejelentkezés is szükséges a besorolások miatt. A letöltés és a későbbi frissítések indítása:

```powershell
.\.venv\Scripts\python.exe -u -B blog/update.py
```

A program letölti a változásokat, majd besorolja a cikkeket témakör és tartalmi jelleg szerint. Legfeljebb öt módosult, már besorolt cikket automatikusan újra besorol. Többnél a hiányzó besorolások elkészítése után egyszer kér jóváhagyást az érintett cikkek együttes darabszámára. Ehhez interaktív terminálban kifejezetten `igen` választ kell adni; átirányított bemenetről nem fogad el jóváhagyást.

A végén összesítés és teendőlista jelenik meg a `blog/content-mirror/articles/update-report.json` fájlban is. Ha maradt teendő, a következő futtatáskor folytatható a feldolgozás. Kilépési kód: `0` siker, `2` maradt teendő vagy hiányos letöltés, `1` végzetes hiba, `130` megszakítás. A frissítést kézzel indítjuk.

A csak letöltéshez és az újrageneráláshoz szükséges parancsokat a [letöltő leírása](content-mirror/README.md), a besorolások működését és az emberi döntést igénylő eseteket a [besorolás leírása](enrichment/README.md) tartalmazza.

## Mit érdemes tudni a mentett tartalomról?

- Az eredeti webes forrásokat jelenleg külön őrizzük meg. Az olvasható Markdown-fájlok ezekből készülnek, és újra előállíthatók; közvetlenül nem szerkesztjük őket.
- A letöltő a jelenleg elérhető tartalmat követi; a cikkek korábbi változatairól nem készít történetet. Ha egy cikk eltűnik a blogról, a már mentett helyi példánya megmarad.
- A hozzászólásokat külön nem gyűjti. A teljes weboldal mentett HTML-forrásában azonban szerepelhetnek hozzászólások.
- A képek, mellékletek és a külső videólejátszók tartalmai webes hivatkozásként maradnak meg. A gyűjtemény ezért nem minden részében használható internet nélkül; a webes kalkulátorok működését sem másolja le.
- A letöltött adatok és az előállított cikkfájlok nincsenek Gitben. A repó a kódot, a dokumentációt és a közös besorolási nyilvántartást tartalmazza. A meglévő gyűjtemény megőrzéséhez a `blog/content-mirror/articles/` és a `.local-cache/blog/content-mirror/` mappát külön is menteni kell.

## Tesztek

A tesztekre a [projekt fejlesztési szabályai](../README.md#fejlesztés-és-dokumentáció) vonatkoznak.

A teljes tesztkészlet futtatása:

```powershell
.\.venv\Scripts\python.exe -m pip install -r blog\content-mirror\automation\requirements-test.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s blog\content-mirror\automation -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s blog/enrichment/automation -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s blog -p "test_update.py"
```
