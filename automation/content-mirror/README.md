# Kiszámoló tartalom-mirror

Újrafuttatható, egyirányú szinkronizálás a jelenlegi nyilvános WordPress-cikkekről és önálló oldalakról, hozzászólások nélkül. A távoli tartalom a mérvadó; a generált helyi fájlok kézi szerkesztése felülíródhat. Verziótörténetet nem készít.

## Futtatás

Python 3.10+ és a `requirements.txt` csomagjai szükségesek. A parancsokat a projekt gyökérmappájából futtasd. A telepítéshez:

```powershell
python -m pip install -r .\automation\content-mirror\requirements.txt
```

A normál szinkronizálás indítása:

```powershell
python -u -B .\automation\content-mirror\sync.py
```

Alapértelmezett olvasható cél: `articles/`, a média az `articles/assets` alá kerül. A nyers API/HTML-források és a sync állapota a `raw_source/content-mirror/` mappában vannak. Az `articles` önállóan másolható olvasási/AI-csomag: minden generált helyi cikk- és médiahivatkozás a csomagon belülre mutat. A sync folytatásához és a teljes forrásbizonyítékhoz a nyers archívum is szükséges. Ütemezést önmagában nem telepít.

Hálózatmentes újragenerálás és a régi médiaelrendezés átköltöztetése:

```powershell
python -u -B .\automation\content-mirror\sync.py --rebuild
```

Ez a meglévő állapotban rögzített cikkgyökérrel működik. Ellenőrzi a helyi forrásokat és a letöltött média lenyomatait, majd újragenerálja a Markdownokat és indexeket. A korábban a forrásgyökér `assets` mappájában lévő nyilvántartott fájlokat áthelyezi, nem másolja; megszakított költözés után újrafuttatható. Eltérő meglévő célfájlt nem ír felül. A normál sync is felismeri a régi elrendezést és átköltözteti. Az offline újragenerálás megőrzi a távoli státuszokat, a letöltési hiányokat és a sync időbélyegeit; külön `articles/rebuild-report.json` készül, a `last-report.json` továbbra is a legutóbbi hálózati sync riportja. A külső referenciák és a korábbi letöltési hiányok ettől nem válnak offline elérhetővé.

Más Markdown-cél: `--output PATH`; külön forráscél: `--source-output PATH`. A két könyvtár nem fedheti egymást. Párhuzamos letöltések száma: `--workers 1..8` (alapból 4). A média streamelve töltődik, alapból méretkorlát nélkül; opcionális `--max-asset-mib N` korlát esetén a túl nagy fájl külön hibaként jelenik meg.

Mintafutás: `--sample ID ID ... --output KÜLÖN_CIKK_KÖNYVTÁR --source-output KÜLÖN_FORRÁS_KÖNYVTÁR`. Teljes távoli leltárt kér, de csak a kiválasztott cikkeket és oldalaikat dolgozza fel. Nem állít `remote_missing` státuszt és nem lépteti a szinkronizálás időbélyegét.

## Tárolás

- `articles/YYYY/MM/YYYY-MM-DD HH.MM Cím.md`: az olvasható cikkek és oldalak. Csak Markdown-nézetet generál.
- `raw_source/content-mirror/records/posts/ID/`, `records/pages/ID/`: stabil forrásútvonalak WordPress-ID alapján.
- `source.json`: a teljes API-rekord értékei; a `source.html` az API `content.rendered` mezőjének változatlan UTF-8 tartalma.
- `source-page.html`: a nyilvános cikkoldal változatlan HTTP-választörzse. Ez a teljes oldalkeretet, így a lapban szereplő hozzászólásokat is megőrizheti; a hozzászólásokat külön nem gyűjtjük és a Markdown-nézetbe nem tesszük bele. A webhely teljes CSS/JS-környezetét nem tükrözzük.
- `source-featured-media.json`: kiemelt kép API-metaadata, ha van.
- `view-source.html`: a származtatott nézetek pontos HTML-bemenete. Üres API-törzsű oldalnál a mentett nyilvános oldal felismert tartalomkonténere; az index `derived_source` mezője jelzi az eredetet. Ismeretlen elrendezésnél az AI-nézet hiányát explicit jelzi.
- A Markdown helyi média- és belső tartalomhivatkozásokat, valamint az elején metaadatokat tartalmaz. A videók, hangok és egyéb beágyazások feliratos hivatkozásokká alakulnak, a hozzájuk tartozó szöveg és kép megmarad. Az ismert helyi cikkembedek helyi Markdown-hivatkozást kapnak.
- A kiemelések általában Markdown-jelölést kapnak; ahol az írásjelhatárok miatt ez hibásan jelenne meg, rövid HTML-jelölés őrzi meg a formázást.
- `articles/assets/`: az URL SHA-256 lenyomata alapján elnevezett média és mellékletek. Egyetlen példányuk van; a cikkek innen hivatkozzák őket, például `../../assets/HASH.docx`. A forrás-URL és a fájl tartalmi lenyomata a nyers archívum `state.json` fájljában található. Az állapot `assets_location: articles` mezője jelöli az elrendezést; az eszköz által kezelt médiaútvonalak az `articles` gyökérhez relatívak.
- `raw_source/content-mirror/sources/api/`: a legutóbbi futás eredeti API-válaszai, kérés-URL-lel, fejlécekkel, bájtmérettel és lenyomattal a manifestben. Az `inventory-posts`/`inventory-pages` a könnyű teljes leltár; a `details-posts`/`details-pages` csak a célzottan lekért teljes rekordok. A változatlan tartalmak korábban mentett teljes `source.json` rekordja megmarad. A taxonómiai válaszok az ID-t és nevet tartalmazzák.
- `articles/index.jsonl`: minden helyileg nyilvántartott tartalom, státusszal és a csomagon belüli Markdown-útvonallal (`path`). A `source_record`, például `records/posts/123`, a társított nyers archívum logikai rekordazonosítója, nem az `articles` alatt keresendő fájlútvonal. A korábbi, csomagból kilépő `source_directory` mező megszűnt. `articles/INDEX.md`: év és kategória szerinti navigáció.
- `articles/MEDIA-SOURCES.md`, `media-sources.json`: hostonként egyedi média-URL-ek és érintett tartalmak; képek és embedek külön. A `--rebuild` frissíti. Az API-törzsek és elérhető kiemelt képek mellett a feldolgozott üres API-törzsű oldalak felismert nyilvános tartalma is beleszámít. A srcset-változatok külön URL-ek, a számok nem különálló ábrák darabszámai.
- `raw_source/content-mirror/state.json`: a szinkronizálás állapota. `articles/last-report.json`: az utolsó futás számai és részletes hibái.

A fájlnév-normalizálás során a `?` és a `"` törlődik, a `:` helyére ` - ` kerül, a többi Windowsban tiltott karakter szóközzé alakul, a whitespace összevonódik, a szélső szóköz/pont/kötőjel eltűnik. Az eredeti cím a metaadatokban megmarad. Cím- vagy publikálásidátum-változáskor új útvonal keletkezik ugyanahhoz a WordPress-ID-hoz. A rá mutató helyi hivatkozások célját külön javítja; a hivatkozó cikk szövege megmarad. Ütközéskor a fájlnév ` [ID]` utótagot kap. A régi generált Markdown csak ellenőrzött tartalmi lenyomat esetén kerül törlésre; a kézzel módosított korábbi fájlt megőrzi és jelzi. A forrás és a média ilyenkor nem költözik.

Ha egy átnevezés másik rekord korábbi fájlnevét veszi át (például két cím cseréjekor), a kézzel módosított korábbi tartalmat felülírás előtt a forrásgyökér `preserved-markdown/SHA256.md` fájljába menti. A riport `reassigned_locally_modified_files_preserved` mezője összeköti az eredeti útvonalat és a megőrzött másolatot.

## Szinkronizálási szabályok

Az [exclusions.json](exclusions.json) fájlban egyes bejegyzések kizárhatók. A kulcs a letöltő rekordazonosítója (például `post:104197` vagy `page:2`), az érték a kizárás tartalmi indoka. A program induláskor betölti és ellenőrzi a fájlt; az indokok a szinkronizálási riportban is megjelennek. A sync és a `--rebuild` törli a kizárt bejegyzésekhez tartozó helyi cikket és forrásmappát, frissíti az indexeket, és megőrzi a más cikkek által is használt médiát.

A gyűjtemény a blog tudásanyagát fedi le. Az ehhez lazán kapcsolódó, saját érdemi magyarázat nélküli egyszeri külső érdekességeket kizárjuk.

A cikkgyűjteményből kizárjuk a navigációs és cikkjegyzékoldalakat, a szolgáltatás- és eszközoldalakat, az adminisztratív oldalakat, a tesztanyagot, valamint a külön kiválasztott alkalmi közleményeket és önálló érdemi szöveg nélküli cikkajánlókat. A pusztán más tartalomra hivatkozó bejegyzést is kizárjuk, ha a hivatkozott érdemi tartalom már nem elérhető. A lista nem zár ki automatikusan minden új WordPress-oldalt. A témakör-besorolás állapota vagy a szöveghossz önmagában nem kizárási feltétel.

Minden futás lekéri a teljes publikált API-leltárt az ID-val, típussal, státusszal, módosítási időkkel és a GMT publikálási idővel (`_fields=id,type,status,modified,modified_gmt,date_gmt`). Ezeket a cikknyilvántartással veti össze. Az új és módosult cikkekhez teljes API-rekordot kér, majd feldolgozza őket. A változatlan cikkek adatait a nyilvántartásból használja. Eltűnést az adott elemre kapott külön API-404 után jelöl.

A normál futás a helyi fájlok meglétét ellenőrzi. Hiányzó forrást letölt, hiányzó Markdown-fájlt a mentett forrásból állít elő. A fájlok tartalmi épségét a `--rebuild` ellenőrzi.

Ha a leltár és a célzott rekordlekérés között változik a módosítási időbélyeg vagy eltűnik egy kért rekord, a futás leáll és újrafuttatást kér.

A nyilvános HTML és a kapcsolódó média újraellenőrzése az új, a WordPress szerint módosult vagy visszatért tartalmakra, valamint a hiányzó vagy korábban hibás cikkforrásokra terjed ki. Az átmeneti letöltési hibákat (például időtúllépést, kapcsolatmegszakadást vagy ideiglenes szerverhibát) a következő futás újrapróbálja. A végleg elérhetetlen vagy hibás fájl `unavailable` státuszt kap; többé nem próbálkozik vele újra. Ismert `unavailable` média vagy nyilvános HTML hiánya önmagában nem teszi hiányossá a cikket. Ha az API sem tartalmaz cikk szöveget, és a nyilvános HTML sem érhető el, a cikk olvasható nézete hiányos marad; a letöltő ezt csak a WordPress-tartalom vagy az URL változásakor vizsgálja újra. A média eredeti URL-je marad a Markdownban. Ha korábbi helyi másolat volt, a fájl megmarad, de a hivatkozás visszaáll az eredeti címre. A szerver ETag/Last-Modified támogatása esetén feltételes GET-et használ.

A cikknyilvántartás `classification_pending` mezője jelzi a témakörök szerinti besorolásra váró új vagy módosult cikkeket. A mező az `index.jsonl` fájlban is szerepel. A gyökérmappából indítható `python -u -B update.py` a letöltés után elvégzi a besorolást, és siker esetén törli a jelzést. A kihagyott és sikertelen besorolások függőben maradnak a következő futásra.

Újrabesorolást a cím, a cikk HTML-törzse, a védett tartalom állapota, a kiemelt kép cseréje vagy a megjelenített képadatok (kép URL-je, típusa, alternatív szövege, képaláírása) indítanak. A Yoast-adatok, a WordPress módosítási idő és más, a cikk besorolását nem érintő API-mezők változása önmagában nem. A teljes API-forrás ettől még változatlanul megmarad.

A saját `kiszamolo.hu` és `www.kiszamolo.hu` hostról közvetlenül hivatkozott médiafájlokat tölti le, beleértve a képméret-változatokat, kiemelt eredeti képet, dokumentumokat és közvetlen hang-/videófájlokat. Külső hostra mutató átirányítást sem követ. Külső fájlok `external_reference` státusszal és eredeti URL-lel maradnak meg. Külső lejátszók mögötti videót nem archivál; az ismert saját cikkek WordPress-embedje helyi Markdown-hivatkozást kap. A médiahost-statisztika a törzsben felismert hivatkozásokból és elérhető kiemelt képekből származik, nem a teljes webszerver fájlleltára.

Távoli eltűnéskor a rekord és minden helyi fájl megmarad, státusza `remote_missing`, első észlelési idővel. Ez csak teljes, validált leltár és az adott elemre adott külön nyilvános API-404 után állítható be. Élő elemre utaló egyedi válasz esetén a futás inkonzisztens leltárral leáll. Visszatéréskor ismét `active`. A lapozott API nem atomikus adatbázis-pillanatkép; folyamatos szerkesztés közben újrafuttatásra lehet szükség.

Sikertelen letöltés nem cseréli le a korábbi jó fájlt. Az átmeneti hibát `failed` státusszal jelzi; ezek a következő futáskor új célpontok. A végleges HTTP-hibát, a hibás URL-t és a nem megfelelő fájlt `unavailable` státusszal jegyzi meg, a hibával és HTTP-kóddal, ha van. Ezek az ismert hiányok nem teszik hiányossá a cikket, és nem okoznak ismétlődő hibajelentést. A Markdown az eredeti média-URL-t őrzi meg. Külső hivatkozás a megállapodott scope részeként megőrzött referencia, nem letöltési hiba. Védett API-tartalom továbbra is explicit hiányként jelenik meg.

A `last_completed_sync_at` csak akkor lép előre, ha a teljes leltár feldolgozása és az állapot mentése befejeződött; az átmeneti letöltési hibák mellett is. Ezek a következő futásban ismét célpontok. Az `unavailable` URL-eket később sem próbálja újra; új URL új letöltési célpont. Fatal leltárhiba, megszakítás vagy mintafutás nem lépteti. Az egyes fájlok atomikusan cserélődnek, a teljes többfájlos archívum nem atomikus tranzakció; megszakítás után újrafuttatás szükséges. Párhuzamos futás ellen operációs rendszer által tartott zár véd.

Az API-forrás és a megőrzött weboldal az elsődleges bizonyíték; a Markdown újragenerálható nézet. A konverzió nem karakterpontos: a whitespace, elválasztóvonalak és hangsúlyjelölések reprezentációja változhat. A Markdown-nézet nem tartalmaz HTML-kommenteket, script- és style-tartalmat; ezek a forrásban megmaradnak. A forráshűség a jelenleg elérhető nyilvános tartalom megőrzésére vonatkozik, nem eredeti adatbázisra, korábbi verziókra, teljes webhelykinézetre vagy a dinamikus kalkulátorok működésére.

A felső index `^(...)`, az alsó index `\_(...)` alakban marad egyértelmű; a lábjegyzetjelek megmaradnak. A hivatkozások céljában a szóközök és a Markdownban különleges karakterek URL-kódolást kapnak. A hivatkozásokhoz szükséges HTML-azonosítókat megőrzi. A táblázatok összevont celláinak értékét a megfelelő sorokban és oszlopokban megismételjük; a színt hordozó üres cellák `[szín: #hex]` jelölést kapnak. A nem biztonságosan átalakítható táblázatok HTML-ként maradnak meg.

Kilépési kód: `0` befejezett, hiánytalan saját-hostos letöltés vagy sikeres offline újragenerálás; `2` befejezett hálózati sync explicit tartalmi/letöltési hiányokkal; `1` fatális hiba; `130` megszakítás. A sikeres `--rebuild` nem jelent új távoli ellenőrzést vagy hiánytalan tartalmat.

A párhuzamos írás ellen a forrás- és a Markdown-gyökeret külön operációsrendszer-zár védi, így eltérő forrásgyökérrel sem írható egyszerre ugyanaz a Markdown-cél. A riport `full_records_fetched` és `cached_records_reused` mezői jelzik a teljes rekordlekérések és az újrahasznált források számát; az `api_response_count` és `api_response_bytes` a megőrzött API-válaszok darabszáma és összes választestmérete (nem a tömörített hálózati forgalom).

## Ellenőrzés

```powershell
python -m pip install -r .\automation\content-mirror\requirements-test.txt
python -B -m unittest discover -s .\automation\content-mirror -p "test_*.py" -v
```

A tesztek hálózatfüggetlenek, kivéve egy helyi HTTP-tesztszervert. Ellenőrzik a lapozási hibákat, hosthatárt, ékezetes URL-eket, atomikus letöltést, cache-sérülést, a friss, változatlan cikkek újraletöltésének elmaradását, a besorolási teendők megőrzését, távoli eltűnést/visszatérést, részleges/fatális futást, helyi hivatkozásokat, táblázatokat és kiemelt képet. A teszt-tempkönyvtár a TEMP/TMP/TMPDIR változókkal kijelölhető.
