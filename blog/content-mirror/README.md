# Kiszámoló tartalom-mirror

A letöltő a Kiszámoló blog nyilvános cikkeit és önálló oldalait olvasható Markdown-fájlokként tartja naprakészen.

## Futtatás

A [telepítés](../README.md#telepítés-és-frissítés) után, a projekt gyökeréből:

```powershell
.\.venv\Scripts\python.exe -u -B blog/content-mirror/automation/sync.py
```

A [teljes frissítés](../README.md#telepítés-és-frissítés) a cikkek besorolását is elvégzi.

## Tárolás

- `articles/YYYY/MM/YYYY-MM-DD HH.MM Cím [ID].md`: a cikkek, saját JSON-értékeket használó front matter fejléccel. Az ID alapján a fájl a fejléc beolvasása nélkül megtalálható.
- `articles/wordpress-modified.json`: frissítési index, WordPress-ID → `modified_gmt` párokkal, azonosító szerinti sorrendben, kétszóközös behúzással.

```json
{
  "66976": "2021-12-14T14:36:31"
}
```

Az index értéke pontosan a cikk `modified_at_gmt` mezőjében is elmentett WordPress-időbélyeg. A frissítő ezt az UTC-ben megadott forrásidőt hasonlítja az API válaszához. Előbb a cikket menti, és csak sikeres mentés után frissíti az indexbejegyzést. Sikertelen frissítéskor a következő futás újrapróbálja a letöltést.

A blog eredeti WordPress-kategóriáit és -címkéit nem vesszük át a cikkekbe és az indexbe: ezek nem illeszkednek a tudásbázis tematikus keresési és rendszerezési igényeihez. Erre a célra az [enrichment saját témaköreit](../enrichment/README.md) használjuk.

## Frissítés

A frissítő beolvassa az indexet, és lekéri a blog könnyű leltárát. Csak az új, módosult vagy helyben hiányzó cikkeket tölti le és alakítja át. Ez a régi cikkek utólagos módosításait is követi; a változatlan cikkfájlokat nem olvassa végig.

A cikkadatok közvetlenül az API-válaszból készülnek. Ha az API nem tartalmaz szöveget, a letöltő megpróbálja a nyilvános cikkoldalból kinyerni. A nyers API-válaszokat és HTML-forrásokat nem őrizzük meg külön archívumban. A blogról eltűnt cikk helyi példánya megmarad.

Az [articles/exclusions.json](articles/exclusions.json) tartalmazza a kézzel kizárt rekordokat WordPress-ID → indoklás formában, az ID számszerű értéke szerint növekvő sorrendben. Az ID önmagában azonosítja a bejegyzéseket és az oldalakat, típuselőtag nélkül. A frissítés eltávolítja ezeket a helyi gyűjteményből. A kizárásokról a felhasználó dönt, a besoroló önállóan nem zár ki cikket.

## Kizárási szabályok

Azt hagyjuk ki, amiben nincs a későbbi tudásbázisban hasznosítható érdemi információ. A szöveg mellett a hozzáférhető képek, beágyazott videók és mellékletek is számítanak. Egy érdemi rész elegendő a megtartáshoz, reklám vagy közlemény mellett is.

A videók és mellékletek teljes feldolgozása nem feltétele a beválogatásnak. Ha a kísérőszöveg alapján hasznos tudásanyag várható bennük, a cikket megtartjuk, és a tartalmat később dolgozzuk fel.

Kizárjuk:

- a pusztán navigációs, cikkjegyzék-, adminisztratív és tesztoldalakat;
- az önálló érdemi tartalom nélküli ajánlókat, átlinkeléseket, szolgáltatásajánlatokat és eszközfelületeket;
- a pusztán alkalmi közleményeket és felhívásokat, például tanfolyam elmaradását, szolgáltatás újraindulását vagy havi biztosítási taglista és befizetési bizonylat közlését;
- az elérhetetlen vagy jelszóval védett tartalmakat, ha a hozzáférhető részek önmagukban nem adnak érdemi információt.

A rövidség, elavulás, hírjelleg, nem pénzügyi téma, külső eredet vagy hiányzó témakör önmagában nem kizárási ok.

A szöveg alapján egyértelműen adminisztratív mellékleteket, például taglistát vagy utalási bizonylatot nem szükséges külön megnyitni, ha nincs utalás érdemi tudásanyagra. A biztosítási feltételeket vagy általánosan hasznosítható biztosítási ismereteket érdemben ismertető rész viszont megtartást indokolhat. A Kiszámoló saját biztosításának jelentkezési és befizetési útmutatói önmagukban adminisztratív tartalomnak számítanak.

## Az index újraépítése

Hiányzó index esetén az első futás automatikusan újraépíti azt a cikkek fejlécéből. Kifejezetten is kérhető:

```powershell
.\.venv\Scripts\python.exe -B blog/content-mirror/automation/sync.py --rebuild-index
```

Ez hálózati kérés és cikkszöveg-újragenerálás nélkül működik. Normál frissítéskor nincs szükség a cikkek fejlécének újbóli beolvasására.

## Markdown, hivatkozások és média

A Markdown a blog tartalmát követi; közvetlenül nem szerkesztjük. A fájlnév a létrehozáskori címet, dátumot és a WordPress-ID-t tartalmazza, és később változatlan marad. A forrás aktuális címe és dátuma a front matterben szerepel, így a helyi hivatkozások a forrás szerkesztésekor is stabilak.

A képek, dokumentumok, hang- és videófájlok az eredeti, abszolút webes URL-jükön szerepelnek. A letöltő nem tölti le és nem ellenőrzi ezeket; elérésük internetet és a forrás elérhetőségét igényli. Feldolgozáskor az ismert helyi cikkekre mutató hivatkozásokat helyi Markdown-hivatkozássá alakítjuk. Egy új cikk megjelenése miatt a korábbi cikkeket nem olvassuk újra; azok meglévő webes hivatkozásai megmaradhatnak. Kizáráskor az érintett helyi hivatkozásokat a forrás webes címére javítjuk; ez a ritkább művelet a meglévő cikkek átnézését igényli.

## Besorolási teendők

A besorolás eredményeit és feldolgozási nyilvántartását az [enrichment](../enrichment/README.md#tárolás-és-működés) kezeli.

A hibák és az összesítés a terminálra kerülnek; külön riportfájlok, cikkjegyzékek és médiastatisztikák nem készülnek. A kilépési kód `0` siker, `2` befejezett futás megmaradt teendővel, `1` végzetes hiba, `130` megszakítás. Egy közös folyamatközi zár védi a frissítést és a besorolást; a zárolási fájlok a repó `.local-cache/locks/` mappájában vannak.

## Ellenőrzés

A tesztfüggőségeket és a tesztparancsokat a [projekt leírása](../README.md#tesztek) tartalmazza.
