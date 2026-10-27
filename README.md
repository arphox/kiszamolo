# Kiszámoló tudásbázis

Közösségi tudásbázist építünk a [Kiszámoló blog](https://kiszamolo.hu/) kiegészítéseként.

A blog cikkgyűjteménye és feldolgozó eszközei a [blog/](blog/README.md) alatt találhatók. A közösségi tudásanyag külön rész lesz; még nem létezik.

A [CONTRIBUTING.md](CONTRIBUTING.md) leírja, hogyan segíthetsz a projektben.

## Licenc

A projekt saját anyagai az [MIT-licenc](LICENSE) alatt használhatók, ha nincs másként jelezve.

Az MIT-licenc nem vonatkozik a kiszamolo.hu blogról származó és másoktól átvett tartalmakra. Ezek jogai az eredeti jogosultaknál maradnak.

A Kiszámoló blog cikkeinek Markdown-változatát Csernok Miklós engedélyével tesszük közzé.

## Fejlesztés és dokumentáció

- Az automata tesztek nem érhetnek el külső hálózatot és nem hívhatnak valódi AI-szolgáltatást. A külső szolgáltatásokat mockok helyettesítik; helyi HTTP-tesztkiszolgáló használható.
- Minden szabályt és magyarázatot a hozzá tartozó dokumentumban tartunk fenn; máshonnan hivatkozunk rá, az ismétlés helyett.
- A tartós döntéseket és a használathoz szükséges működést dokumentáljuk. A kódból kiolvasható apró részleteket és a lezárult fejlesztések történetét kihagyjuk.
- Nyelvezet:
  - Angol: forráskód, kódkommentek, commitüzenetek, branchnevek, `AGENTS.md`.
  - Magyar: issue-k és PR-ek címe, leírása, beszélgetései és review-k; dokumentáció, cikkek és egyéb szöveges tartalom.
  - A bevett technikai kifejezéseket megtartjuk, ha a magyarra fordításuk erőltetett lenne.

Ideiglenes ellenőrzés a GitHub-védelmek kipróbálásához.
