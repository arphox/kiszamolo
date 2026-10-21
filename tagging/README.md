# Cikkek címkézése

Az `assignments.json` a cikkekhez rendelt tagek nyilvántartása. A tagek nevét,
jelenlegi definícióját, szinonimáit és tágabb témáit a `tags.json` határozza
meg.

## Cikkek és összerendelések

A cikk azonosítójával keresd ki a rekordot az `articles/index.jsonl` fájlban:
itt találod a címet és a helyi útvonalat. Az útvonal az `articles/` mappán
belüli Markdown-fájlra mutat.

Az `assignments.json` gyökerében az `articles` objektum kulcsai a cikkazonosítók,
például `"12345"`. A bejegyzés `tags` mezője a cikkhez rendelt tagazonosítók
rendezett listája.

A fájl UTF-8 kódolású, LF sorvégű, záró soremeléssel; minden cikkbejegyzés egy
sorban áll. A cikkazonosítók számszerű sorrendben, a tagazonosítók egyedien és
ábécérendben szerepelnek.

## Tagválasztás

- A `tags.json` definíciói az irányadók. Csak az ott szereplő tagazonosítókat
  használd; új vagy összevont tagról a felhasználó dönt.
- Minden megtartott cikkhez legalább egy tag kell. Ha egyik tag sem illik rá,
  és a cikk kizárása sem indokolt, humán mérlegelést kell kérni a besorolásról
  vagy a fogalomtár bővítéséről. Üres taglistával nem véglegesíthető.
- A tag a blog tudásanyagán belül hasznos, önálló keresési témát jelöljön.
  Egyszeri, lazán kapcsolódó külső érdekesség miatt nem bővítjük a fogalomtárat;
  az ilyen bejegyzést kizárjuk.
- Rövid, de érdemi melléktéma is kaphat taget; a puszta említés, reklám,
  felsorolás vagy kapcsolódó cikk ajánlója önmagában nem elég.
- Egy cikk több, egymást átfedő témát is kaphat. A tágabb taget is a cikk
  tartalma és a tag definíciója alapján válaszd ki; a `broader` kapcsolat
  nem jelent automatikus öröklést.
- Az adósságfék, hitelkiváltás és váltó a **Hitelek és tartozások** témához
  tartozik. A családon belüli anyagi segítséget, az etikus és vallási
  befektetést, valamint a garázsbefektetést is a meglévő témákkal címkézzük,
  külön tag nélkül.
- Vásárlói csoportnál a tagságból önmagában nem következtetünk jogi státuszra;
  a NOK nem automatikus szinonima.
- Régi és nem pénzügyi cikkeket is értékelünk.
- Bizonytalan taghatárnál fogalmazz meg rövid javaslatot, és kérdezd meg a
  felhasználót. Új tag helyett a hiányzó témát jelezd.

## AI-session szabály

Egy AI-session kizárólag egy cikket elemezhet. A session a teljes cikkszöveget
megkaphatja, de más cikk szövege vagy korábbi elemzése nem kerülhet bele.
Újrapróbálkozáskor is új sessiont kell indítani.

## Címkézés menete

1. A cikkazonosító alapján keresd meg a Markdown-fájlt és a hozzá tartozó
   forrásokat. Ellenőrizd, hogy a feldolgozott tartalom teljes-e.
2. Olvasd el a cikkszöveget, és nézd meg a tartalmat hordozó képeket is,
   lehetőleg a letöltött legnagyobb felbontásban. Beágyazott videónál a videó
   vagy elérhető átirata alapján dolgozz. A cím és az ajánló önmagában nem
   helyettesíti a hivatkozott tartalmat; az ajánlott cikkek tagjait nem örökítjük.
3. A `tags.json` teljes fogalomtára alapján válaszd ki a tageket. Az eredmény
   rendezett, ismétlés nélküli taglista, a döntésekhez rövid tartalmi indokkal.
   Ha egyik tag sem illik, javasolj kizárást, vagy kérj humán döntést, mielőtt
   az összerendelést véglegesítenéd.
4. Ha az értékeléshez szükséges tartalom nem hozzáférhető vagy nem olvasható,
   jelezd a konkrét akadályt, és ne írj végleges besorolást.
   Hiányzó témát külön javasolj; új taget ne hozz létre.
5. A végleges taglistát vezesd át az `assignments.json` fájlba.
   Ellenőrizd a tagazonosítókat, a rendezést és azt, hogy más cikkek
   összerendelései nem változtak.

## Címkézőprogram

A `label.py` egy már helyben letöltött cikket címkéz a WordPress-azonosítója
alapján, és frissíti az `assignments.json` fájlt. A projekt Python-függőségei
mellett a Codex CLI és ChatGPT-előfizetéssel történő bejelentkezés szükséges.
A parancsot a projekt gyökeréből futtasd, a címkézendő cikk azonosítójával:

```powershell
python -B tagging/label.py 128100
```

A program minden cikkhez friss sessiont indít, és a helyi képeket is
figyelembe veszi. Hiányzó vagy nem feldolgozható tartalomnál humán ellenőrzést
kér.

Ha a cikk nincs a helyi cikklistában vagy hiányzik a fájlja, a program leáll;
a letöltést külön kell elindítani. A már címkézett cikk tagjait modellhívás
nélkül kiírja. Új tag, kizárás vagy más humán döntés szükségességét jelzi,
ilyenkor az összerendeléseket változatlanul hagyja.

Siker esetén menti a besorolást, és kiírja a tageket a tartalmi indoklásukkal.

A címkéző és a nyilvántartás modellhívás nélküli tesztjei:

```powershell
python -B -m unittest discover -s tagging -p "test_*.py"
```
