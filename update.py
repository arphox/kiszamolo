"""Refresh the local article collection and its independent classifications."""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "automation/content-mirror"))

import sync
from classification import classify


DIMENSIONS = classify.DIMENSIONS
REPORT_PREFIXES = {"topics": "", "content_nature": "content_"}


def confirm_reclassification(count):
    """Require an explicit answer from an interactive terminal, never piped input."""
    print(f"{count} korábban besorolt cikk módosult.", flush=True)
    if not sys.stdin.isatty():
        print("Az újbóli besoroláshoz emberi jóváhagyás kell; indítsd újra interaktív terminálból.", flush=True)
        return False
    try:
        return input(f"Újra besorolod ezt a {count} módosult cikket? [igen/nem] ").strip().casefold() == "igen"
    except EOFError:
        return False


def run_update(root=ROOT, *, progress=print, confirm=None):
    root = Path(root).resolve()
    source_root = root / "raw_source/content-mirror"
    articles_root = root / "articles"
    progress("Cikkgyűjtemény frissítése…")
    sync_report = sync.run_sync(source_root, articles_root=articles_root, progress=progress)

    # Hold both locks while preparing input, calling the model, and saving progress.
    with sync.exclusive_run(source_root), sync.exclusive_run(articles_root):
        state = sync.load_state(source_root)
        vocabulary = classify.load_json(root / "classification/topics.json")
        allowed_topics = {topic["id"] for topic in vocabulary["topics"]}
        assignments = classify.validate_assignments(
            classify.load_json(root / "classification/assignments.json"), allowed_topics)
        posts = sorted((item for item in state["records"].values() if item["type"] == "post"),
                       key=lambda item: int(item["id"]))
        scheduled = {d: {"new": set(), "changed": set()} for d in DIMENSIONS}
        for item in posts:
            aid = str(item["id"])
            assignment = assignments.get(aid, {})
            for dimension in classify.pending_dimensions(item, assignment):
                kind = "changed" if dimension in assignment else "new"
                scheduled[dimension][kind].add(aid)
                item[classify.PENDING_FLAGS[dimension]] = True
        # Persist retry state before any work so interruption cannot lose missing fields.
        if any(scheduled[d]["new"] for d in DIMENSIONS):
            sync.atomic_write(source_root / "state.json", sync.json_bytes(state))

        report = dict(started_at=sync.utc_now().isoformat(),
                      new_candidates=len(scheduled["topics"]["new"]),
                      changed_candidates=len(scheduled["topics"]["changed"]),
                      classified=[], reclassified=[], review_required=[], errors=[],
                      reclassification_approved=None,
                      content_new_candidates=len(scheduled["content_nature"]["new"]),
                      content_changed_candidates=len(scheduled["content_nature"]["changed"]),
                      content_classified=[], content_reclassified=[],
                      content_review_required=[], content_errors=[],
                      incomplete_downloads=sync_report["incomplete_records"])

        def save_report():
            report["pending_article_ids"] = [item["id"] for item in posts
                if item.get("classification_pending") or "topics" not in assignments.get(str(item["id"]), {})]
            report["content_pending_article_ids"] = [item["id"] for item in posts
                if item.get("content_classification_pending") or "content_nature" not in assignments.get(str(item["id"]), {})]
            report["updated_at"] = sync.utc_now().isoformat()
            sync.atomic_write(articles_root / "update-report.json", sync.json_bytes(report))

        def process_article(item, dimensions, *, reclassify):
            if not dimensions:
                return
            aid = str(item["id"])
            title = item["title"]
            phase = "újrabesorolás" if reclassify else "besorolás"
            progress(f"Besorolás ({', '.join(dimensions)}), {phase}: {aid} · {title}")
            for dimension, outcome in classify.process_article(aid, root=root, state=state,
                    assignments=assignments, dimensions=dimensions, force=reclassify):
                prefix = REPORT_PREFIXES[dimension]
                if isinstance(outcome, Exception):
                    issue_kind = "review_required" if isinstance(outcome, classify.ReviewRequired) else "errors"
                    report[prefix + issue_kind].append(dict(id=item["id"], title=title, reason=str(outcome)))
                    progress(f"Teendő · {aid} · {title}: {outcome}")
                else:
                    report[prefix + ("reclassified" if reclassify else "classified")].append(item["id"])
                save_report()

        save_report()
        try:
            # Complete all missing dimensions before asking about bulk reclassification.
            for item in posts:
                aid = str(item["id"])
                work = [d for d in DIMENSIONS if aid in scheduled[d]["new"]]
                process_article(item, work, reclassify=False)

            changed_ids = set().union(*(scheduled[d]["changed"] for d in DIMENSIONS))
            if changed_ids:
                approved = len(changed_ids) <= 5 or (confirm or confirm_reclassification)(len(changed_ids))
                report["reclassification_approved"] = approved
                save_report()
                if approved:
                    for item in posts:
                        aid = str(item["id"])
                        work = [d for d in DIMENSIONS if aid in scheduled[d]["changed"]]
                        process_article(item, work, reclassify=True)
                else:
                    progress(f"Újbóli besorolás elhalasztva: {len(changed_ids)} cikk függőben maradt.")
        finally:
            sync.publish_article_index(articles_root, state)

        report["finished_at"] = sync.utc_now().isoformat()
        save_report()
        progress(f"Kész: {len(report['classified'])} besorolva, {len(report['reclassified'])} újra besorolva; "
                 f"{len(report['pending_article_ids'])} cikk függőben, "
                 f"{report['incomplete_downloads']} hiányos letöltés.")
        progress(f"Tartalmi besorolás: {len(report['content_classified'])} új, "
                 f"{len(report['content_reclassified'])} frissített, "
                 f"{len(report['content_pending_article_ids'])} függőben.")
        progress(f"Részletek: {articles_root / 'update-report.json'}")
        return report


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description="A helyi cikkgyűjtemény frissítése és besorolása.")
    parser.parse_args(argv)
    try:
        report = run_update(root)
        return 2 if (report["pending_article_ids"] or report["incomplete_downloads"]
                     or report["review_required"] or report["errors"]
                     or report["content_pending_article_ids"] or report["content_review_required"]
                     or report["content_errors"]) else 0
    except KeyboardInterrupt:
        print("Megszakítva; a megmaradt teendőkhöz indítsd újra a parancsot.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"A frissítés megállt: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
