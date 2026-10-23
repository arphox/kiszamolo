# A Kiszámoló blog cikkei és metaadatai

A [Kiszámoló blog](https://kiszamolo.hu/) tudásanyagának helyi gyűjteménye, a letöltéséhez és feldolgozásához készült eszközökkel.
> A kiszamolo.hu szerzője Csernok Miklós; az általa írt cikkek az ő szellemi termékei.

A cél elsőként az, hogy a cikkek könnyebben olvasható és feldolgozható formában legyenek meg. A cikkek témakörei alapján később témák szerinti katalógusok épülhetnek, például egy gyűjtemény a nyugdíjról szóló cikkekből. Hosszabb távon nyílt forrású, közösségi tudásbázist építünk a blog kiegészítéseként.

A cikkek Markdown-változatát elsősorban sima szövegként és AI-eszközökkel használjuk; a szöveghűség mellett az olvashatóság is fontos.

## A GitHubra szánt tudásbázis alapelvei

- A repó tartalmazza a cikkek Markdown-változatát, frissítési indexét és besorolásait.
- A cikkek a kiszamolo.hu tartalmát követik. Szövegüket közvetlenül nem szerkesztjük; a blog változásai és az átalakító javításai alapján frissülnek.
- Az asseteket nem tároljuk helyben vagy Gitben: a képek és mellékletek az eredeti webes URL-jükön szerepelnek. Elérésük internetet és a forrás elérhetőségét igényli.
- A közösségi tudásanyag külön, még nem létező részbe kerül majd; nem a forráscikkeket módosítja.

Ezek az átállás célját rögzítik. A cikkgyűjtemény Gitbe vétele hátralévő feladat: addig várunk vele, amíg elég magabiztosak vagyunk a tartalmában, és nem várható nagyobb módosítás.

## Feladatok és adatok

A `content-mirror/` tölti le és tárolja a blog tartalmát. Az `enrichment/` a mentett cikkekhez saját metaadatokat ad. Mindkettőnek saját automatizációja és tesztjei vannak; a `blog/update.py` együtt indítja őket.

A cím, dátum, forrás-URL és WordPress-azonosító a cikkgyűjteményhez tartozik. A blog saját kategóriáit és címkéit [tudatosan kihagyjuk](content-mirror/README.md#tárolás). A saját témakörök és a tartalmi jelleg szerinti besorolások az `enrichment/` adatai; WordPress-azonosítóval kapcsolódnak a cikkekhez.

A frissítő a `content-mirror/articles/wordpress-modified.json` alapján dönti el, mely cikkeket kell letölteni: az index kizárólag WordPress-azonosítókat és UTC-módosítási időket tartalmaz. A helyi fájlok azonosítója a fájlnevükben is szerepel. A besorolások feldolgozási nyilvántartását külön az enrichment kezeli.

## Mi működik már?

- A gyűjteménybe válogatott nyilvános cikkek és oldalak letöltése és frissítése.
- Olvasható Markdown-fájlok készítése, év és hónap szerinti mappákban.
- A képek és mellékletek eredeti URL-jeinek megőrzése, valamint a helyi cikkek közötti hivatkozások kialakítása.
- Újraépíthető frissítési index készítése.
- A helyi gyűjtemény, a témakörök és a tartalmi jelleg besorolásának közös, kézzel indítható frissítése.

## Mi hol található?

| Mappa vagy fájl | Tartalma |
| --- | --- |
| `blog/content-mirror/articles/` | Az olvasható cikkek és oldalak, saját metaadataikkal. |
| `blog/content-mirror/articles/wordpress-modified.json` | A cikkek azonosítói és WP-módosítási idői a frissítéshez. |
| `blog/content-mirror/articles/exclusions.json` | A [kézzel kizárt rekordok és indoklásuk](content-mirror/README.md#frissítés). |
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

A végén összesítés és teendőlista jelenik meg a terminálon. Ha maradt teendő, a következő futtatáskor folytatható a feldolgozás. Kilépési kód: `0` siker, `2` maradt teendő vagy hiányos letöltés, `1` végzetes hiba, `130` megszakítás. A frissítést kézzel indítjuk.

A csak letöltéshez és az index újraépítéséhez szükséges parancsokat a [letöltő leírása](content-mirror/README.md), a besorolások működését és az emberi döntést igénylő eseteket a [besorolás leírása](enrichment/README.md) tartalmazza.

## Mit érdemes tudni a mentett tartalomról?

- Az olvasható Markdown-fájlok az elsődleges helyi példányok; közvetlenül nem szerkesztjük őket. A frissítő a blog változásaiból készíti az új változatukat, külön nyersforrás-archívum nélkül.
- A letöltő a jelenleg elérhető tartalmat követi; a cikkek korábbi változatairól nem készít történetet. Ha egy cikk eltűnik a blogról, a már mentett helyi példánya megmarad.
- A hozzászólásokat nem gyűjtjük.
- A képek, mellékletek és a külső videólejátszók tartalmai webes hivatkozásként maradnak meg. A gyűjtemény ezért nem minden részében használható internet nélkül; a webes kalkulátorok működését sem másolja le.
- A `blog/content-mirror/articles/` mappában a cikkek és a frissítési index egyelőre ki vannak zárva a Gitből; a cikkek tartalmának stabilizálódására várunk. Addig ezt a mappát külön is menteni kell. A kézzel karbantartott kizárási lista és a besorolási nyilvántartás a repó része.

## Tesztek

A tesztekre a [projekt fejlesztési szabályai](../README.md#fejlesztés-és-dokumentáció) vonatkoznak.

A teljes tesztkészlet futtatása:

```powershell
.\.venv\Scripts\python.exe -m pip install -r blog\content-mirror\automation\requirements-test.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s blog\content-mirror\automation -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s blog/enrichment/automation -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s blog -p "test_update.py"
```
