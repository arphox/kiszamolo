# Témakörök szerinti besorolás

Az `assignments.json` tartalmazza a cikkek témaköreit. A témakörök nevét,
definícióját, szinonimáit és tágabb témaköreit a `topics.json` határozza meg.

## Cikkek és összerendelések

A cikk azonosítójával keresd ki a rekordot az `articles/index.jsonl` fájlban:
itt találod a címet és a helyi útvonalat. Az útvonal az `articles/` mappán
belüli Markdown-fájlra mutat.

Az `assignments.json` gyökerében az `articles` objektum kulcsai a cikkazonosítók,
például `"12345"`. A bejegyzés `topics` mezője a cikkhez rendelt
témakörazonosítók rendezett listája.

A fájl UTF-8 kódolású, LF sorvégű, záró soremeléssel; minden cikkbejegyzés egy
sorban áll. A cikkazonosítók számszerű sorrendben, a témakörazonosítók egyedien
és ábécérendben szerepelnek.

## Témakörválasztás

- A `topics.json` definíciói az irányadók. Csak az ott szereplő
  témakörazonosítókat használd; új vagy összevont témakörről a felhasználó dönt.
- Minden megtartott cikkhez legalább egy témakör kell. Ha egyik témakör sem illik
  rá, és a cikk kizárása sem indokolt, humán mérlegelést kell kérni a besorolásról
  vagy a fogalomtár bővítéséről. Üres témakörlistával nem véglegesíthető.
- A témakör a blog tudásanyagán belül hasznos, önálló keresési témát jelöljön.
  Egyszeri, lazán kapcsolódó külső érdekesség miatt nem bővítjük a fogalomtárat;
  az ilyen bejegyzést kizárjuk.
- Rövid, de érdemi melléktéma is kaphat saját témakört; a puszta említés,
  reklám, felsorolás vagy kapcsolódó cikk ajánlója önmagában nem elég.
- Egy cikk több, egymást átfedő témakörbe is besorolható. A tágabb témakört is a
  cikk tartalma és a definíció alapján válaszd ki; a `broader` kapcsolat nem
  jelent automatikus öröklést.
- Az adósságfék, hitelkiváltás és váltó a **Hitelek és tartozások** témakörhöz
  tartozik. A családon belüli anyagi segítséget, az etikus és vallási
  befektetést, valamint a garázsbefektetést is a meglévő témakörökkel soroljuk
  be, külön témakör nélkül.
- Vásárlói csoportnál a csoporttagságból önmagában nem következtetünk jogi státuszra;
  a NOK nem automatikus szinonima.
- Régi és nem pénzügyi cikkeket is értékelünk.
- Bizonytalan témakörhatárnál fogalmazz meg rövid javaslatot, és kérdezd meg a
  felhasználót. Új témakör helyett a hiányzó témát jelezd.

## AI-session szabály

Egy AI-session kizárólag egy cikket elemezhet. A session a teljes cikkszöveget
megkaphatja, de más cikk szövege vagy korábbi elemzése nem kerülhet bele.
Újrapróbálkozáskor is új sessiont kell indítani.

## A besorolás menete

1. A cikkazonosító alapján keresd meg a Markdown-fájlt és a hozzá tartozó
   forrásokat. Ellenőrizd, hogy a feldolgozott tartalom teljes-e.
2. Olvasd el a cikkszöveget, és nézd meg a tartalmat hordozó képeket is,
   lehetőleg a letöltött legnagyobb felbontásban. Beágyazott videónál a videó
   vagy elérhető átirata alapján dolgozz. A cím és az ajánló önmagában nem
   helyettesíti a hivatkozott tartalmat; az ajánlott cikkek témaköreit nem
   örökítjük.
3. A `topics.json` teljes fogalomtára alapján válaszd ki a témaköröket. Az
   eredmény rendezett, ismétlés nélküli témakörlista, a döntésekhez rövid
   tartalmi indokkal. Ha egyik témakör sem illik, javasolj kizárást, vagy kérj
   humán döntést, mielőtt az összerendelést véglegesítenéd.
4. Ha az értékeléshez szükséges tartalom nem hozzáférhető vagy nem olvasható,
   jelezd a konkrét akadályt, és ne írj végleges besorolást. Hiányzó témát külön
   javasolj; új témakört ne hozz létre.
5. A végleges témakörlistát vezesd át az `assignments.json` fájlba. Ellenőrizd a
   témakörazonosítókat, a rendezést és azt, hogy más cikkek összerendelései nem
   változtak.

## Besorolóprogram

A `classify.py` egy már helyben letöltött cikk témaköreit határozza meg a
WordPress-azonosítója alapján, és frissíti az `assignments.json` fájlt. A
projekt Python-függőségei mellett a Codex CLI és ChatGPT-előfizetéssel történő
bejelentkezés szükséges. A parancsot a projekt gyökeréből futtasd, a besorolandó
cikk azonosítójával:

```powershell
python -B topics/classify.py 128100
```

A parancs a besorolás idejére a letöltő meglévő zárolásával védi a helyi
cikkeket és forrásokat. Ha már fut letöltés vagy besorolás, hibával leáll; a
másik futás befejezése után újraindítható.

A program minden cikkhez friss sessiont indít, és a helyi képeket is
figyelembe veszi. Hiányzó vagy nem feldolgozható tartalomnál humán ellenőrzést
kér.

A besorolás bemenetében az ismert WordPress-szmájlit szöveges jellé alakítja,
a blog ismert reklámképét kihagyja. Ezeket pontos forráscímük és a hozzájuk
nyilvántartott helyi fájlútvonal alapján ismeri fel.

Ha a cikk nincs a helyi cikklistában vagy hiányzik a fájlja, a program leáll;
a letöltést külön kell elindítani. A már besorolt cikk témaköreit modellhívás
nélkül kiírja. Új témakör, kizárás vagy más humán döntés szükségességét jelzi,
ilyenkor az összerendeléseket változatlanul hagyja.

Siker esetén menti a besorolást, és kiírja a témaköröket a tartalmi
indoklásukkal.

Egy már besorolt cikk kifejezett újraértékeléséhez a `--force` kapcsoló
használható: `python -B topics/classify.py 128100 --force`. A korábbi
témakörök csak sikeres besorolás után cserélődnek le. Hiányos letöltésnél nincs
modellhívás.

A letöltést és a témakörök szerinti besorolást együtt a [közös frissítő](../README.md#telepítés-és-frissítés) végzi.

A besoroló és a nyilvántartás modellhívás nélküli tesztjei:

```powershell
python -B -m unittest discover -s topics -p "test_*.py"
```
