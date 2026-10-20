# Cikkek címkézése és keresése

A tagek egy témáról való íráshoz gyűjtik össze a releváns cikkeket. Az AI ezután
elolvassa és értékeli a találatokat. A címkézés a szöveg mai érvényességét nem igazolja.

## Tagválasztás

- Egyszerű, érthető témákat használunk. Egy szakfogalom létezése önmagában nem
  indok külön tagra: Adósságfék és Hitelkiváltás helyett elég a **Hitelek és tartozások**.
- A külön keresés haszna dönt. A **Lakáshitel, ETF, TBSZ, Unit-linked biztosítás,
  Befektetés és Kerti medence** önálló tag marad. Nem cél az egységes részletesség
  vagy egy előre meghatározott tagszám.
- Kombinálható keresési szempont is lehet tag: **Adózás és Költségek** igen;
  **Megtérülés és Pénzügyi kockázat** nem. Utóbbiakat a tárgy témáján belül értékeljük.
- Egy cikk több, egymást átfedő taget is kaphat: például Unit-linked biztosítás,
  Életbiztosítás és Biztosítás. Minden szülőtag ráírása nem kötelező; a kereső
  a taglistában megadott altémákat is megtalálja.
- Az **Online vásárlás** mellé mindig megadjuk a **Vásárlás** taget is.
  A **Fűtés** mellett Lakásfelújítás és Költségek is szerepelhet, ha érdemi témák.
- Bizonytalan összevonásnál kérdezzünk röviden, javaslattal, választható kérdésben.
  Az agent javaslata nem felhasználói döntés.

Az Adósságrendezés, Önerő és Hitelszerződések elszámolása a **Hitelek és
tartozások** része; az **Előtörlesztés** külön marad. A közös tag Adósság,
Hitel, Hitelek, Tartozás és Tartozások néven is kereshető, azonosítója továbbra
is `debt`. A **Befektetésválasztás** a Befektetés része. **Biztosítóváltás** és
**Szerződés megszüntetése** helyett az érintett biztosítás vagy termék témáját
használjuk.

Önálló keresési téma a **Fűtés**, **Rezsi**, **Pénzügyi panaszkezelés**,
**Vásárlás** és **Online vásárlás**. A fűtési technológiák, a Pénzügyi Békéltető
Testület és az egyes webáruházak ezek részletei, nem külön tagek.

Érdemi, rövid melléktéma is címkézhető; puszta említés, tantervi felsorolás,
reklám és kapcsolódó cikk ajánlója nem. Valódi ügyfélközlemény releváns.
Régi és nem pénzügyi cikkek is maradnak, dátummal. Elsőként csak a cikkszöveget
értékeljük; a hiányos szöveg külön állapotot kap, cím alapján nem találgatunk.
A cikk jellege külön, többértékű mező: magyarázat, tanács, hír/közlemény, vélemény.

## Címkézés

Python 3.10+ és ChatGPT-fiókkal bejelentkezett Codex CLI szükséges. A hívások a
meglévő előfizetést használják, külön API-kulcs nélkül. Modell: GPT-6.1 Sol, medium.

```powershell
python -B tagging/label.py --output tagging/runs/current.json
python -B tagging/label.py --run --output tagging/runs/current.json
```

Az első parancs csak előnézet: nem hív modellt és nem ír fájlt. A `--run` indít
címkézést. Alapból az 50 próbacikket választja; `--split holdout` a félretett 20,
`--split all` az összes bejegyzés. A minták cikkazonosítói rögzítettek. A félretett
20 alapján nem finomítjuk a szabályokat. A teljes feldolgozásról külön döntünk.

**Kötelező kontextusizoláció:** egy AI-session csak egy cikket elemezhet,
az éles feldolgozásban is. Minden kérés külön `codex exec` folyamatban,
`--ephemeral` módban, külön ideiglenes munkamappából indul. Korábbi sessiont
nem folytatunk, és másik cikk szövegét vagy elemzését nem adjuk át. Az
újrapróbálkozás is új session; több cikket közös sessionben nem dolgozunk fel.

Egy kérés egy cikk. A program folyamatosan ment, ugyanazzal a paranccsal
folytatható; a már kész cikkeket kihagyja. Alapból eredményfájlonként legfeljebb
150 kérés engedélyezett (`--max-requests`), cikkenként legfeljebb három próbálkozás.
A keret újraindításkor sem nullázódik; az előfizetési limitnél megáll.
Megváltozott cikkszöveghez vagy címkézési beállításhoz külön `--reprocess`
kell. Új tag hozzáadása önmagában nem igényel újrafuttatást; a kész cikkeket
a folytatás továbbra is kihagyja. A taglistát csak leállított címkéző mellett,
két feldolgozási szakasz között változtatjuk. A modell csak létező tageket
adhat; a le nem
fedett témákat a `missing_topics` mezőben jelzi, a program pedig összesíti őket.
Ezekből nem készül automatikusan új tag: a külön keresés hasznát és a meglévő
tagek lefedését mérlegeljük, majd a bővítésről a felhasználó dönt. A jelzések
hiánya önmagában nem igazolja, hogy nincs lefedetlen téma.

A `tags.json` az egyetlen taglista; a `broader` mezők a tágabb témák azonosítói.
Nincs kézi tag-, prompt- vagy tisztítóverziózás. Az eredményfájl megőrzi a
használt fogalomtárat, cikkenként pedig a vizsgált tagazonosítókat. Ha a
fogalomtár csak új tagekkel bővül, a korábbi besorolások érvényesek maradnak.
Meglévő tag módosítását vagy törlését, illetve az utasítások, modell vagy
szövegtisztítás változását továbbra is elavulásként kezeljük. A régi,
fogalomtármásolat nélküli eredményeket csak egyező lenyomatok mellett tudjuk
biztonságosan kiegészíteni ezekkel az adatokkal.

Az eredmény és a használati napló egy JSON-ban van a `runs/` alatt; ezek
helyi fájlok, nem kerülnek Gitbe.
A címkéző az `articles/` és `raw_source/` tartalmát nem módosítja;
a túl hosszú szöveget jelzi, nem csonkolja.

## Keresés

Friss címkézési eredménnyel:

```powershell
python -B tagging/search.py ETF Adózás --results tagging/runs/current.json
python -B tagging/search.py ETF Költségek --results tagging/runs/current.json
python -B tagging/search.py "Hitelek és tartozások" --results tagging/runs/current.json
```

Több tag ÉS kapcsolat; a keresés a tágabb tag altémáit is tartalmazza.
Új tag esetén a régebbi cikkeket még nem vizsgáltuk az új téma szerint:
a kereső ezt a `unevaluated_ids` listában és szöveges figyelmeztetésben jelzi.
Ez a tágabb témára keresésre is vonatkozik, ha új altémát vettünk fel.
A régi találatok használhatók maradnak, de az új tag szerinti lefedést
külön ellenőrzéssel kell pótolni; a hiányzó tag nem jelent biztos kizárást.
Jelleg, dátum és állapot szerint is szűrhető (`--nature`, `--from`, `--until`,
`--status`); a `--json` gépi kimenet. Nincs találatlevágás. A tagek és a jelleg
cikkenkénti együttállása nem garantálja, hogy ugyanarra a szövegrészre vonatkoznak.

A kézi javítás külön JSON, `--overrides` kapcsolóval: `tags_sha256` és `articles`
lista; javításonként `id`, `markdown_sha256`, `tags`, `nature`, `reason`.
A címkéző ezt nem írja felül. Hibás vagy elavult bemenetet előbb rendezni kell.

A friss, korábban nem vizsgált 20 cikkes Sol medium próba a
`runs/fresh-sol.json` fájlban kereshető. A korábbi 70 mintacikk kizárásával,
időben szétterítve, tartalmi válogatás nélkül választottunk. A rögzített
mintákat és a címkézés beállításait nem módosítottuk. Mind a 20 besorolás
elkészült 20 kérésből, hiba és újrapróbálkozás nélkül.

Két külön ellenőrző előbb 10–10 teljes cikket olvasott el a kimenet nélkül,
majd az eltéréseket a szövegből ellenőrizte vissza. Egyértelmű témataghibát,
meglévő taggal lefedhető főtéma kihagyását és visszatérő hibamintát nem
találtak; néhány védhető határeset maradt. A próba teljesítette az előre
megbeszélt indulási mércét: nincs fontos főtéma-kihagyás, legfeljebb 1–2
cikknél kisebb taghiba, nincs ismétlődő hibatípus, és a technikai működés
rendben van. A jelenlegi tagkészlettel a teljes feldolgozás indítható;
még nem indult el, arról külön döntünk. Ez gyakorlati indulási feltétel,
nem a teljes gyűjtemény hibátlanságának bizonyítéka.

A fogalomtár lefedése korlátozott: a NOK konstrukcióját és a turizmust
hiányzó témaként jelezte a modell. A NOK-cikk tágabb tagekkel megtalálható,
a turizmus főtémájú 117962-es cikk megfelelő tag híján üres taglistát kapott.
A `missing_topics` jelzés nem teszi ezeket önállóan taggel kereshetővé;
a fogalomtár bővítéséről külön döntünk.

A `runs/holdout-sol.json` az előző, már ismert 20 cikk próbája ugyanezekkel
a beállításokkal; annak külön ellenőrzése sem talált egyértelmű, érdemi
témataghibát. Az ETF + Költségek keresés ott a 125587-es cikket adja.
A korábbi Luna-próbához képest a modell és a taglista is változott,
így a javulást nem lehet kizárólag a modellnek tulajdonítani. Az általános
címkézési szabályokon egyik ellenőrzött minta alapján sem finomítottunk.

A korábbi `runs/pilot.json` és `runs/holdout.json` megmaradt, de korábbi
fogalomtárhoz tartozik; a kereső mindkettőt elutasítja. A Luna 20 cikkes
próbájának külön tartalmi ellenőrzése érdemi kihagyásokat és fölösleges
tageket talált.

## Ellenőrzés

```powershell
python -B -m unittest discover -s tagging -p "test_*.py"
```

A tesztek nem hívnak modellt. A tartalmi minőséget a külön ellenőrzött cikkek
alapján értékeljük, nem a technikai tesztekből következtetjük.
