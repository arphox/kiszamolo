# Cikkek besorolása

A közös besoroló két szempontból értékeli a cikkeket: a `topics` azt jelöli,
miről szólnak, a `content_nature` a tartalmi jellegüket írja le. Két külön
modellhívás dolgozik ugyanabból az előkészített cikkből és médiából;
az eredmények egyetlen `assignments.json` fájlba kerülnek.

## Közös szabályok

- Mindkét besorolás többértékű: minden jelölés saját feltételeit külön kell
  teljesíteni. Nincs kötelező fő kategória; egyik jelölés sem von maga után másikat.
- Érdemi mellékrész is elegendő. Önállóan összefoglalható, a jelölés feltételeit
  teljesítő tartalmi egység kell; puszta említés, reklámbetét, felsorolás vagy
  kapcsolódó cikk ajánlója nem elegendő. Nincs szószám- vagy százalékhatár.
- Minden jelöléshez rövid tartalmi indok és visszakereshető bizonyíték kell:
  azonosítható szövegrész vagy a tartalmat hordozó kép megfelelő része.
  A cím, a WordPress-kategória és a másik szempont besorolása nem bizonyíték.
- A teljes hozzáférhető cikkszöveget és mellékelt médiát értékeld. Más cikk
  tartalmát és besorolását ne örökítsd át; külső hivatkozás puszta jelenléte
  nem akadály. A szerző által szándékosan meg nem jelentetett rész nem hiányzó bemenet.
- Hiányzó, olvashatatlan vagy nem feldolgozható kép, videó vagy melléklet
  önmagában nem akadály. Ha a hozzáférhető tartalom megalapozza az adott
  besorolást, dönts abból. Ha a hiányzó rész érdemben befolyásolhatná a döntést,
  adj indokolt `source_unavailable` áttekintési esetet; az érintett besorolást
  ne véglegesítsd. Hiányzó tartalmat ne találj ki.
- Ha nincs megfelelő kategória, adj indokolt `taxonomy_unmatched` esetet,
  konkrétan jelezve, mi hiányzik. Ne erőltess besorolást. Új végleges döntéshez
  legalább egy jelölés kell; az áttekintési eset függő feladat, nem üres végleges eredmény.
- Meglévő kategóriák közötti határesetben a modell a legjobb megítélése szerint
  dönt. A bizonytalan kategóriahatár önmagában nem ok emberi mérlegelésre,
  újrapróbálkozásra vagy második elemzőre.
- Új kategóriáról, meglévő kategória módosításáról vagy összevonásáról és cikk
  kizárásáról a felhasználó dönt. A besoroló csak javasolhat, nem zár ki és nem töröl.
- Egy elemző AI-session egyetlen cikket kaphat, más cikk szövege és korábbi
  elemzése nélkül. A két szempont külön sessiont használ. Érvénytelen formátumú
  válasz egyszer újrapróbálható, szintén friss sessionben; tartalmi döntést nem próbálunk újra.
- A jelölések nem igazolják az állítások helyességét, mai alkalmazhatóságát vagy
  Miklós szerzőségét. Idézett és olvasói szövegnél az eredetet az indoklásban jelezd.

## Témakörválasztás

- A `topics.json` definíciói az irányadók. Csak az ott szereplő
  témakörazonosítókat használd.
- A témakör a blog tudásanyagán belül hasznos, önálló keresési témát jelöljön.
  Egyszeri, lazán kapcsolódó külső érdekesség miatt nem bővítjük a fogalomtárat;
  az ilyen bejegyzésnél kizárást javasolj a felhasználónak.
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

## `content_nature`

### `explanatory` — magyarázó / útmutató

A cikk legalább egy működést, összefüggést, módszert vagy döntési szempontot
úgy fejt ki, hogy az olvasó megértse a miértet vagy a hogyant. Ehhez bemutatja
például a kapcsolat működését, a lépéseket, a mérlegelendő alternatívákat,
alkalmazási feltételeket vagy egy számítás menetét.

**Ellenőrző kérdés:** Mit ért meg vagy mit tanul meg elvégezni az olvasó,
és melyik kifejtett rész alapján?

Nem elegendő egy tény, eredmény, előrejelzés vagy tanács közlése; egy adat
következményének puszta kijelentése sem. Egy kapcsolat megnevezése és néhány
paraméter felsorolása még akkor sem elég, ha technikailag új ismeret az
olvasónak. Például az éves inflációhoz igazodó állampapírkamat és a különböző
kamatprémiumok kétmondatos közlése még nem kifejtett magyarázat.

A tényközlésen túl az összefüggés működését, alkalmazását vagy a döntés
mérlegelését is ki kell bontani. Egy javaslat okainak, az alternatívák
előnyeinek és lényeges feltételeinek feldolgozása is megfelelhet; nem
kötelező hozzá ügyintézési lépéssor. Nem kell hosszúnak vagy örökérvényűnek
lennie: egy tömör, teljes magyarázat, illetve korabeli szabályokon alapuló
részletes útmutató is megfelelhet.

### `mindset` — szemléletformáló

A cikk érdemben vizsgálja vagy újragondoltatja a pénzhez kapcsolódó célokat,
értékrendet, prioritásokat, elvárásokat vagy hozzáállást. A szerző kifejti,
milyen gondolkodásmódot és miért tart követendőnek vagy problémásnak;
önvizsgálatra vezető kérdéssor és feldolgozott élethelyzet is lehet ennek eszköze.

**Ellenőrző kérdés:** Melyik célt, értékrendet vagy hozzáállást gondoltatja
át, és hogyan bontja ki ezt a cikk?

Nem elegendő egy motivációs mondat, értékítélet vagy általános felszólítás.
A pszichológiai téma önmagában nem ilyen jelleg: egy gondolkodási torzítás
működésének bemutatása lehet kizárólag `explanatory`. Attól sem jár ez a
flag, hogy egy magyarázat új nézőpontot ad. A két jelleg együtt akkor jár,
ha mindkettő saját feltételei teljesülnek, akár ugyanabban a gondolatmenetben.
A befektetés időtávhoz és kockázattűréshez illesztése, a tényszerű kockázatok
ismertetése vagy egy piaci tévhit helyesbítése önmagában még magyarázat.
`mindset`-hez a hozzáállás, célok, prioritások vagy szokások érdemi vizsgálata
is kell; egy megnyugtató következtetés nem elég.

### `news` — aktualitás

A cikk érdemi célja vagy önálló része egy konkrét, az írás idején aktuális
esemény, adatközlés, szabály- vagy termékfeltétel-változás, illetve akkori
helyzet bemutatása vagy értékelése. A közlés jelentése lényegesen kötődik
ahhoz, mikor jelent meg.

**Ellenőrző kérdés:** Melyik akkori eseményről vagy állapotról tájékoztat,
és hol foglalkozik vele érdemben?

Nem elegendő a publikálás dátuma, egy régi példa vagy az idővel elavuló
számadat használata. Egy útmutató aktualitásra utaló rövid felütése, illetve
a benne összehasonlított akkori árak vagy díjak önmagukban nem indokolnak
hírbesorolást. Egy történelmi esemény oktatási feldolgozása sem
automatikusan hír. A blog saját működési hírei az `announcement` alá tartoznak;
`news` csak önálló külső aktualitás feldolgozása esetén jár melléjük.

### `promotional` — promóciós

A cikk érdemi célja vagy önálló része egy konkrét termék, szolgáltatás vagy
fizetős program igénybevételének ösztönzése: ajánlatként mutatja be az
előnyöket, feltételeket, és vásárlásra, szerződésre vagy jelentkezésre vezet.
A szöveg funkcióját értékeljük; nem következtetünk nem igazolt szponzorációra.

**Ellenőrző kérdés:** Melyik ajánlat igénybevételére ösztönöz, és melyik
érdemi rész szolgálja ezt?

Nem elegendő egy reklámkép, szokásos ajánlósáv, mellékes önreklám vagy link.
A más témájú cikket lezáró rövid tanfolyam- vagy tanácsadás-ajánló akkor sem
elegendő, ha árat, linket és jelentkezési felhívást is tartalmaz. Ez a
kivétel nem vonatkozik azokra a rövid cikkekre, amelyeknek maga az ajánlat
a fő tartalma. Érdemi ajánlati részhez az ösztönző célon túl az ajánlat
önálló feldolgozása is kell; puszta záró felhívás nem ilyen.
A tárgyilagos termékelemzés, összehasonlítás és abból következő szakmai
ajánlás önmagában nem promóció. A kifejtett oktatási tartalom mellett valódi,
érdemi ajánlati rész esetén `explanatory` és `promotional` együtt is adható.

### `announcement` — közlemény / közösségi felhívás

A cikk érdemi célja vagy önálló része a blog, a hozzá kapcsolódó közösség,
program vagy saját szolgáltatás működéséről tájékoztatni, vagy az olvasóktól
konkrét segítséget, tapasztalatot vagy részvételt kérni. Például időpontot,
helyszínt, elérhetőséget, működési szünetet vagy újraindulást, részvételi
változást közölhet; közösségi felhívásnál világos, mit kér az olvasóktól.

**Ellenőrző kérdés:** Milyen saját működési vagy közösségi ügyben ad önállóan
hasznos tájékoztatást?

Egy ajánlat szokásos ára, webcíme vagy jelentkezési felhívása önmagában nem
külön közlemény. Rutinszerű záró kérdés és retorikai kérdés nem közösségi
felhívás. Érdemi szervezési vagy működési információval kiegészített
promóciónál mindkét jelleg járhat. Külső bank üzemszünete vagy országos
szabályváltozás alapvetően `news`, nem a blog közleménye.

### `reporting` — bemutató / beszámoló

Önállóan értékes tényszerű helyzetet, adatokat vagy eredményeket,
feltételeket, összehasonlítást, illetve konkrét eseményt vagy tapasztalatot
mutat be. A hozadéka az, hogy mi van, mit figyeltek meg vagy mi történt;
nem szükséges hozzá oksági magyarázat vagy általános tanulság.

Háttéradat, odavetett állítás, ítélet, tanács vagy puszta link nem elég.
A rövid hír nem automatikusan beszámoló, és a beszámoló nem automatikusan
magyarázat. Mindegyik jelölés saját feltételeit külön kell teljesítse.

### `curated` — ajánló / válogatás

A cikk önálló funkciója kiválasztott anyag ajánlása, megosztása vagy
válogatás közlése: például cikket, videót, könyvet, oldalt/eszközt,
idézeteket vagy más összeállított anyagot mutat be.

Mellékes hivatkozás, a saját feldolgozást támogató forrásmegjelölés,
valamint a kapcsolódó cikkek szokásos listája nem elég. Külső anyag puszta
linkjének tanítását nem szabad átvenni; a ténylegesen mellékelt vagy
beágyazott, hozzáférhető tartalmat a többi jelleg saját feltételei szerint
szintén értékelni kell.

## Tárolás és működés

Az `assignments.json` gyökerében az `articles` objektum kulcsai a cikkek
WordPress-azonosítói. Cikkenként a `topics` és a `content_nature` rendezett,
ismétlés nélküli listája szerepelhet:

```json
{"topics":["government-bonds"],"content_nature":["explanatory","news"]}
```

A hiányzó mező még el nem készült besorolást jelent. A korábbról megmaradt
üres listákat megőrizzük. Az indoklás a modellválasz ellenőrzéséhez kell,
a nyilvántartásba nem kerül.
A fájl UTF-8 kódolású, LF sorvégű, záró soremeléssel; minden cikk egy sor,
a cikkazonosítók számszerű sorrendben szerepelnek.

A cím és a helyi Markdown-útvonal az `articles/index.jsonl` fájlban kereshető
ki a cikkazonosító alapján; az útvonal az `articles/` mappán belüli fájlra mutat.

A két szempont eredménye külön menthető. Ha az egyik hibázik vagy emberi
döntést igényel, a másik sikeres eredménye megmarad. A következő futás csak
a hiányzó vagy függő részt ismétli. Sikertelen újrabesoroláskor a régi érték
megmarad, a cikknyilvántartás megfelelő függő jelzése mutatja az elavultságát.
Az áttekintési esetek és hibák az `articles/update-report.json` riportban látszanak.

## Használat

Python 3.10+, a projekt Python-függőségei, a Codex CLI és ChatGPT-előfizetéssel
történő bejelentkezés szükséges. A parancsokat a projekt gyökeréből futtasd.

Egy már letöltött cikk mindkét besorolása:

```powershell
python -B classification/classify.py 125610
```

Csak az egyik szempont, illetve kifejezett újraértékelés:

```powershell
python -B classification/classify.py 125610 --dimension topics
python -B classification/classify.py 125610 --dimension content_nature --force
```

Meglévő, nem függő eredménynél nincs modellhívás. A `--force` a kiválasztott
szempontokat újraértékeli; a korábbi érték csak siker esetén cserélődik.
A parancs a letöltő zárolásával védi a cikkeket és forrásokat. Már futó letöltés
vagy besorolás mellett hibával leáll. Hiányzó cikknél vagy helyi szövegfájlnál
a letöltést külön kell elindítani.

A témakörök alapértelmezett modellje `gpt-6.1-sol` / `medium`,
a tartalmi jellegé `gpt-6-luna` / `high`. A `--model` és a
`--reasoning-effort` a kiválasztott szempontok beállítását felülírja.

A teljes gyűjteményt a [közös frissítő](../README.md#telepítés-és-frissítés)
frissíti. A tömeges újrabesorolás jóváhagyásának szabálya ott található.
Kilépési kód: `0` siker, `2` maradt áttekintést igénylő eset, `1` technikai
vagy végzetes hiba, `130` megszakítás. Egy szempont hibája mellett a másik feldolgozása folytatódik.

## Képek és videók feldolgozása

A két modellhívás ugyanazt az előkészített cikkszöveget és médiát kapja.
A besoroló a helyi és a közvetlenül hivatkozott külső képeket is megkísérli
feldolgozni, a hivatkozott anyagok tartalmát azonban nem találja ki.
A nem feldolgozott médiáról a modell tájékoztatást kap, hogy megítélhesse,
akadályozza-e a hiánya az adott besorolást.

Helyi videóból az `ffmpeg` és `ffprobe` segítségével időrendi képkockákat vesz.
Hangátiratot nem készít, külső videót nem tölt le; a videó hangja így nem
hozzáférhető tartalom. A képekből szükség esetén tömörített másolat készül,
az eredeti források megmaradnak. Ha a képbemenet így is túl nagy, hibát jelez.

Az ismert WordPress-szmájlit szöveges jellé alakítja, a blog ismert
reklámképét kihagyja; ezeket forráscím és helyi fájlútvonal alapján ismeri fel.

## Tesztek

A modellhívás és külső hálózat nélküli tesztek:

```powershell
python -B -m unittest discover -s classification -p "test_*.py"
```
