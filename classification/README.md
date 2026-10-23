# Tartalmi jelleg

A [témakörök](../topics/README.md) azt
jelölik, miről szól egy cikk; a `content_nature` a tartalmi jellegét írja le.

A `classify.py` a témaköröktől függetlenül, külön modellhívással határozza
meg a jelöléseket, és az itteni `assignments.json` fájlba menti őket.
A meglévő témaköröket és kizárásokat nem módosítja.

## Közös szabályok

- A besorolás többértékű: minden jelölést külön kell indokolni. Nincs
  kötelező fő kategória, és egyik jelölés sem von maga után másikat.
- Érdemi mellékrész is elegendő. Egy önállóan összefoglalható, a jelölés
  feltételeit teljesítő tartalmi egységet kell találni; puszta említés,
  odavetett mondat, kapcsolódó cikk ajánlója vagy reklámbetét nem elegendő.
  Nincs szószám- vagy százalékhatár: egy rövid magyarázat is lehet teljes.
- Minden megadott jelöléshez rögzíts rövid tartalmi indokot és visszakereshető
  bizonyítékot: szövegrészt sorszámmal, vagy a tartalmat hordozó kép megfelelő
  részét. A cím, a WordPress-kategória és a meglévő témakörök nem bizonyítékok.
- A teljes hozzáférhető cikkszöveget és a hozzáférhető, mellékelt képeket
  értékeld. Egy külső hivatkozás, melléklet vagy videó puszta jelenléte nem
  akadály; más cikk tartalmát ne örökítsd át. Kisebb forráshiánynál a
  hozzáférhető adatok alapján sorolj be, a hiány külön rögzítése nélkül.
  Hiányzó tartalmat ne találj ki. Ha a fő tartalom ténylegesen nem
  hozzáférhető, indokolt `source_unavailable` áttekintési esetet adj. Ha a
  teljesen hozzáférhető tartalom egyik jellegbe sem sorolható megalapozottan,
  indokolt `taxonomy_unmatched` esetet adj. Ezek nem kizárási döntések.
- Egy elemző AI-session egyetlen cikket kaphat, más cikk szövege és korábbi
  elemzése nélkül. Újraértékeléshez is új session kell.
- Minden új végleges döntéshez legalább egy tartalmi jelleg kell. Csak
  indokolt `taxonomy_unmatched` vagy `source_unavailable` esetben maradhat
  címke nélkül; ne erőltess jelölést, és ne tekintsd kizárásnak.
- Határesetben a modell a legjobb megítélése szerint dönt. A jelleg
  bizonytalan határa önmagában nem indokol emberi mérlegelést,
  újrapróbálkozást vagy második elemzőt. Érvénytelen formátumú modellválasz
  egyszer újrapróbálható friss sessionben; ez nem tartalmi felülvizsgálat.
- A jelölések nem igazolják az állítások helyességét, mai alkalmazhatóságát
  vagy Miklós szerzőségét. Idézett és olvasói szövegnél az eredetet külön jelezd.
- A besorolás szűrésre szolgál; nem indokol automatikus kizárást vagy törlést.

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
WordPress-azonosítói. Minden bejegyzés a rendezett, ismétlés nélküli
`content_nature` listát tartalmazza, például:

```json
{"content_nature": ["explanatory"]}
```

Az indoklás a modellválasz ellenőrzéséhez kell; a nyilvántartásba nem kerül.

Egy helyi cikk besorolása a projekt gyökeréből:

```powershell
python -B classification/classify.py 125610
```

Meglévő eredménynél nincs új modellhívás. Kifejezett újraértékelés:
`python -B classification/classify.py 125610 --force`.

Az alapbeállítás `gpt-6-luna`, `high` reasoning, a témakör-besorolótól
függetlenül. A `--model` és `--reasoning-effort` kapcsolóval felülírható.

A gyűjtemény frissítését és a besorolást a
[közös frissítő](../README.md#telepítés-és-frissítés) végzi.

Kilépési kód: `0` siker, `2` szükséges tartalom hiánya vagy áttekintést igénylő
eset, `1` végzetes hiba, `130` megszakítás.

## Képek és videók feldolgozása

A besoroló a helyi és a közvetlenül hivatkozott külső képeket is megkísérli
feldolgozni. A nem feldolgozható média kimaradhat a modell bemenetéből.

Helyi videóból az `ffmpeg` és `ffprobe` segítségével időrendi képkockákat vesz.
Hangátiratot nem készít, külső videót nem tölt le.

A modellnek szánt képekből szükség esetén tömörített másolat készül; az eredeti
források megmaradnak. Ha a képbemenet így is túl nagy, hibát jelez.

## Tesztek

A modellhívás és hálózat nélküli tesztek:

```powershell
python -B -m unittest discover -s classification -p "test_*.py"
```
