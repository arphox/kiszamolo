"""Search saved article assignments and broader topics offline."""

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NATURES = {
    "explanation": "Magyarázat",
    "advice": "Tanács",
    "news": "Hír vagy közlemény",
    "opinion": "Vélemény",
}
STATUSES = ("classified", "corrected", "insufficient_content", "input_too_long", "error", "stale")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("A dátum ÉÉÉÉ-HH-NN alakú legyen.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Érvénytelen dátum: {value}") from error


class Catalog:
    def __init__(self, tags, rows, root=ROOT, scope="all"):
        definitions = tags["tags"]
        self.tags = {tag["id"]: tag for tag in definitions}
        require(len(self.tags) == len(definitions), "Ismétlődő tagazonosító.")
        self.rows = rows
        self.root = Path(root)
        self.scope = scope
        self.aliases = {}
        self.children = {tag_id: set() for tag_id in self.tags}
        for tag in definitions:
            for alias in [tag["id"], tag["name"], *tag.get("synonyms", [])]:
                key = normalize(alias)
                require(key, "Üres tagnév vagy szinonima.")
                require(key not in self.aliases or self.aliases[key] == tag["id"],
                        f"Többértelmű tagnév vagy szinonima: {alias}")
                self.aliases[key] = tag["id"]
            broader = tag.get("broader", [])
            require(isinstance(broader, list) and len(broader) == len(set(broader)),
                    "Hibás vagy ismételt szülőtag.")
            for parent in broader:
                require(parent in self.tags, "Ismeretlen fogalom a tagkapcsolatban.")
                require(parent != tag["id"], "Önmagára mutató fogalomkapcsolat.")
                self.children[parent].add(tag["id"])
        self.expanded = {}

        def expand(tag_id, visiting):
            require(tag_id not in visiting, "Kör a fogalomkapcsolatokban.")
            if tag_id not in self.expanded:
                descendants = {tag_id}
                for child in self.children[tag_id]:
                    descendants.update(expand(child, visiting | {tag_id}))
                self.expanded[tag_id] = descendants
            return self.expanded[tag_id]

        for tag_id in self.tags:
            expand(tag_id, set())
        require(len({row["id"] for row in rows}) == len(rows), "Ismétlődő cikkazonosító.")
        for row in rows:
            row.setdefault("evaluated_tag_ids", list(self.tags))
            parse_date(row["published_at"][:10])
            require(set(row["tags"]) <= self.tags.keys(), "Ismeretlen tag a cikkben.")
            require(set(row["nature"]) <= NATURES.keys(), "Ismeretlen cikkjelleg.")
            require(row["status"] in STATUSES, "Ismeretlen cikkállapot.")
            require(row["status"] not in ("insufficient_content", "input_too_long", "error", "stale")
                    or not (row["tags"] or row["nature"]),
                    "Hibás vagy hiányos cikkhez nem tartozhat besorolás.")

    @classmethod
    def load(cls, results_path=None, overrides_path=None):
        tags_path = HERE / "tags.json"
        tags_bytes = tags_path.read_bytes()
        tags = json.loads(tags_bytes)
        tags_sha256 = hashlib.sha256(tags_bytes).hexdigest()
        if results_path is None:
            results_path = HERE / "runs" / "pilot.json"
        document = json.loads(Path(results_path).read_text(encoding="utf-8"))
        require(document.get("schema_version") == 1, "Ismeretlen címkézési fájlformátum.")
        current_vocabulary = {tag["id"]: tag for tag in tags["tags"]}
        saved_vocabulary = document.get("vocabulary_snapshot")
        if saved_vocabulary is None:
            require(document.get("tags_sha256") == tags_sha256,
                    "A mentett címkézés régi fogalomtárhoz tartozik; újrafeldolgozás szükséges.")
        else:
            require(isinstance(saved_vocabulary, dict)
                    and set(saved_vocabulary) <= set(current_vocabulary)
                    and all(saved_vocabulary[key] == current_vocabulary[key] for key in saved_vocabulary),
                    "A mentett címkézés módosított vagy eltávolított tagot tartalmaz; újrafeldolgozás szükséges.")
        rows = document["articles"]
        from label import settings

        current = settings(tags, tags_sha256)
        current_settings = current["settings_sha256"]
        for row in rows:
            path = (ROOT / row["path"]).resolve()
            require(path.is_relative_to((ROOT / "articles").resolve()),
                    "A cikk útvonala kilép az archívumból.")
            source_changed = False
            if row["status"] in ("classified", "insufficient_content", "corrected"):
                try:
                    source_changed = hashlib.sha256(path.read_bytes()).hexdigest() != row["markdown_sha256"]
                except OSError:
                    source_changed = True
            if row.get("labeling_sha256") is None:
                compatible_settings = row.get("settings_sha256") == current_settings
                if compatible_settings:
                    row["labeling_sha256"] = current["labeling_sha256"]
                    row["evaluated_tag_ids"] = sorted(current_vocabulary)
            else:
                compatible_settings = row["labeling_sha256"] == current["labeling_sha256"]
            if (row["status"] == "stale" or source_changed or not compatible_settings):
                row.update(status="stale", tags=[], nature=[])
        if overrides_path is not None:
            overrides = json.loads(Path(overrides_path).read_text(encoding="utf-8"))
            require(overrides["tags_sha256"] == tags_sha256,
                    "A kézi javítás más fogalomtárhoz tartozik.")
            by_id = {row["id"]: row for row in rows}
            corrected = set()
            for correction in overrides["articles"]:
                article_id = correction["id"]
                require(article_id in by_id and article_id not in corrected,
                        "Ismeretlen vagy ismételt kézi javítás.")
                row = by_id[article_id]
                require(row["status"] not in ("stale", "error", "input_too_long", "insufficient_content"),
                        "Kézi besorolás előtt a hibás vagy hiányos bemenetet rendezni kell.")
                require(correction["markdown_sha256"] == row["markdown_sha256"],
                        "A kézi javítás más cikkszöveghez tartozik.")
                require(isinstance(correction["reason"], str) and correction["reason"].strip(),
                        "A kézi javításhoz indok kell.")
                for field in ("tags", "nature"):
                    value = correction[field]
                    require(isinstance(value, list) and all(isinstance(item, str) for item in value)
                            and len(value) == len(set(value)), "Hibás kézi tag- vagy jelleglista.")
                    row[field] = value
                row["evaluated_tag_ids"] = sorted(set(row.get("evaluated_tag_ids", [])) | set(correction["tags"]))
                row.update(status="corrected", correction_reason=correction["reason"])
                corrected.add(article_id)
        return cls(tags, rows, root=ROOT, scope=document.get("scope", "all"))

    def resolve(self, value):
        try:
            return self.aliases[normalize(value)]
        except KeyError as error:
            raise ValueError(f"Ismeretlen fogalom: {value!r}. A neveket és szinonimákat a tagging/tags.json sorolja fel.") from error

    def search(self, tags=(), nature=(), since=None, until=None, status=None):
        tag_ids = list(dict.fromkeys(self.resolve(value) for value in tags))
        nature_aliases = {normalize(value): key for key, name in NATURES.items() for value in (key, name)}
        nature_ids = []
        for value in nature:
            require(normalize(value) in nature_aliases, f"Ismeretlen cikkjelleg: {value!r}.")
            key = nature_aliases[normalize(value)]
            if key not in nature_ids:
                nature_ids.append(key)
        start = parse_date(since) if since is not None else None
        end = parse_date(until) if until is not None else None
        require(not (start and end and start > end), "A kezdődátum későbbi a záródátumnál.")
        require(status is None or status in STATUSES, "Ismeretlen cikkállapot.")

        def tag_view(tag_id):
            return {"id": tag_id, "name": self.tags[tag_id]["name"]}

        results = []
        unevaluated = []
        for row in sorted(self.rows, key=lambda r: (r["published_at"], r["id"])):
            day = parse_date(row["published_at"][:10])
            if start and day < start or end and day > end:
                continue
            if status is not None and row["status"] != status:
                continue
            if not set(nature_ids) <= set(row["nature"]):
                continue
            if row["status"] in ("classified", "corrected"):
                requested_coverage = set().union(*(self.expanded[tag_id] for tag_id in tag_ids)) if tag_ids else set(self.tags)
                missing_coverage = requested_coverage - set(row.get("evaluated_tag_ids", self.tags))
                if missing_coverage:
                    unevaluated.append(row["id"])
            if not all(self.expanded[tag_id].intersection(row["tags"]) for tag_id in tag_ids):
                continue
            results.append({
                "id": row["id"], "title": row["title"], "published_at": row["published_at"],
                "tags": [tag_view(tag_id) for tag_id in row["tags"]],
                "nature": [{"id": key, "name": NATURES[key]} for key in row["nature"]],
                "status": row["status"], "path": row["path"],
                "local_path": str((self.root / row["path"]).resolve()),
            })
        return {
            "scope": self.scope, "total_articles": len(self.rows),
            "unclassified_ids": [r["id"] for r in self.rows if r["status"] in ("error", "input_too_long", "stale")],
            "insufficient_content_ids": [r["id"] for r in self.rows if r["status"] == "insufficient_content"],
            "unevaluated_ids": unevaluated,
            "query": {
                "tag_operator": "AND", "nature_operator": "AND",
                "tags": [{**tag_view(key), "includes": [tag_view(child) for child in sorted(self.expanded[key])]} for key in tag_ids],
                "nature": [{"id": key, "name": NATURES[key]} for key in nature_ids],
                "since": since, "until": until, "status": status,
            },
            "count": len(results), "results": results,
        }


def main():
    parser = argparse.ArgumentParser(description="Keresés a mentett besorolásokban. A tagek és szűrők kapcsolata ÉS.")
    parser.add_argument("tags", nargs="*", help="Magyar tagnevek, szinonimák vagy technikai azonosítók; több szavas név idézőjelben.")
    parser.add_argument("--nature", action="append", default=[], help="Cikkjelleg magyar neve vagy azonosítója; ismételhető, ÉS kapcsolat.")
    parser.add_argument("--from", dest="since", help="Kezdőnap, ÉÉÉÉ-HH-NN, beleértve.")
    parser.add_argument("--until", help="Zárónap, ÉÉÉÉ-HH-NN, beleértve.")
    parser.add_argument("--status", choices=STATUSES, help="Besorolási vagy hibaállapot szűrése.")
    parser.add_argument("--results", help="Címkézési eredményfájl; alapból a tagging/runs/pilot.json.")
    parser.add_argument("--overrides", help="Külön mentett kézi javítások JSON-fájlja.")
    parser.add_argument("--json", action="store_true", help="Géppel olvasható eredmény, minden találattal.")
    args = parser.parse_args()
    try:
        result = Catalog.load(args.results, args.overrides).search(args.tags, args.nature, args.since, args.until, args.status)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"Hiba: {error}\n")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"{result['count']} találat a(z) {result['total_articles']} mentett cikkből. A tagek és szűrők kapcsolata: ÉS.")
    if result["insufficient_content_ids"]:
        print("Hiányos szöveg, témára nem kereshető: " + ", ".join(map(str, result["insufficient_content_ids"])))
    if result["unclassified_ids"]:
        print("Hibás vagy elavult, témára nem kereshető: " + ", ".join(map(str, result["unclassified_ids"])))
    if result["unevaluated_ids"]:
        print(f"A taglista egy részére még nem vizsgált cikk a szűrésben: {len(result['unevaluated_ids'])} (a JSON tartalmazza az azonosítókat).")
    for row in result["results"]:
        print(f"\n{row['published_at'][:10]} | {row['id']} | {row['title']}")
        print("Tagek: " + ("; ".join(t["name"] for t in row["tags"]) or "nincs"))
        print("Jelleg: " + ("; ".join(n["name"] for n in row["nature"]) or "nem állapítható meg"))
        print("Állapot: " + row["status"])
        print(row["local_path"])


if __name__ == "__main__":
    main()
