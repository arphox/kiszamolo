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

Az [exclusions.json](automation/exclusions.json) tartalmazza a kézzel kizárt rekordokat és az indoklást. A frissítés eltávolítja ezeket a helyi gyűjteményből. A kizárásokról a felhasználó dönt, a besoroló önállóan nem zár ki cikket.

A gyűjtemény a blog tudásanyagát fedi le. Kizárjuk a navigációs, cikkjegyzék-, szolgáltatás-, eszköz- és adminisztratív oldalakat, a tesztanyagokat és a külön kiválasztott alkalmi közleményeket. Szintén kizárjuk a saját érdemi szöveg nélküli bejegyzéseket akkor is, ha a beágyazott videó vagy ábra hozzáférhető, feldolgozható tudásanyagot tartalmaz. Kizárjuk továbbá az elérhetetlenné vált érdemi tartalomra mutató bejegyzéseket, valamint a blog tudásanyagához lazán kapcsolódó, saját magyarázat nélküli egyszeri külső érdekességeket. Az új WordPress-oldalak nem záródnak ki automatikusan; a témakör-besorolás állapota és a szöveghossz önmagában nem kizárási ok.

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

A hibák és az összesítés a terminálra kerülnek; külön riportfájlok, cikkjegyzékek és médiastatisztikák nem készülnek. A kilépési kód `0` siker, `2` befejezett futás megmaradt teendővel, `1` végzetes hiba, `130` megszakítás. Egy közös folyamatközi zár védi a frissítést és a besorolást; a zárolási fájl az `articles/` mappán kívül van.

## Ellenőrzés

A tesztfüggőségeket és a tesztparancsokat a [projekt leírása](../README.md#tesztek) tartalmazza.
