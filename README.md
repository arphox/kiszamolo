# Kiszámoló tudásbázis

Tudástárat építünk a [Kiszámoló blog](https://kiszamolo.hu/) tartalmaiból. A cél, hogy a blogon összegyűlt tudás témák szerint rendezve, könnyebben megtalálható és használható formában legyen elérhető.

Jelenleg a blog cikkgyűjteménye és a hozzá készült besorolások adják az alapot; ezekre épül majd a közösségi tudásanyag. A gyűjteményt és a feldolgozó eszközöket a [blog/](blog/README.md) mappában találod.

Segíthetsz hibák jelzésével, a besorolások pontosításával vagy ötletekkel a tudástár felépítéséhez. A [CONTRIBUTING.md](CONTRIBUTING.md) megmutatja, hogyan kapcsolódhatsz be.

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
