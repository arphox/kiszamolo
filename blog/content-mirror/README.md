# Kiszámoló tartalom-mirror

A letöltő a Kiszámoló blog nyilvános cikkeit és önálló oldalait menti el, majd olvasható Markdown-fájlokat készít belőlük.

## Futtatás

A [telepítés](../README.md#telepítés-és-frissítés) után csak a letöltő futtatásához, a projekt gyökeréből:

```powershell
.\.venv\Scripts\python.exe -u -B .\blog\content-mirror\automation\sync.py
```

Alapértelmezés szerint az olvasható fájlok a `blog/content-mirror/articles/`, a nyers források és a folytatáshoz szükséges állapot a `.local-cache/blog/content-mirror/` mappába kerülnek. A teljes frissítés besorolja a cikkeket is; ennek menetét a [besorolás leírása](../enrichment/README.md) ismerteti.

Más célmappákat a `--output PATH` és `--source-output PATH` kapcsolóval lehet megadni. A két mappa nem fedheti egymást. A párhuzamos letöltések száma `--workers 1..8` kapcsolóval állítható; alapértéke 4. A média méretkorlát nélkül töltődik le; a `--max-asset-mib N` kapcsolóval korlátozható. A korlátnál nagyobb fájl letöltési hibaként jelenik meg.

Mintafuttatáshoz használd a `--sample ID ID ... --output KÜLÖN_CIKK_KÖNYVTÁR --source-output KÜLÖN_FORRÁS_KÖNYVTÁR` alakot. Ez teljes távoli leltárt kér, de csak a megadott cikkeket és oldalakat dolgozza fel. Nem jelöl távoli eltűnést, és nem lépteti a szinkronizálás időbélyegét.

## Hálózat nélküli újragenerálás

A `--rebuild` a nyers archívumban megőrzött forrásokból és ellenőrzött médiafájlokból újra elkészíti a Markdown-nézeteket és az indexeket:

```powershell
.\.venv\Scripts\python.exe -u -B .\blog\content-mirror\automation\sync.py --rebuild
```

A nyers archívumban rögzített cikkmappát használja. A korábban mentett források és média meglétét és lenyomatait ellenőrzi; sérülésüknél vagy hiányuknál hibával leáll. A már ismert letöltési hiányok ettől nem szűnnek meg.

A régi médiaelrendezést a normál futás és a `--rebuild` is átköltözteti a `blog/content-mirror/articles/assets/` alá; megszakítás után újrafuttatható. Az offline újragenerálás nem ellenőrzi a távoli állapotot és nem változtatja meg a szinkronizálás időbélyegét.

## Tárolás

- `blog/content-mirror/articles/YYYY/MM/YYYY-MM-DD HH.MM Cím.md`: cikkenkénti olvasható Markdown-nézet, metaadatokkal és helyi hivatkozásokkal.
- `blog/content-mirror/articles/INDEX.md` és `blog/content-mirror/articles/index.jsonl`: emberi navigáció, illetve a helyi cikknyilvántartás.
- `blog/content-mirror/articles/assets/`: a cikkek által használt, egy példányban tárolt helyi média és mellékletek.
- `.local-cache/blog/content-mirror/records/posts/ID/` és `records/pages/ID/`: WordPress-ID alapján megőrzött nyers rekordok. A `source.json` a teljes API-rekordot, a `source.html` annak `content.rendered` mezőjét, a `source-page.html` pedig a nyilvános cikkoldal válaszát őrzi. A kiemelt kép metaadatai a `source-featured-media.json` fájlba kerülnek.
- `view-source.html`: a Markdown-nézet előállításához használt HTML. Üres API-törzsű oldalnál a felismert nyilvános tartalmat tartalmazza; az `index.jsonl` `derived_source` mezője jelzi, ha ez az oldalból származik.
- `.local-cache/blog/content-mirror/sources/api/`: az utolsó futás API-válaszai és leltárai, a lekérdezések adataival.
- `.local-cache/blog/content-mirror/state.json`: a folytatáshoz szükséges állapot, köztük a forrás- és médiafájlok lenyomatai.
- `blog/content-mirror/articles/last-report.json`: az utolsó hálózati futás összesítése és részletes hibái.

Az `index.jsonl` `path` mezője a `blog/content-mirror/articles/` alatti Markdown-fájlra mutat. A `source_record` (például `records/posts/123`) a nyers archívum logikai rekordazonosítója; nem a `blog/content-mirror/articles/` alatti fájlútvonal.

## Gyűjtemény és kizárások

Az [exclusions.json](automation/exclusions.json) tartalmazza a kézzel kizárt rekordokat. A kulcs a rekord típusa és WordPress-ID-ja, például `post:104197` vagy `page:2`; az érték a kizárás indoka. A sync és a `--rebuild` törli a kizárt rekord helyi cikkét és forrásmappáját, frissíti az indexeket, és megtartja a más cikkek által is használt médiát.

A gyűjtemény a blog tudásanyagát fedi le. Kizárjuk a navigációs, cikkjegyzék-, szolgáltatás-, eszköz- és adminisztratív oldalakat, a tesztanyagokat és a külön kiválasztott alkalmi közleményeket. Szintén kizárjuk a saját érdemi szöveg nélküli bejegyzéseket akkor is, ha a beágyazott videó vagy ábra hozzáférhető, feldolgozható tudásanyagot tartalmaz. Kizárjuk továbbá az elérhetetlenné vált érdemi tartalomra mutató bejegyzéseket, valamint a blog tudásanyagához lazán kapcsolódó, saját magyarázat nélküli egyszeri külső érdekességeket. Az új WordPress-oldalak nem záródnak ki automatikusan; a témakör-besorolás állapota és a szöveghossz önmagában nem kizárási ok.

## Frissítés és hiányzó tartalom

Minden hálózati futás teljes WordPress-leltárt kér, és a módosítási időbélyegek alapján frissít. Az új vagy módosult rekordok teljes adatait tölti le; a változatlan rekordok korábban mentett forrását használja. A leltár és a részletes lekérés közötti módosítás vagy eltűnés esetén a futás leáll, és újrafuttatást kér. Eltűntnek csak a teljes leltár után, az adott rekordra kapott külön API-404 alapján jelöl tartalmat. A lapozott leltár nem egyetlen adatbázis-pillanatkép, ezért folyamatos szerkesztés közben újrafuttatásra lehet szükség.

A normál futás pótolja a hiányzó forrást és a hiányzó Markdown-fájlt. Távoli eltűnéskor a már mentett rekord és fájljai megmaradnak `remote_missing` státusszal; a visszatérő tartalom újra `active` lesz. A letöltési hiba nem cseréli le a korábbi jó fájlt. Az átmeneti hibákat `failed` státusszal jelzi, és a következő futás újrapróbálja. A végleges HTTP-hibát, hibás URL-t vagy nem megfelelő fájlt `unavailable` állapotként rögzíti, és nem próbálja újra ugyanazt az URL-t. A Markdown ilyenkor az eredeti URL-re hivatkozik, a korábbi helyi példány pedig megmarad.

Ha az API nem tartalmaz cikkszöveget, a nyilvános HTML-ből megpróbál olvasható nézetet készíteni. Ha az sem érhető el véglegesen, a nézet hiányos marad, és csak a WordPress-tartalom vagy az URL változásakor vizsgálja újra. A védett API-tartalmat szintén hiányként jelzi. Ismert `unavailable` média vagy nyilvános HTML hiánya önmagában nem teszi hiányossá a cikket, és nem okoz ismétlődő hibajelentést.

A szinkronizálás időbélyege csak a teljes leltár feldolgozása és az állapot mentése után lép előre; az átmeneti letöltési hibák ezt nem akadályozzák. Végzetes hiba, megszakítás és mintafuttatás nem lépteti. A fájlok külön-külön atomikusan cserélődnek, de a teljes archívum nem egyetlen tranzakció; megszakítás után indítsd újra a futást.

## Markdown, hivatkozások és média

A Markdown a megőrzött forrásból újragenerálható olvasási nézet. A `kiszamolo.hu` és `www.kiszamolo.hu` címekről közvetlenül hivatkozott képeket, dokumentumokat, hang- és videófájlokat letölti a `blog/content-mirror/articles/assets/` alá; külső fájlokra és lejátszókra hivatkozást hagy, a külső hostra átirányító címeket pedig nem követi. Ismert helyi cikkek beágyazásait helyi Markdown-hivatkozássá alakítja. A `blog/content-mirror/articles/` önállóan másolható: a generált helyi hivatkozások a mappán belül maradnak.

A médiaforrások listája a `blog/content-mirror/articles/MEDIA-SOURCES.md` és `blog/content-mirror/articles/media-sources.json` fájlban található; ezeket csak a `--rebuild` frissíti, a normál letöltés nem. Hostonként az egyedi URL-eket és az érintett cikkeket számolja, nem a különálló ábrákat vagy a webszerver összes fájlját.

Cím vagy publikálási dátum változásakor új Markdown-útvonal készül, és a helyi hivatkozások célját is frissíti. A kézzel módosított régi Markdown-fájl megmarad, és a riport jelzi. Ha az átnevezés egy másik rekord korábbi fájlnevét venné át, a kézzel módosított fájlt a forrásarchívum `preserved-markdown/` mappájába menti felülírás előtt.

## Besorolási teendők

A `blog/content-mirror/articles/index.jsonl` `classification_pending` mezője a témaköri, a `content_classification_pending` mezője a tartalmi jelleg szerinti besorolásra váró cikket jelzi. A sync akkor jelöl újrabesorolást, ha változik a cím, a cikk törzse, a védett tartalom állapota vagy a kiemelt kép, illetve annak URL-je, típusa, alternatív szövege vagy képaláírása. A korábban letöltött médiafájl tartalmi változásának észlelése szintén újrabesorolást jelöl az azt használó cikkeknél. Más API-metaadat vagy a WordPress módosítási idejének változása önmagában nem indít újrabesorolást. A részletes állapotkezelést és az eredmények tárolását a [besorolás leírása](../enrichment/README.md#tárolás-és-működés) ismerteti.

## Riportok és kilépési kódok

A hálózati futás riportja a `blog/content-mirror/articles/last-report.json`; az offline újragenerálásé a `blog/content-mirror/articles/rebuild-report.json`. A futás végén összesítés és teendőlista jelenik meg. Kilépési kód: `0` befejezett, hiánytalan hálózati futás vagy sikeres offline újragenerálás; `2` befejezett hálózati futás ismert tartalmi vagy letöltési hiánnyal; `1` végzetes hiba; `130` megszakítás. A sikeres offline újragenerálás nem jelent új távoli ellenőrzést.

A párhuzamos futások külön zárral védik a nyers forrás- és a Markdown-célmappát. Így különböző forrásmappa megadásakor sem írhat két futás egyszerre ugyanabba a Markdown-célba.

## Ellenőrzés

A tesztfüggőségek telepítését és a tesztek futtatását a [projekt leírása](../README.md#tesztek) tartalmazza.
