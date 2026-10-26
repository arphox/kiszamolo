# Közreműködés

Nem kell informatikusnak lenned ahhoz, hogy segíts. Jelezhetsz hibát vagy nehezen érthető részt, javasolhatsz jobb besorolást, hiányzó témát vagy hasznos forrást.

A GitHub az az oldal, ahol a projekt fájljait és a javaslatokat gyűjtjük. A beküldéshez elég egy böngésző és egy ingyenes GitHub-fiók. Ha még nincs fiókod, a [GitHub hivatalos útmutatója](https://docs.github.com/en/account-and-profile/how-tos/account-management/creating-an-account-on-github) segít a regisztrációban.

## A legegyszerűbb: írj egy issue-t

Az _issue_ egy bejegyzés, amelyben hibát jelezhetsz vagy javaslatot tehetsz. Ehhez nem kell fájlt szerkesztened vagy saját másolatot készítened a projektről.

1. Jelentkezz be, és az [Issues lapon](https://github.com/arphox/kiszamolo/issues) nézd meg a korábbi issue-kat. Ha már szóba került az ötleted, ott is hozzászólhatsz.
2. Ha nincs ilyen, [nyiss új issue-t](https://github.com/arphox/kiszamolo/issues/new).
3. Adj neki rövid magyar címet. Írd le, mit javasolsz és miért. Ha konkrét anyagról van szó, másold be a hivatkozását és az érintett szövegrészt. Ha van forrásod, azt is linkeld.
4. Olvasd át, majd a **Submit new issue** gombbal küldd el.

Például: „Ez a cikk a lakáshitelekről szól, de nem találom ebben a témakörben. A cikk hivatkozása: …” Nem baj, ha nem tudod a megoldást; egy pontos hibajelzés is segítség.

Képes útmutató angolul: [GitHub: Creating an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-an-issue).

<details>
<summary>AI-segítség a javaslat megfogalmazásához</summary>

Másold az alábbi szöveget egy AI-asszisztensnek, és a végén írd le a saját észrevételedet:

```text
Segíts rövid, közérthető magyar címet és leírást írni egy GitHub issue-hoz:
https://github.com/arphox/kiszamolo

Ha fontos adat hiányzik, kérdezz rá. Ne találj ki tényeket vagy forrásokat.
A megadott hivatkozásokat őrizd meg. A szöveget én ellenőrzöm és küldöm be.
Ha kérem, vezess végig a beküldésen, egyszerre egy lépéssel.
Használd a GitHub angol kifejezéseit és gombneveit, rövid magyar magyarázattal.

Az észrevételem:
[Ide írd, mit vettél észre vagy mit javasolsz.]
```

</details>

## Ha te magad javítanál egy szöveget

Kisebb javítást a böngészőből is küldhetsz, például egy elírás vagy hibás hivatkozás javítását a dokumentációban. A blogcikkek a kiszamolo.hu tartalmának másolatai, ezeket nem szerkesztjük.

1. Nyisd meg a javítandó fájlt a [projekt oldalán](https://github.com/arphox/kiszamolo), és kattints a ceruzaikonra.
2. Ha a GitHub felajánlja, kattints a **Fork this repository** gombra. Ez egy _forkot_, vagyis saját másolatot készít a GitHub-fiókodban; az eredeti projektet nem változtatja meg.
3. Javítsd a szöveget, és a **Preview** nézetben ellenőrizd az eredményt.
4. Kattints a **Commit changes…** gombra, és a **Commit message** mezőben adj rövid angol leírást a javításnak. Ezután válaszd a **Propose changes** lehetőséget.
5. A következő oldalon írd le angolul, mit és miért javítottál, majd kattints a **Create pull request** gombra. A _pull request_ (PR) a javításod elfogadására tett kérés; a saját másolat mentése önmagában még nem küldi be a javítást.

Az angol szöveghez is kérhetsz AI-segítséget. Beküldés előtt ellenőrizd, hogy azt mondja-e, amit szerettél volna.

Képes útmutatók angolul: [szerkesztés böngészőből](https://docs.github.com/en/repositories/working-with-files/managing-files/editing-files#editing-files-in-another-users-repository), [fork készítése](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/fork-a-repo?tool=webui#forking-a-repository).

<details>
<summary>AI-segítség a szövegjavítás beküldéséhez</summary>

Másold az alábbi szöveget egy AI-asszisztensnek:

```text
Egy szövegjavítást szeretnék beküldeni ide:
https://github.com/arphox/kiszamolo
Nem értek a GitHubhoz, sem a Githez. Segíts kizárólag böngészőből, telepítés és parancsok nélkül.

Olvasd el a projekt CONTRIBUTING.md útmutatóját, és kövesd a szabályait.
Kérdezd meg, melyik fájlt és hogyan javítanám, majd vezess végig
a szerkesztésen és a pull request beküldésén.
Mindig csak a következő lépést mondd, és várd meg, amíg jelzem, hol tartok.
Használd a GitHub angol kifejezéseit és gombneveit, rövid magyar magyarázattal.
Ha mást látok a képernyőn, kérdezz rá; ne találgasd, mire kell kattintanom.
Segíts megírni a beküldéshez szükséges szövegeket, és magyarázd el őket magyarul.
Beküldés előtt segíts átnézni a változtatást. Én küldöm el.
```

</details>

## Fejlesztőknek

- Nagyobb fejlesztés vagy átalakítás előtt egyeztess issue-ban.
- A saját forkodban dolgozz, és a projekt `main` ágára nyiss PR-t. Egy PR egy összetartozó változtatásról szóljon. Írd le, mit változtattál, miért, és hogyan ellenőrizted. Ha tartozik hozzá issue, hivatkozz rá.
- A helyi környezet beállítását és a tesztparancsokat a [blog/README.md](blog/README.md#telepítés-és-frissítés) tartalmazza. A tesztekhez nincs szükség Codex-bejelentkezésre vagy a cikkgyűjtemény frissítésére.
- Kódmódosításnál futtasd a [teszteket](blog/README.md#tesztek), és szükség szerint egészítsd ki őket. A megváltozott működés leírását is frissítsd. Markdown-módosításnál a szöveg és a hivatkozások ellenőrzése mellett futtasd a [formázást és annak ellenőrzését](#markdown-formázása). Böngészős javításnál a CI jelzi a formázási eltéréseket; ha elakadsz, kérj segítséget a PR-ben.
- A feldolgozás szabályait a [blog/content-mirror/README.md](blog/content-mirror/README.md), a besorolásokét a [blog/enrichment/README.md](blog/enrichment/README.md) tartalmazza. A közös fejlesztési és nyelvi szabályok, valamint a licencek a [README.md](README.md) fájlban vannak.

### Markdown formázása

A saját Markdown-fájljainkat Prettierrel formázzuk. A `proseWrap: "never"` beállítás bekezdésenként egy forrássort készít; az olvasáshoz kapcsold be az editor vizuális sortördelését (soft wrap). Minden másban az alapértelmezett stílust követjük. A generált blogcikkeket kihagyjuk. A verziót a `package.json` és a `package-lock.json` rögzíti; a közös beállítás a `.prettierrc.json`, a kizárások a `.prettierignore` fájlban vannak.

Node.js 24 LTS és npm szükséges. A projekt gyökeréből:

```powershell
npm ci
npm run format
npm run format:check
```

Az `npm ci` telepíti a rögzített függőségeket; a `format` automatikusan javítja a formázást, a `format:check` módosítás nélkül ellenőrzi. Markdown-módosítás után futtasd mindkettőt. A [formázási CI](.github/workflows/format.yml) minden PR-nél és a `main` ágra történő pushkor ugyanezt az ellenőrzést végzi.

A szerkesztőd [Prettier-integrációjában](https://prettier.io/docs/editors) bekapcsolhatod a Markdown mentéskori formázását. A projekt helyi Prettier-verzióját és konfigurációját használd, a `.prettierignore` kizárásait is figyelembe véve. Az editor beállítását minden közreműködő maga végzi; az AI közvetlen fájlmódosításai után is futtatni kell a helyi javítást és ellenőrzést.

## Ki dönt a változtatásokról?

A projektet jelenleg Ozsvárt Károly ([arphox](https://github.com/arphox)) gondozza. Ő dönt a projekt irányáról és a változtatások elfogadásáról. A PR-eket átnézés és sikeres automata ellenőrzés után fogadja el.
